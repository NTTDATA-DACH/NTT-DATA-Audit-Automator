# src/clients/ai_client.py
import logging
import json
import asyncio
import time
from typing import List, Dict, Any

from google.cloud import aiplatform
from google.api_core import exceptions as api_core_exceptions
from vertexai.generative_models import GenerativeModel, GenerationConfig, Part

from src.config import AppConfig

GENERATIVE_MODEL_NAME = "gemini-2.5-pro"
MAX_RETRIES = 5


class AiClient:
    """A client for all Vertex AI model interactions, using the aiplatform SDK."""

    def __init__(self, config: AppConfig):
        self.config = config
        aiplatform.init(project=config.gcp_project_id, location=config.vertex_ai_region)
        self.generative_model = GenerativeModel(GENERATIVE_MODEL_NAME)
        self.semaphore = asyncio.Semaphore(config.max_concurrent_ai_requests)
        logging.info(f"Vertex AI Client instantiated for project '{config.gcp_project_id}' in region '{config.vertex_ai_region}'.")

    async def generate_json_response(self, prompt: str, json_schema: Dict[str, Any], gcs_uris: List[str] = None) -> Dict[str, Any]:
        """
        Generates a JSON response from the AI model, enforcing a specific schema and
        optionally providing GCS files as context. Implements an async retry loop
        with exponential backoff and connection limiting.

        Args:
            prompt: The text prompt for the model.
            json_schema: The JSON schema to enforce on the model's output.
            gcs_uris: A list of 'gs://...' URIs pointing to PDF files for context.

        Returns:
            The parsed JSON response from the model.
        """
        try:
            schema_for_api = json.loads(json.dumps(json_schema))
            schema_for_api.pop("$schema", None)
        except Exception as e:
            logging.error(f"Failed to process JSON schema before API call: {e}")
            raise ValueError("Invalid JSON schema provided.") from e

        gen_config = GenerationConfig(
            response_mime_type="application/json",
            response_schema=schema_for_api,
            max_output_tokens=65536,
            temperature=0.2,
        )

        # Build the content list for the API call
        contents = [prompt]
        if gcs_uris:
            for uri in gcs_uris:
                # Assuming all provided documents are PDFs
                contents.append(Part.from_uri(uri, mime_type="application/pdf"))
            if self.config.is_test_mode:
                logging.info(f"Attaching {len(gcs_uris)} GCS files to the prompt.")

        async with self.semaphore:
            for attempt in range(MAX_RETRIES):
                try:
                    if self.config.is_test_mode:
                        logging.info(f"Attempt {attempt + 1}/{MAX_RETRIES}: Calling Gemini model '{GENERATIVE_MODEL_NAME}'...")
                    response = await self.generative_model.generate_content_async(
                        contents=contents,
                        generation_config=gen_config,
                    )

                    if not response.candidates:
                        raise ValueError("The model response contained no candidates.")

                    finish_reason = response.candidates[0].finish_reason.name
                    if finish_reason not in ["STOP", "MAX_TOKENS"]:
                        # Raise an exception to be caught by the generic handler below,
                        # which will trigger the retry-with-backoff logic.
                        raise ValueError(f"Model finished with non-OK reason: '{finish_reason}'")

                    response_json = json.loads(response.text)
                    if self.config.is_test_mode:
                        logging.info(f"Successfully generated and parsed JSON response on attempt {attempt + 1}.")
                    return response_json

                except (api_core_exceptions.GoogleAPICallError, Exception) as e:
                    wait_time = 2 ** attempt
                    # If this was the last attempt, log critical error and re-raise the exception.
                    if attempt == MAX_RETRIES - 1:
                        logging.critical(f"AI generation failed after all {MAX_RETRIES} retries.", exc_info=True)
                        raise # This is now inside the except block and will correctly re-raise 'e'.

                    # Log the appropriate warning for the current attempt.
                    if isinstance(e, api_core_exceptions.GoogleAPICallError):
                        logging.warning(f"Generation attempt {attempt + 1} failed with Google API Error (Code: {e.code}). Retrying in {wait_time}s...")
                    else:
                        logging.warning(f"Generation attempt {attempt + 1} failed with an exception: {e}. Retrying in {wait_time}s...")

                    await asyncio.sleep(wait_time)

        # This line should not be reachable if the loop logic is correct, but serves as a fallback.
        raise RuntimeError("AI generation failed unexpectedly after exhausting all retries.")