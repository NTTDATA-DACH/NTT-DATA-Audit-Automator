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
from src.constants import (
    CHECKER_MODEL,
    ENABLE_MAKER_CHECKER,
    GROUND_TRUTH_MODEL,
    PROMPT_CONFIG_PATH,
    THINKING_LEVEL,
)

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

        # Maker/checker: the checker prompt is generic, so one template serves every task.
        self.checker_prompt_template = prompt_config.get("checker", {}).get("prompt", "")
        self.checker_enabled = ENABLE_MAKER_CHECKER and bool(self.checker_prompt_template)
        if ENABLE_MAKER_CHECKER and not self.checker_prompt_template:
            logging.error(
                "ENABLE_MAKER_CHECKER is set but prompt_config.json has no 'checker.prompt'. "
                "Running single-pass — answers are NOT being verified."
            )
        # Protocol of every checker verdict; the controller persists it per stage.
        self.checker_log: List[Dict[str, Any]] = []

        logging.info(f"Vertex AI Client instantiated for project '{config.gcp_project_id}' (Vertex AI location '{vertex_location}').")
        logging.info(f"System Message Context includes today's date: {current_date}")
        logging.info(
            f"Maker/checker is {'enabled' if self.checker_enabled else 'disabled'}"
            + (f" (checker model '{CHECKER_MODEL}')." if self.checker_enabled else ".")
        )

    @staticmethod
    def _resolve_thinking_level(model: str) -> str:
        """Thinking level for a model; the pro tier has no 'minimal' and clamps to 'low'."""
        level = (THINKING_LEVEL or "").strip().lower()
        if level == "minimal" and "pro" in model:
            return "low"
        return level

    def _build_generation_config(self, json_schema: Dict[str, Any], model: str) -> types.GenerateContentConfig:
        """Build the GenerateContentConfig, enforcing JSON output against the given schema."""
        try:
            schema_for_api = json.loads(json.dumps(json_schema))
            schema_for_api.pop("$schema", None)
        except Exception as e:
            logging.error(f"Failed to process JSON schema before API call: {e}")
            raise ValueError("Invalid JSON schema provided.") from e

        config_fields = types.GenerateContentConfig.model_fields
        kwargs: Dict[str, Any] = {
            "system_instruction": self.system_message,
            "response_mime_type": "application/json",
            "max_output_tokens": 65535,
            # Gemini 3.x is tuned for temperature 1; lowering it degrades reasoning quality.
            "temperature": 1,
        }

        # The assets are real JSON Schemas, so hand them to the field that speaks that
        # dialect; response_schema (OpenAPI subset) is the fallback for older SDKs.
        if "response_json_schema" in config_fields:
            kwargs["response_json_schema"] = schema_for_api
        else:
            kwargs["response_schema"] = schema_for_api

        thinking_level = self._resolve_thinking_level(model)
        if thinking_level and "thinking_config" in config_fields:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=thinking_level)

        return types.GenerateContentConfig(**kwargs)

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
        model_to_use = model_override if model_override else GROUND_TRUTH_MODEL
        gen_config = self._build_generation_config(json_schema, model_to_use)
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

        # Select the appropriate model; it also decides the thinking level.
        model_to_use = model_override if model_override else GROUND_TRUTH_MODEL
        gen_config = self._build_generation_config(json_schema, model_to_use)

        # Build the content list. The system message is handled via the generation config.
        contents = self._build_contents(prompt, gcs_uris)
        if gcs_uris and self.config.is_test_mode:
            logging.info(f"Attaching {len(gcs_uris)} GCS files to the prompt.")

        for attempt in range(retries):
            try:
                logging.info(f"[{request_context_log}] Attempt {attempt + 1}/{retries}: Calling Gemini model '{model_to_use}'...")
                # The semaphore guards the in-flight call only: holding it across the
                # backoff sleep would idle a concurrency slot for up to 15 seconds.
                async with self.semaphore:
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

    @staticmethod
    def _build_checker_schema(json_schema: Dict[str, Any]) -> Dict[str, Any]:
        """Wraps a task schema into the checker's verdict schema.

        The correction is nullable via `anyOf` rather than a nullable type, because that
        is the form Vertex accepts for a composed sub-schema.
        """
        task_schema = json.loads(json.dumps(json_schema))
        task_schema.pop("$schema", None)
        return {
            "type": "object",
            "properties": {
                "freigabe": {
                    "type": "boolean",
                    "description": "true, wenn die Antwort fachlich korrekt und belegt ist.",
                },
                "probleme": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Konkrete Maengel der Antwort; leer, wenn freigegeben.",
                },
                "korrigierte_antwort": {
                    "anyOf": [task_schema, {"type": "null"}],
                    "description": (
                        "Bei freigabe=false die vollstaendig korrigierte Antwort im Schema "
                        "der Aufgabe, sonst null."
                    ),
                },
            },
            "required": ["freigabe", "probleme"],
        }

    def _record_checker_verdict(
        self, request_context_log: str, freigabe: Optional[bool], probleme: List[str], correction_taken: bool
    ) -> None:
        """Appends one checker verdict to the in-memory protocol."""
        self.checker_log.append({
            "task": request_context_log,
            "checker_model": CHECKER_MODEL,
            "freigabe": freigabe,
            "probleme": probleme,
            "korrektur_uebernommen": correction_taken,
        })

    async def generate_checked_json_response(
        self,
        prompt: str,
        json_schema: Dict[str, Any],
        gcs_uris: List[str] = None,
        request_context_log: str = "Generic AI Request",
        model_override: Optional[str] = None,
        max_retries: int = None
    ) -> Dict[str, Any]:
        """
        Generates an answer and has a second, independent call verify it (maker/checker).

        The checker sees the same source documents and the maker's answer, and returns a
        verdict plus an optional correction. Only answers that end up in the report or
        produce findings are worth this second pass; bulk extraction is not.

        Fails open: if the checker call itself fails, the maker's answer is returned with
        a warning. An unverified chapter is worse than an empty one only in theory — in
        practice an empty chapter breaks the report assembly.
        """
        answer = await self.generate_json_response(
            prompt, json_schema, gcs_uris, request_context_log, model_override, max_retries
        )
        if not self.checker_enabled:
            return answer

        checker_prompt = (
            self.checker_prompt_template
            # replace() not format(): both the task prompt and the answer contain JSON
            # braces, which format() would try to interpret.
            .replace("{original_prompt}", prompt)
            .replace("{antwort_json}", json.dumps(answer, indent=2, ensure_ascii=False))
        )

        try:
            verdict = await self.generate_json_response(
                prompt=checker_prompt,
                json_schema=self._build_checker_schema(json_schema),
                gcs_uris=gcs_uris,
                request_context_log=f"Checker[{request_context_log}]",
                model_override=CHECKER_MODEL,
                max_retries=2,
            )
        except Exception as e:  # noqa: BLE001 — fail open, but never silently
            logging.warning(
                f"[{request_context_log}] Checker call failed ({type(e).__name__}: {e}). "
                "Keeping the unverified answer."
            )
            self._record_checker_verdict(
                request_context_log, None, [f"Checker-Aufruf fehlgeschlagen: {e}"], False
            )
            return answer

        problems = [str(p) for p in (verdict.get("probleme") or [])]
        if verdict.get("freigabe"):
            logging.info(f"[{request_context_log}] Checker approved the answer.")
            self._record_checker_verdict(request_context_log, True, problems, False)
            return answer

        correction = verdict.get("korrigierte_antwort")
        if not isinstance(correction, dict):
            logging.warning(
                f"[{request_context_log}] Checker rejected the answer but supplied no correction: "
                f"{problems}. Keeping the original answer."
            )
            self._record_checker_verdict(request_context_log, False, problems, False)
            return answer

        try:
            validate(instance=correction, schema=json_schema)
        except ValidationError as e:
            clean_msg = e.message.split('\n')[0]
            logging.warning(
                f"[{request_context_log}] Checker correction does not match the task schema "
                f"({clean_msg}). Keeping the original answer."
            )
            self._record_checker_verdict(
                request_context_log, False, problems + [f"Korrektur schema-invalide: {clean_msg}"], False
            )
            return answer

        logging.warning(
            f"[{request_context_log}] Checker corrected the answer: {problems}"
        )
        self._record_checker_verdict(request_context_log, False, problems, True)
        return correction

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
