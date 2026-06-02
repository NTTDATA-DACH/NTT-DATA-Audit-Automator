# src/clients/ai_client.py
import logging
import json
import asyncio
import datetime
from typing import List, Dict, Any, Optional

from google import genai
from google.genai import types
from google.genai import errors as genai_errors
from jsonschema import validate, ValidationError

from src.config import AppConfig
from src.constants import GROUND_TRUTH_MODEL, PROMPT_CONFIG_PATH

MAX_RETRIES = 5


class AiClient:
    """A client for all Vertex AI model interactions, using the google-genai SDK."""

    def __init__(self, config: AppConfig):
        self.config = config

        with open(PROMPT_CONFIG_PATH, 'r', encoding='utf-8') as f:
            prompt_config = json.load(f)

        base_system_message = prompt_config.get("system_message", "")
        if not base_system_message:
            logging.warning("System message is empty. AI calls will not have a predefined persona.")

        # Append the current date to the system prompt
        current_date = datetime.date.today().strftime("%Y-%m-%d")
        self.system_message = f"{base_system_message}\n\nImportant: Today's date is {current_date}."

        # Vertex AI location. "global" is intentional here for broad Gemini model
        # availability; config.region still drives other GCP resources (GCS, Document AI).
        vertex_location = "global"
        self.client = genai.Client(
            vertexai=True,
            project=config.gcp_project_id,
            location=vertex_location,
        )

        self.semaphore = asyncio.Semaphore(config.max_concurrent_ai_requests)

        logging.info(f"Vertex AI Client instantiated for project '{config.gcp_project_id}' (Vertex AI location '{vertex_location}').")
        logging.info(f"System Message Context includes today's date: {current_date}")

    def _build_generation_config(self, json_schema: Dict[str, Any]) -> types.GenerateContentConfig:
        """Build the GenerateContentConfig, enforcing JSON output against the given schema."""
        try:
            schema_for_api = json.loads(json.dumps(json_schema))
            schema_for_api.pop("$schema", None)
        except Exception as e:
            logging.error(f"Failed to process JSON schema before API call: {e}")
            raise ValueError("Invalid JSON schema provided.") from e

        return types.GenerateContentConfig(
            system_instruction=self.system_message,
            response_mime_type="application/json",
            response_schema=schema_for_api,
            max_output_tokens=65535,
            temperature=0.2,
        )

    def _build_contents(self, prompt: str, gcs_uris: List[str] = None) -> List[Any]:
        """Assemble the request contents from the prompt and any GCS PDF URIs."""
        contents: List[Any] = [prompt]
        if gcs_uris:
            for uri in gcs_uris:
                contents.append(types.Part.from_uri(file_uri=uri, mime_type="application/pdf"))
        return contents

    @staticmethod
    def _extract_json(response: Any) -> Dict[str, Any]:
        """Validate the response shape and parse its text payload as JSON."""
        if not response.candidates:
            raise ValueError("The model response contained no candidates.")

        finish_reason = response.candidates[0].finish_reason.name
        if finish_reason not in ["STOP", "MAX_TOKENS"]:
            raise ValueError(f"Model finished with non-OK reason: '{finish_reason}'")

        try:
            return json.loads(response.text)
        except json.JSONDecodeError as e:
            # Clean JSON error without the full traceback
            raise ValueError(f"Failed to parse model response as JSON: {str(e).split(':')[0]}")

    async def generate_json_response_single_attempt(
        self,
        prompt: str,
        json_schema: Dict[str, Any],
        gcs_uris: List[str] = None,
        request_context_log: str = "Generic AI Request",
        model_override: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Single attempt JSON generation - used for model fallback scenarios.
        Fails fast on JSON errors rather than retrying 5 times.
        """
        gen_config = self._build_generation_config(json_schema)
        model_to_use = model_override if model_override else GROUND_TRUTH_MODEL
        contents = self._build_contents(prompt, gcs_uris)

        logging.info(f"[{request_context_log}] Single attempt with model '{model_to_use}'...")
        response = await self.client.aio.models.generate_content(
            model=model_to_use,
            contents=contents,
            config=gen_config,
        )

        response_json = self._extract_json(response)
        logging.info(f"[{request_context_log}] Successfully generated JSON response.")
        return response_json

    async def generate_json_response(
        self,
        prompt: str,
        json_schema: Dict[str, Any],
        gcs_uris: List[str] = None,
        request_context_log: str = "Generic AI Request",
        model_override: Optional[str] = None,
        max_retries: int = None
    ) -> Dict[str, Any]:
        """
        Generates a JSON response from the AI model, enforcing a specific schema and
        optionally providing GCS files as context. Implements an async retry loop
        with exponential backoff and connection limiting.

        Args:
            prompt: The text prompt for the model.
            json_schema: The JSON schema to enforce on the model's output.
            gcs_uris: A list of 'gs://...' URIs pointing to PDF files for context.
            request_context_log: A string to identify the request source in logs.
            model_override: Optional model name to use instead of the default.
            max_retries: Optional override for the number of retries (defaults to MAX_RETRIES).

        Returns:
            The parsed JSON response from the model.
        """
        retries = max_retries if max_retries is not None else MAX_RETRIES
        gen_config = self._build_generation_config(json_schema)

        # Select the appropriate model
        model_to_use = model_override if model_override else GROUND_TRUTH_MODEL

        # Build the content list. The system message is handled via the generation config.
        contents = self._build_contents(prompt, gcs_uris)
        if gcs_uris and self.config.is_test_mode:
            logging.info(f"Attaching {len(gcs_uris)} GCS files to the prompt.")

        async with self.semaphore:
            for attempt in range(retries):
                try:
                    logging.info(f"[{request_context_log}] Attempt {attempt + 1}/{retries}: Calling Gemini model '{model_to_use}'...")
                    response = await self.client.aio.models.generate_content(
                        model=model_to_use,
                        contents=contents,
                        config=gen_config,
                    )

                    response_json = self._extract_json(response)
                    logging.info(f"[{request_context_log}] Successfully generated and parsed JSON response on attempt {attempt + 1}.")
                    return response_json

                except (genai_errors.APIError, ValueError, asyncio.TimeoutError) as e:
                    wait_time = 2 ** attempt
                    if attempt == retries - 1:
                        logging.critical(f"[{request_context_log}] AI generation failed after all {retries} retries.", exc_info=True)
                        raise

                    if isinstance(e, genai_errors.APIError):
                        logging.warning(f"[{request_context_log}] Generation attempt {attempt + 1} failed with Google API Error (Code: {e.code}): {e.message}. Retrying in {wait_time}s...")
                    else:
                        # Clean up JSON error messages to be more readable
                        error_msg = str(e)
                        if "Unterminated string" in error_msg or "json.decoder.JSONDecodeError" in error_msg:
                            logging.warning(f"[{request_context_log}] Attempt {attempt + 1} failed: JSON parsing error. Retrying in {wait_time}s...")
                        else:
                            logging.warning(f"[{request_context_log}] Attempt {attempt + 1} failed: {error_msg}. Retrying in {wait_time}s...")

                    await asyncio.sleep(wait_time)

        raise RuntimeError("AI generation failed unexpectedly after exhausting all retries.")

    async def generate_validated_json_response(
        self,
        prompt: str,
        json_schema: Dict[str, Any],
        gcs_uris: List[str] = None,
        request_context_log: str = "Generic AI Request",
        model_override: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generates and validates a JSON response from the AI model.

        Raises:
            ValidationError: If the response doesn't match the provided schema

        Returns:
            The validated JSON response from the model
        """
        try:
            result = await self.generate_json_response(prompt, json_schema, gcs_uris, request_context_log, model_override)
            validate(instance=result, schema=json_schema)
            return result
        except ValidationError as e:
            # Clean validation error message
            clean_msg = e.message.split('\n')[0] if '\n' in e.message else e.message
            logging.error(f"[{request_context_log}] Schema validation failed: {clean_msg}")
            raise ValidationError(f"Response validation failed: {clean_msg}")
