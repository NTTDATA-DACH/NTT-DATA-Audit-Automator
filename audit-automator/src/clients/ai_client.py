# src/clients/ai_client.py
"""Single entry point for every Gemini call on the Gemini Enterprise Agent Platform (ex Vertex AI).

One model for every task, gemini-4-argon (google-genai >= 2.30.1). Cost and quality are set per
call by the thinking level (low | medium | high) and the output budget, never by a second model.
Concurrency, the retry policy, the batch tier and token accounting live here, so a rule for
"all AI calls" holds by construction instead of by discipline at each call site.

Argon facts this module relies on (verified 14 Oct 2026):
  - one 1,048,576-token window shared by input, thinking and output; max_output_tokens counts
    thinking tokens, above 65,536 the SDK continues the answer in 64K hops (long decode)
  - thinking_level low/medium/high only; "minimal", "off" and thinking_budget are a 400
  - temperature/top_p/top_k are ignored, candidate_count and the penalties are a 400
  - implicit caching only: a repeated request prefix (>= 6,144 tokens) is discounted by itself,
    caches.create / cached_content are a 400 and do not appear here
"""
import asyncio
import contextvars
import datetime
import hashlib
import json
import logging
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import httpx
from google import genai
from google.genai import types
from google.genai import errors as genai_errors
from jsonschema import validate, SchemaError, ValidationError

from src.assets_loader import load_asset_json
from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.spreadsheet import SHEET_SUFFIXES, render_sheet_text
from src.constants import (
    BATCH_DEADLINE_S,
    BATCH_MIN_ITEMS,
    BATCH_PREFIX,
    CHECKER_THINKING_LEVEL,
    ENABLE_BATCH,
    ENABLE_MAKER_CHECKER,
    GEMINI_MODEL,
    MAX_OUTPUT_TOKENS,
    PROMPT_CONFIG_PATH,
    THINKING_LEVEL,
)

# Attempts per call. The budget covers transient API errors and output that fails to parse.
MAX_RETRIES = 5
BACKOFF_CAP_S = 30

# App-level thinking names -> Argon levels. Argon knows low | medium | high only; "minimal" and
# "off" (older Flash models) are a hard 400, so legacy config values are mapped with a log line
# instead of failing the run. Anything else is a config error and raises.
_THINKING_LEVELS = {"low": "low", "medium": "medium", "high": "high",
                    "minimal": "low", "off": "low", "max": "high"}

# max_output_tokens counts thinking tokens. Below MIN the model thinks the budget away and returns
# truncated JSON without an error; above the window the API returns 400.
MIN_MAX_OUTPUT_TOKENS = 32_768
MAX_MAX_OUTPUT_TOKENS = 1_048_576

# A long-decode hop (gemini-4-argon emits up to 65,536 thinking+output tokens per hop, then
# finish_reason CONTINUATION and the SDK resends) may run 20 minutes before the server closes
# it; the client read timeout matches. Milliseconds.
HTTP_TIMEOUT_MS = 1_200_000

# A batch job in one of these states will not change any more. PAUSED is deliberately not
# terminal: a paused job can resume and bill later, so it is awaited and cancelled like a
# running one.
_BATCH_TERMINAL_STATES = frozenset({
    "JOB_STATE_SUCCEEDED", "JOB_STATE_PARTIALLY_SUCCEEDED", "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED",
})
_BATCH_POLL_S = 10
_BATCH_CANCEL_GRACE_S = 60


def _is_transient(exc: BaseException) -> bool:
    """True for failures worth a retry: 429, 5xx, timeouts and dropped connections.

    Permanent errors (400 invalid request, 401/403 auth, 404 unknown model) are not: retrying
    them burns quota and buries the cause. httpx transport errors (the SDK's wire layer) carry
    no status code and often an empty message, so they are matched by type.
    """
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError, httpx.TransportError)):
        return True
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code == 429 or 500 <= code < 600
    msg = str(exc).lower()
    return any(s in msg for s in ("timeout", "timed out", "connection", "unavailable", "deadline"))


def _describe(exc: BaseException) -> str:
    """One readable line per failure for the log; the traceback goes to the log separately."""
    if isinstance(exc, genai_errors.APIError):
        return f"Google API error {exc.code}: {exc.message}"
    text = str(exc)
    if "Unterminated string" in text or "JSONDecodeError" in text:
        return "JSON parsing error"
    return f"{type(exc).__name__}: {text}"

# The audit stage whose work the current coroutine belongs to. Stages run concurrently
# and share one AiClient, so the owner of a checker verdict must be recorded when the
# verdict is appended — not when a stage happens to persist the log. asyncio copies the
# context into every task it creates, so a value set in run_single_stage reaches all
# AI calls that stage spawns, and only those.
current_stage: contextvars.ContextVar[str] = contextvars.ContextVar("current_stage", default="unknown")


class AiClient:
    """All Gemini calls of the audit: one model, one retry policy, one place for the numbers."""

    def __init__(self, config: AppConfig, gcs_client: Optional[GcsClient] = None):
        self.config = config
        # Only the batch tier needs GCS (Vertex batch jobs read and write JSONL there).
        self.gcs_client = gcs_client

        prompt_config = load_asset_json(PROMPT_CONFIG_PATH)

        base_system_message = prompt_config.get("system_message", "")
        if not base_system_message:
            logging.warning("System message is empty. AI calls will not have a predefined persona.")

        # Append the current date to the system prompt. The undated persona is kept for the
        # batch job key, so a job interrupted before midnight is still found after it.
        current_date = datetime.date.today().strftime("%Y-%m-%d")
        self.base_system_message = base_system_message
        self.system_message = f"{base_system_message}\n\nImportant: Today's date is {current_date}."

        # Location "global": gemini-4-argon serves the multi-regions global, us and eu (single
        # regions are not listed); batch jobs run on the global endpoint. No other client takes a
        # region either: GCS is addressed by bucket name and Document AI derives its location from
        # the processor name. enterprise=True is the Agent Platform flag (google-genai >= 1.74;
        # vertexai=True is its legacy alias, never pass both). The 20-minute read timeout covers
        # one long-decode hop.
        self.model = GEMINI_MODEL
        vertex_location = "global"
        self.client = genai.Client(
            enterprise=True,
            project=config.gcp_project_id,
            location=vertex_location,
            http_options=types.HttpOptions(timeout=HTTP_TIMEOUT_MS),
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

        self.batch_enabled = ENABLE_BATCH and gcs_client is not None
        if ENABLE_BATCH and gcs_client is None:
            logging.info("AiClient has no GCS client: the batch tier is disabled for this process.")

        # Token accounting across the process, so the cache and batch savings show in the log.
        self.usage_totals: Dict[str, int] = defaultdict(int)

        # Spreadsheets reach the model as row text; each file is downloaded and rendered once.
        self._rendered_docs: Dict[str, str] = {}

        logging.info(
            f"Gemini client instantiated for project '{config.gcp_project_id}': model '{self.model}', "
            f"location '{vertex_location}', hop timeout {HTTP_TIMEOUT_MS // 60000} min."
        )
        logging.info(
            f"Thinking level '{self._resolve_thinking_level()}' for document reasoning, "
            f"output budget {self._check_max_output(MAX_OUTPUT_TOKENS):,} tokens (thinking included)."
        )
        logging.info(f"System Message Context includes today's date: {current_date}")
        logging.info(
            f"Maker/checker is {'enabled' if self.checker_enabled else 'disabled'}"
            + (f" (checker thinking level '{self._resolve_thinking_level(CHECKER_THINKING_LEVEL)}')." if self.checker_enabled else ".")
        )
        logging.info(
            f"Batch tier {'on' if self.batch_enabled else 'off'} (deadline {BATCH_DEADLINE_S}s, min {BATCH_MIN_ITEMS} items); "
            "caching is implicit (documents ahead of the prompt, repeated prefix discounted by the API)."
        )

    # ------------------------------------------------------------------ generation config
    @staticmethod
    def _resolve_thinking_level(level: Optional[str] = None) -> str:
        """Argon thinking level for an app-level name (default THINKING_LEVEL).

        Legacy names from older configs are mapped ("minimal"/"off" -> "low", "max" -> "high")
        with a log line, so a migrated .env never 400s; an unknown name is a config error.
        """
        name = (level or THINKING_LEVEL or "medium").strip().lower()
        try:
            resolved = _THINKING_LEVELS[name]
        except KeyError:
            raise ValueError(f"Thinking level {name!r} unknown: use low, medium or high.") from None
        if resolved != name:
            logging.info(f"Thinking level '{name}' does not exist on gemini-4-argon; using '{resolved}'.")
        return resolved

    @staticmethod
    def _check_max_output(n: int) -> int:
        """Refuse an output budget the model would think away or the API would reject."""
        if not MIN_MAX_OUTPUT_TOKENS <= n <= MAX_MAX_OUTPUT_TOKENS:
            raise ValueError(
                f"MAX_OUTPUT_TOKENS={n}: the budget includes thinking tokens; use "
                f"{MIN_MAX_OUTPUT_TOKENS:,}..{MAX_MAX_OUTPUT_TOKENS:,} (default 65,536)."
            )
        return n

    def _build_generation_config(
        self,
        json_schema: Dict[str, Any],
        thinking_level: Optional[str] = None,
    ) -> types.GenerateContentConfig:
        """Build the GenerateContentConfig, enforcing JSON output against the given schema.

        No sampling parameters: temperature, top_p and top_k are ignored on gemini-4-argon,
        candidate_count and the penalties return 400. Determinism lives in the prompt and the
        schema. No caching fields: cached_content is a 400 on Argon; the discount comes from the
        request prefix (system instruction, then documents) repeating between calls.
        """
        try:
            schema_for_api = json.loads(json.dumps(json_schema))
            schema_for_api.pop("$schema", None)
        except Exception as e:
            logging.error(f"Failed to process JSON schema before API call: {e}")
            raise ValueError("Invalid JSON schema provided.") from e

        return types.GenerateContentConfig(
            system_instruction=self.system_message,
            response_mime_type="application/json",
            # The assets are real JSON Schemas, so they go to the field that speaks that dialect.
            response_json_schema=schema_for_api,
            thinking_config=types.ThinkingConfig(thinking_level=self._resolve_thinking_level(thinking_level)),
            # Thinking and answer share this budget; the default 65,536 is the API default.
            max_output_tokens=self._check_max_output(MAX_OUTPUT_TOKENS),
            # When a hop ends with finish_reason CONTINUATION the SDK (>= 2.30.1) resends the
            # request with the continuation token. Stated explicitly so an older SDK fails here.
            automatic_continuation=True,
        )

    async def _rendered_sheet(self, uri: str) -> str:
        """Row text of a spreadsheet in GCS, rendered once per process."""
        if uri not in self._rendered_docs:
            if self.gcs_client is None:
                raise RuntimeError(f"Spreadsheet context needs a GCS client (AiClient was built without one): {uri}")
            blob_name = uri.replace(f"gs://{self.gcs_client.bucket.name}/", "", 1)
            data = await asyncio.to_thread(self.gcs_client.download_blob_as_bytes, self.gcs_client.bucket.blob(blob_name))
            text = render_sheet_text(data, blob_name)
            logging.info(f"Rendered spreadsheet '{blob_name}' as text: {len(text):,} characters (~{len(text) // 4:,} tokens).")
            self._rendered_docs[uri] = text
        return self._rendered_docs[uri]

    async def _document_part(self, uri: str) -> types.Part:
        """A PDF goes as a file part, a spreadsheet as its row text; anything else is refused."""
        lower = uri.lower()
        if lower.endswith(".pdf"):
            return types.Part.from_uri(file_uri=uri, mime_type="application/pdf")
        if lower.endswith(SHEET_SUFFIXES):
            return types.Part.from_text(text=await self._rendered_sheet(uri))
        raise ValueError(f"Unsupported document format for the model: {uri}")

    async def _document_parts(self, gcs_uris: List[str]) -> List[types.Part]:
        return [await self._document_part(uri) for uri in gcs_uris]

    async def _build_contents(self, prompt: str, gcs_uris: List[str] = None) -> List[Any]:
        """Assemble the request contents: the documents first, the prompt last.

        Implicit caching discounts a repeated prefix (system instruction + documents) of at
        least 6,144 tokens on the Agent Platform by about 95 %; a varying prompt ahead of the
        documents would throw that away on every call. This is the only caching Argon has, so
        the maker, its checker and the Chapter-3 subchapters on the same set profit from it
        as long as their calls land within the 15-20 minute cache lifetime.
        """
        contents: List[Any] = list(await self._document_parts(gcs_uris or []))
        contents.append(prompt)
        return contents

    @staticmethod
    def _finish_reason(response: Any) -> Optional[str]:
        """Name of the first candidate's finish reason, None when there is no candidate."""
        candidates = getattr(response, "candidates", None) or []
        return getattr(getattr(candidates[0], "finish_reason", None), "name", None) if candidates else None

    @staticmethod
    def _extract_json(response: Any) -> Dict[str, Any]:
        """Validate the response shape and parse its text payload as JSON.

        Raises ValueError (which the retry loop catches) for every unusable response.
        MAX_TOKENS counts as unusable: a truncated answer that still happens to parse
        would flow into the report as if it were complete.
        """
        if not response.candidates:
            raise ValueError("The model response contained no candidates.")

        finish_reason = AiClient._finish_reason(response)
        if finish_reason != "STOP":
            # MAX_TOKENS is a truncated answer; a surviving CONTINUATION means the SDK stopped
            # following the continuation tokens, which leaves a truncated answer too.
            hint = " (truncated output)" if finish_reason in ("MAX_TOKENS", "CONTINUATION") else ""
            raise ValueError(f"Model finished with non-OK reason: '{finish_reason}'{hint}")

        # With thinking enabled a candidate can carry no text part at all; json.loads(None)
        # would raise TypeError, which the retry loop does not catch.
        if response.text is None:
            raise ValueError("The model response contained no text part.")

        try:
            return json.loads(response.text)
        except json.JSONDecodeError as e:
            # Clean JSON error without the full traceback
            raise ValueError(f"Failed to parse model response as JSON: {str(e).split(':')[0]}")

    @staticmethod
    def _validate_against_schema(payload: Any, json_schema: Dict[str, Any], request_context_log: str) -> None:
        """Validates a model reply against the schema that was sent with the request.

        Raises ValueError on a mismatch so the caller's retry loop treats it like any
        other bad response. A broken schema asset is a code bug, not a model failure,
        so it is logged as critical and also raised rather than silently skipped.
        """
        try:
            validate(instance=payload, schema=json_schema)
        except ValidationError as e:
            location = " -> ".join(str(p) for p in e.absolute_path) or "<root>"
            raise ValueError(f"Response does not match the requested schema at '{location}': {e.message}")
        except SchemaError as e:
            logging.critical(f"[{request_context_log}] The requested JSON schema is itself invalid: {e.message}")
            raise ValueError(f"Invalid JSON schema supplied: {e.message}") from e

    # ------------------------------------------------------------------ token accounting
    def _record_usage(self, label: str, model: str, usage: Any, tier: str = "standard",
                      finish_reason: Optional[str] = None) -> None:
        """Log one call's token split (and finish reason) and add it to the process totals."""
        if usage is None:
            return
        get = usage.get if isinstance(usage, dict) else lambda k, d=None: getattr(usage, k, d)
        prompt = get("prompt_token_count") or get("promptTokenCount") or 0
        cached = get("cached_content_token_count") or get("cachedContentTokenCount") or 0
        thoughts = get("thoughts_token_count") or get("thoughtsTokenCount") or 0
        output = get("candidates_token_count") or get("candidatesTokenCount") or 0
        self.usage_totals[f"{tier}_calls"] += 1
        self.usage_totals["prompt_tokens"] += prompt
        self.usage_totals["cached_tokens"] += cached
        self.usage_totals["thought_tokens"] += thoughts
        self.usage_totals["output_tokens"] += output
        logging.info(
            f"[{label}] {model} ({tier}): input {prompt:,} tokens ({cached:,} from cache), "
            f"thinking {thoughts:,}, output {output:,}" + (f", finish {finish_reason}." if finish_reason else ".")
        )

    def usage_summary(self) -> str:
        """One line with the process totals, for the end of a stage."""
        t = self.usage_totals
        return (
            f"AI usage so far: {t['standard_calls']} standard + {t['batch_calls']} batch calls, "
            f"input {t['prompt_tokens']:,} tokens ({t['cached_tokens']:,} from cache), "
            f"thinking {t['thought_tokens']:,}, output {t['output_tokens']:,}."
        )

    # ------------------------------------------------------------------ retry helper
    async def _retry_admin(self, fn: Callable[[], Awaitable[Any]], label: str) -> Any:
        """Run an SDK management call (batch job) with the shared transient-only policy."""
        for attempt in range(MAX_RETRIES):
            try:
                return await fn()
            except Exception as e:
                if not _is_transient(e) or attempt == MAX_RETRIES - 1:
                    raise
                wait_time = min(2 ** attempt, BACKOFF_CAP_S)
                logging.warning(f"[{label}] attempt {attempt + 1}/{MAX_RETRIES} failed: {_describe(e)}. Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
        raise RuntimeError(f"[{label}] exhausted retries")  # unreachable: the loop raises first

    # ------------------------------------------------------------------ standard calls
    async def generate_json_response(
        self,
        prompt: str,
        json_schema: Dict[str, Any],
        gcs_uris: List[str] = None,
        request_context_log: str = "Generic AI Request",
        max_retries: int = None,
        thinking_level: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generates a JSON response from the AI model, enforcing a specific schema and
        optionally providing GCS files as context. Implements an async retry loop
        with exponential backoff and connection limiting.

        The reply is validated against the schema that was sent, and a mismatch is
        retried like any other failure. Constrained decoding makes that rare, but
        "rare" is not "never" — and an unvalidated reply reaches the report, where a
        missing key becomes a silently wrong audit statement.

        Args:
            prompt: The text prompt for the model.
            json_schema: The JSON schema to enforce on the model's output.
            gcs_uris: A list of 'gs://...' URIs pointing to PDF files for context.
            request_context_log: A string to identify the request source in logs.
            max_retries: Optional override for the number of attempts (defaults to MAX_RETRIES).
            thinking_level: Optional thinking level for this call (defaults to THINKING_LEVEL).

        Returns:
            The parsed, schema-valid JSON response from the model.
        """
        retries = max_retries if max_retries is not None else MAX_RETRIES

        uris = list(gcs_uris or [])
        gen_config = self._build_generation_config(json_schema, thinking_level)
        contents = await self._build_contents(prompt, uris)
        if uris and self.config.is_test_mode:
            logging.info(f"Attaching {len(uris)} GCS files ahead of the prompt.")

        for attempt in range(retries):
            try:
                logging.info(f"[{request_context_log}] Attempt {attempt + 1}/{retries}: Calling Gemini model '{self.model}'...")
                # The semaphore guards the in-flight call only: holding it across the
                # backoff sleep would idle a concurrency slot for up to 30 seconds.
                async with self.semaphore:
                    response = await self.client.aio.models.generate_content(
                        model=self.model,
                        contents=contents,
                        config=gen_config,
                    )

                self._record_usage(request_context_log, self.model, getattr(response, "usage_metadata", None),
                                   finish_reason=self._finish_reason(response))
                response_json = self._extract_json(response)
                self._validate_against_schema(response_json, json_schema, request_context_log)
                logging.info(f"[{request_context_log}] Successfully generated and parsed JSON response on attempt {attempt + 1}.")
                return response_json

            except Exception as e:
                last_attempt = attempt == retries - 1
                # Unparseable output (ValueError) and transient API failures share the budget;
                # permanent API errors raise at once. TypeError/AttributeError are belt-and-braces
                # for an unexpected response shape: without them a malformed reply would bypass
                # the retries entirely.
                retryable = isinstance(e, (ValueError, TypeError, AttributeError)) or _is_transient(e)
                if not retryable or last_attempt:
                    logging.error(
                        f"[{request_context_log}] AI generation failed on attempt {attempt + 1}/{retries}: {_describe(e)}",
                        exc_info=True,
                    )
                    raise

                wait_time = min(2 ** attempt, BACKOFF_CAP_S)
                logging.warning(f"[{request_context_log}] Attempt {attempt + 1}/{retries} failed: {_describe(e)}. Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)

        raise RuntimeError("AI generation failed unexpectedly after exhausting all retries.")

    # ------------------------------------------------------------------ maker / checker
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
        """Appends one checker verdict to the in-memory protocol.

        The owning stage is stamped here, not when the log is persisted: stages run
        concurrently, so by persist time the list holds other stages' verdicts too.
        """
        self.checker_log.append({
            "stage": current_stage.get(),
            "task": request_context_log,
            "checker_model": self.model,
            "checker_thinking_level": self._resolve_thinking_level(CHECKER_THINKING_LEVEL),
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
        max_retries: int = None
    ) -> Dict[str, Any]:
        """
        Generates an answer and has a second, independent call verify it (maker/checker).

        The checker sees the same source documents and the maker's answer, and returns a
        verdict plus an optional correction. Only answers that end up in the report or
        produce findings are worth this second pass; bulk extraction is not. Same model in
        two roles: the maker at THINKING_LEVEL, the checker at CHECKER_THINKING_LEVEL (high).

        Fails open: if the checker call itself fails, the maker's answer is returned with
        a warning. An unverified chapter is worse than an empty one only in theory — in
        practice an empty chapter breaks the report assembly.
        """
        answer = await self.generate_json_response(
            prompt, json_schema, gcs_uris, request_context_log, max_retries=max_retries
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
                thinking_level=CHECKER_THINKING_LEVEL,
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

    # ------------------------------------------------------------------ batch tier
    def _batch_job_key(self, json_schema: Dict[str, Any], thinking_level: Optional[str], items: Dict[str, str]) -> str:
        """Stable job id over everything that shapes the output, so a rerun finds its folder.

        The undated persona goes in, not the dated system message: a job interrupted before
        midnight must still be found after it.
        """
        digest = hashlib.sha256()
        for part in (self.model, self._resolve_thinking_level(thinking_level), str(MAX_OUTPUT_TOKENS),
                     self.base_system_message, json.dumps(json_schema, sort_keys=True)):
            digest.update(part.encode("utf-8"))
            digest.update(b"\0")
        for key in sorted(items):
            digest.update(key.encode("utf-8"))
            digest.update(b"\0")
            digest.update(items[key].encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()[:16]

    @staticmethod
    def _batch_request_line(prompt: str, gen_config: types.GenerateContentConfig) -> str:
        """One JSONL line in the Vertex batch format: {"request": GenerateContentRequest}.

        The config is serialised with the SDK's camelCase aliases, which is what the REST
        surface of batch prediction expects; the system instruction moves from the config
        into the request's own field.
        """
        config_json = gen_config.model_dump(by_alias=True, exclude_none=True)
        system_instruction = config_json.pop("systemInstruction", None)
        # An SDK-side switch, not a GenerationConfig field: a batch job returns whole answers.
        config_json.pop("automaticContinuation", None)
        request: Dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": config_json,
        }
        if system_instruction:
            request["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        return json.dumps({"request": request}, ensure_ascii=False)

    @staticmethod
    def _parse_batch_output_lines(
        lines: List[str], prompt_to_keys: Dict[str, List[str]]
    ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str], Dict[str, Dict[str, Any]]]:
        """Map Vertex batch output lines back to item keys.

        Each line echoes the request and carries either a `response` or a non-empty
        `status`. Items are matched on the prompt text; items with identical prompts share
        one request and each receive their own copy of the answer. Returns (ok, errors,
        usage): ok = key -> parsed JSON, errors = key (or '?n') -> reason, usage = first key
        of a prompt -> that line's usage metadata (one entry per billed line).
        """
        ok: Dict[str, Dict[str, Any]] = {}
        errors: Dict[str, str] = {}
        usage_by_key: Dict[str, Dict[str, Any]] = {}
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                errors[f"?{len(errors)}"] = "unparseable output line"
                continue
            try:
                prompt_text = record["request"]["contents"][0]["parts"][0]["text"]
            except (KeyError, IndexError, TypeError):
                errors[f"?{len(errors)}"] = "output line without request echo"
                continue
            keys = prompt_to_keys.get(prompt_text)
            if not keys:
                errors[f"?{len(errors)}"] = "output line matches no submitted item"
                continue
            if all(key in ok for key in keys):
                continue  # a later job's duplicate changes nothing
            status = record.get("status")
            if status:
                for key in keys:
                    errors[key] = str(status)
                continue
            response = record.get("response") or {}
            candidates = response.get("candidates") or []
            if not candidates:
                for key in keys:
                    errors[key] = "no candidates"
                continue
            candidate = candidates[0]
            # Same rule as _extract_json: MAX_TOKENS is a truncated answer, not a usable one.
            finish_reason = candidate.get("finishReason") or candidate.get("finish_reason") or "STOP"
            if finish_reason != "STOP":
                for key in keys:
                    errors[key] = f"finish reason {finish_reason}"
                continue
            parts = (candidate.get("content") or {}).get("parts") or []
            text = next((p.get("text") for p in parts if p.get("text") and not p.get("thought")), None)
            if text is None:
                for key in keys:
                    errors[key] = "no text part"
                continue
            try:
                # One parse per key: the consumers mutate the result, so no two keys may alias it.
                for key in keys:
                    ok[key] = json.loads(text)
            except json.JSONDecodeError as e:
                for key in keys:
                    errors[key] = f"JSON parsing error: {str(e).split(':')[0]}"
                continue
            usage = response.get("usageMetadata") or response.get("usage_metadata")
            if usage:
                usage_by_key[keys[0]] = usage
        return ok, errors, usage_by_key

    async def _read_batch_results(
        self,
        out_prefix: str,
        prompt_to_keys: Dict[str, List[str]],
        json_schema: Dict[str, Any],
        request_context_log: str,
        known: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """Collect results under the job's output prefix (any job, any run) not yet in `known`.

        Only new results are returned and only their lines are counted as usage, so a second
        read after the job does not double-count what the resume read already found. Every
        result is validated against the schema like a standard reply; a mismatch leaves the
        item open for the standard path instead of reaching the report.
        """
        new: Dict[str, Dict[str, Any]] = {}
        errors: Dict[str, str] = {}
        blob_names = await self.gcs_client.list_blob_names_async(out_prefix)
        for name in blob_names:
            if not name.endswith(".jsonl"):
                continue
            text = await self.gcs_client.read_text_file_async(name)
            ok, errs, usage_by_key = self._parse_batch_output_lines(text.splitlines(), prompt_to_keys)
            for key, value in ok.items():
                if key in known or key in new:
                    continue
                try:
                    self._validate_against_schema(value, json_schema, request_context_log)
                except ValueError as e:
                    errs[key] = str(e)
                    continue
                new[key] = value
                usage = usage_by_key.get(key)
                if usage:
                    self._record_usage(request_context_log, self.model, usage, tier="batch")
            errors.update(errs)
        for key, reason in errors.items():
            if key not in known and key not in new:
                logging.warning(f"[{request_context_log}] batch item '{key}': {reason}")
        return new

    async def _batch_state(self, name: str) -> str:
        job = await self._retry_admin(lambda: self.client.aio.batches.get(name=name), "batches.get")
        state = getattr(job, "state", None)
        return getattr(state, "name", None) or str(state or "")

    async def _cancel_batch_quietly(self, name: str, request_context_log: str) -> None:
        """Best-effort cancel for a job whose fate is unknown; a finished job makes this a no-op."""
        try:
            await self.client.aio.batches.cancel(name=name)
        except Exception as e:  # noqa: BLE001 — best-effort: the job may already be done
            logging.info(f"[{request_context_log}] cancel of batch job {name} not accepted ({_describe(e)}).")

    async def _wait_for_batch(self, name: str, request_context_log: str) -> Tuple[str, bool]:
        """Poll until the job is done or the deadline passes; on the deadline cancel, don't abandon.

        Returns (state, deadline_hit). An abandoned job can finish later and be billed anyway;
        a cancelled one keeps what it already delivered, which the caller reads afterwards.
        """
        started = time.monotonic()
        state = await self._batch_state(name)
        while state not in _BATCH_TERMINAL_STATES:
            left = BATCH_DEADLINE_S - (time.monotonic() - started)
            if left <= 0:
                break
            await asyncio.sleep(min(_BATCH_POLL_S, left))
            state = await self._batch_state(name)
        if state in _BATCH_TERMINAL_STATES:
            return state, False

        logging.warning(f"[{request_context_log}] batch job {name} not done after {BATCH_DEADLINE_S}s (state {state}): cancelling.")
        try:
            await self._retry_admin(lambda: self.client.aio.batches.cancel(name=name), "batches.cancel")
        except Exception as e:  # noqa: BLE001 — the job may have finished between poll and cancel
            logging.warning(f"[{request_context_log}] cancel failed ({_describe(e)}); reading what landed.")
        grace_started = time.monotonic()
        while state not in _BATCH_TERMINAL_STATES and time.monotonic() - grace_started < _BATCH_CANCEL_GRACE_S:
            await asyncio.sleep(_BATCH_POLL_S)
            state = await self._batch_state(name)
        return state, True

    async def generate_json_batch(
        self,
        items: Dict[str, str],
        json_schema: Dict[str, Any],
        thinking_level: Optional[str] = None,
        request_context_log: str = "Batch AI Request",
    ) -> Dict[str, Dict[str, Any]]:
        """Bulk JSON generation through the batch tier (about half the standard price).

        `items` maps a stable key to its prompt. Returns key -> parsed JSON for every item
        that came back parseable; whatever is missing is the caller's to send through
        `generate_json_response`. Items with identical prompts share one request. Results are
        keyed by content, so an interrupted job's output is reused on the next run and a
        result that lands after the deadline meets an existing key. Every failure of the
        batch machinery itself is logged and leaves the open items to the standard path.
        """
        if not self.batch_enabled or not items:
            return {}

        job_key = self._batch_job_key(json_schema, thinking_level, items)
        job_prefix = f"{BATCH_PREFIX}{job_key}/"
        out_prefix = f"{job_prefix}out/"
        prompt_to_keys: Dict[str, List[str]] = defaultdict(list)
        for key, prompt in items.items():
            prompt_to_keys[prompt].append(key)
        if len(prompt_to_keys) < len(items):
            logging.info(f"[{request_context_log}] {len(items) - len(prompt_to_keys)} item(s) repeat another item's prompt and share its request.")

        results: Dict[str, Dict[str, Any]] = {}
        # Resume: results of an earlier (interrupted) job under the same prefix cost nothing.
        try:
            results.update(await self._read_batch_results(out_prefix, prompt_to_keys, json_schema, request_context_log, known={}))
        except Exception as e:  # noqa: BLE001 — a failed resume read only costs the reuse
            logging.warning(f"[{request_context_log}] could not read earlier batch output under {out_prefix} ({_describe(e)}).")
        missing_prompts = [prompt for prompt, keys in prompt_to_keys.items() if any(key not in results for key in keys)]
        if results:
            logging.info(f"[{request_context_log}] batch resume: {len(results)} result(s) found under {out_prefix}, {len(missing_prompts)} request(s) still open.")
        if not missing_prompts:
            return results
        if len(missing_prompts) < BATCH_MIN_ITEMS:
            logging.info(f"[{request_context_log}] {len(missing_prompts)} open request(s) < BATCH_MIN_ITEMS={BATCH_MIN_ITEMS}: standard calls instead of a batch job.")
            return results

        gen_config = self._build_generation_config(json_schema, thinking_level)
        lines = [self._batch_request_line(prompt, gen_config) for prompt in missing_prompts]
        src_path = f"{job_prefix}requests-{int(time.time())}.jsonl"
        bucket = self.gcs_client.bucket.name
        try:
            await self.gcs_client.upload_from_string_async("\n".join(lines) + "\n", src_path, content_type="application/json")
            job = await self._retry_admin(
                lambda: self.client.aio.batches.create(
                    model=self.model,
                    src=f"gs://{bucket}/{src_path}",
                    config=types.CreateBatchJobConfig(dest=f"gs://{bucket}/{out_prefix}", display_name=f"audit-{job_key}"),
                ),
                "batches.create",
            )
        except Exception as e:  # noqa: BLE001 — the standard path takes over
            logging.warning(f"[{request_context_log}] batch job not created ({_describe(e)}); {len(missing_prompts)} request(s) run as standard calls.")
            return results

        logging.info(f"[{request_context_log}] batch job {job.name} submitted: {len(missing_prompts)} request(s) on '{self.model}', deadline {BATCH_DEADLINE_S}s.")
        try:
            state, deadline_hit = await self._wait_for_batch(job.name, request_context_log)
            delivered = await self._read_batch_results(out_prefix, prompt_to_keys, json_schema, request_context_log, known=results)
        except Exception as e:  # noqa: BLE001 — the job's fate is unknown: stop it, go standard
            logging.warning(
                f"[{request_context_log}] batch job {job.name} could not be awaited or read ({_describe(e)}); "
                "cancelling it and running the open items as standard calls."
            )
            await self._cancel_batch_quietly(job.name, request_context_log)
            return results
        results.update(delivered)
        open_count = sum(1 for key in items if key not in results)
        logging.info(
            f"[{request_context_log}] batch job {job.name} {state}"
            + (f" after the {BATCH_DEADLINE_S}s deadline" if deadline_hit else "")
            + f": {len(delivered)} delivered, {open_count} to run as standard calls."
        )
        return results
