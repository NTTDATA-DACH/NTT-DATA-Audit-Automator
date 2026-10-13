# src/clients/ai_client.py
"""Single entry point for every Gemini call on Vertex AI (google-genai SDK).

Concurrency, the retry policy, context caching, the batch tier and token accounting live
here, so a rule for "all AI calls" holds by construction instead of by discipline at each
call site.
"""
import asyncio
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
from jsonschema import validate, ValidationError

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.constants import (
    BATCH_DEADLINE_S,
    BATCH_MIN_ITEMS,
    BATCH_PREFIX,
    CHECKER_MODEL,
    CONTEXT_CACHE_TTL_S,
    ENABLE_BATCH,
    ENABLE_CONTEXT_CACHE,
    ENABLE_MAKER_CHECKER,
    GROUND_TRUTH_MODEL,
    PROMPT_CONFIG_PATH,
    THINKING_LEVEL,
)

# Attempts per call. The budget covers transient API errors and output that fails to parse.
MAX_RETRIES = 5
BACKOFF_CAP_S = 30

# Only these model families accept thinking_level "minimal". 3.7+/3.8 Flash and the pro tier
# reject it with a 400 validation error, so it is clamped to "low" there.
_MINIMAL_CAPABLE_MODELS = ("gemini-3.5-flash", "gemini-3.6-flash", "flash-lite", "gemini-3-flash")

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


def _mentions_cache(exc: BaseException) -> bool:
    """True when an API error is about the context cache (expired, not found, rejected)."""
    text = f"{getattr(exc, 'message', '')} {exc}".lower()
    return "cache" in text


def _describe(exc: BaseException) -> str:
    """One readable line per failure for the log; the traceback goes to the log separately."""
    if isinstance(exc, genai_errors.APIError):
        return f"Google API error {exc.code}: {exc.message}"
    text = str(exc)
    if "Unterminated string" in text or "JSONDecodeError" in text:
        return "JSON parsing error"
    return f"{type(exc).__name__}: {text}"


class AiClient:
    """A client for all Vertex AI model interactions, using the google-genai SDK."""

    def __init__(self, config: AppConfig, gcs_client: Optional[GcsClient] = None):
        self.config = config
        # Only the batch tier needs GCS (Vertex batch jobs read and write JSONL there).
        self.gcs_client = gcs_client

        with open(PROMPT_CONFIG_PATH, 'r', encoding='utf-8') as f:
            prompt_config = json.load(f)

        base_system_message = prompt_config.get("system_message", "")
        if not base_system_message:
            logging.warning("System message is empty. AI calls will not have a predefined persona.")

        # Append the current date to the system prompt. The undated persona is kept for the
        # batch job key, so a job interrupted before midnight is still found after it.
        current_date = datetime.date.today().strftime("%Y-%m-%d")
        self.base_system_message = base_system_message
        self.system_message = f"{base_system_message}\n\nImportant: Today's date is {current_date}."

        # Vertex AI location. "global" is intentional here for broad Gemini model
        # availability; config.region still drives other GCP resources (GCS, Document AI).
        # Context caching and batch jobs for base Gemini models support the global endpoint too.
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

        # Context caches: cache key -> Vertex cache name (None = known uncacheable). The per-key
        # lock stops parallel calls on the same document set from creating the cache twice.
        self.cache_enabled = ENABLE_CONTEXT_CACHE
        self._caches: Dict[str, Optional[str]] = {}
        self._cache_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

        self.batch_enabled = ENABLE_BATCH and gcs_client is not None
        if ENABLE_BATCH and gcs_client is None:
            logging.info("AiClient has no GCS client: the batch tier is disabled for this process.")

        # Token accounting across the process, so the cache and batch savings show in the log.
        self.usage_totals: Dict[str, int] = defaultdict(int)

        logging.info(f"Vertex AI Client instantiated for project '{config.gcp_project_id}' (Vertex AI location '{vertex_location}').")
        logging.info(f"System Message Context includes today's date: {current_date}")
        logging.info(
            f"Maker/checker is {'enabled' if self.checker_enabled else 'disabled'}"
            + (f" (checker model '{CHECKER_MODEL}')." if self.checker_enabled else ".")
        )
        logging.info(
            f"Context caching {'on' if self.cache_enabled else 'off'} (TTL {CONTEXT_CACHE_TTL_S}s); "
            f"batch tier {'on' if self.batch_enabled else 'off'} (deadline {BATCH_DEADLINE_S}s, min {BATCH_MIN_ITEMS} items)."
        )

    # ------------------------------------------------------------------ generation config
    @staticmethod
    def _resolve_thinking_level(model: str, level: Optional[str] = None) -> str:
        """Thinking level for a model; "minimal" is clamped to "low" where the model rejects it."""
        resolved = (level or THINKING_LEVEL or "").strip().lower()
        if resolved == "minimal" and not any(m in model for m in _MINIMAL_CAPABLE_MODELS):
            return "low"
        return resolved

    def _build_generation_config(
        self,
        json_schema: Dict[str, Any],
        model: str,
        thinking_level: Optional[str] = None,
        cached_content: Optional[str] = None,
    ) -> types.GenerateContentConfig:
        """Build the GenerateContentConfig, enforcing JSON output against the given schema.

        With a context cache the system instruction already lives in the cache and must not
        be sent again, so the config then carries `cached_content` instead.
        """
        try:
            schema_for_api = json.loads(json.dumps(json_schema))
            schema_for_api.pop("$schema", None)
        except Exception as e:
            logging.error(f"Failed to process JSON schema before API call: {e}")
            raise ValueError("Invalid JSON schema provided.") from e

        config_fields = types.GenerateContentConfig.model_fields
        kwargs: Dict[str, Any] = {
            "response_mime_type": "application/json",
            "max_output_tokens": 65535,
            # Gemini 3.x is tuned for temperature 1; lowering it degrades reasoning quality.
            "temperature": 1,
        }
        if cached_content:
            kwargs["cached_content"] = cached_content
        else:
            kwargs["system_instruction"] = self.system_message

        # The assets are real JSON Schemas, so hand them to the field that speaks that
        # dialect; response_schema (OpenAPI subset) is the fallback for older SDKs.
        if "response_json_schema" in config_fields:
            kwargs["response_json_schema"] = schema_for_api
        else:
            kwargs["response_schema"] = schema_for_api

        resolved_level = self._resolve_thinking_level(model, thinking_level)
        if resolved_level and "thinking_config" in config_fields:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=resolved_level)

        return types.GenerateContentConfig(**kwargs)

    @staticmethod
    def _build_contents(prompt: str, gcs_uris: List[str] = None) -> List[Any]:
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

    # ------------------------------------------------------------------ token accounting
    def _record_usage(self, label: str, model: str, usage: Any, tier: str = "standard") -> None:
        """Log one call's token split and add it to the process totals."""
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
            f"thinking {thoughts:,}, output {output:,}."
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
        """Run an SDK management call (cache, batch) with the shared transient-only policy."""
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

    # ------------------------------------------------------------------ context caching
    def _cache_key(self, model: str, gcs_uris: List[str]) -> str:
        """Stable key over everything that shapes the cache: model, persona, document set."""
        digest = hashlib.sha256()
        for part in (model, self.system_message, *sorted(set(gcs_uris))):
            digest.update(part.encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()[:16]

    def _reject_cache(self, model: str, gcs_uris: List[str]) -> None:
        """Remember a document set as uncacheable for the rest of the process.

        Memoising None (rather than forgetting the entry) matters: a live cache that the API
        rejects would otherwise be rediscovered by caches.list() and fail again on every call.
        """
        self._caches[self._cache_key(model, gcs_uris)] = None

    async def _get_cached_content(self, model: str, gcs_uris: List[str]) -> Optional[str]:
        """Return the Vertex cache name for (model, document set), creating it on first use.

        None means "send the documents inline": caching is off, the set is empty, or the set
        is below the model's minimum cache size. A permanent creation failure is remembered,
        so its price is paid once per set and process; a transient one is retried on the
        next call.
        """
        if not self.cache_enabled or not gcs_uris:
            return None
        key = self._cache_key(model, gcs_uris)
        async with self._cache_locks[key]:
            if key in self._caches:
                return self._caches[key]
            display_name = f"audit-{key}"
            name = await self._find_cache(display_name)
            if name is None:
                try:
                    name = await self._create_cache(model, gcs_uris, display_name)
                except Exception as e:  # noqa: BLE001 — surfaced here, the call proceeds inline
                    if _is_transient(e):
                        logging.warning(
                            f"Context cache for {len(set(gcs_uris))} document(s) on '{model}' not created "
                            f"({_describe(e)}); attaching inline this time, creation is retried on the next call."
                        )
                        return None
                    logging.warning(
                        f"Context cache for {len(set(gcs_uris))} document(s) on '{model}' rejected "
                        f"({_describe(e)}); the documents are attached inline for the rest of this run. "
                        "Sets below the 4,096-token minimum always end up here."
                    )
                    name = None
            self._caches[key] = name
            return name

    async def _find_cache(self, display_name: str) -> Optional[str]:
        """Reuse a live cache from an earlier process; caches.list() returns only unexpired ones."""
        try:
            pager = await self._retry_admin(lambda: self.client.aio.caches.list(), "caches.list")
            async for cache in pager:
                if getattr(cache, "display_name", None) == display_name:
                    logging.info(f"Reusing context cache '{display_name}' ({cache.name}, expires {cache.expire_time}).")
                    return cache.name
        except Exception as e:  # noqa: BLE001 — lookup is an optimisation; creation follows
            logging.warning(f"Could not list context caches ({_describe(e)}); creating a new one.")
        return None

    async def _create_cache(self, model: str, gcs_uris: List[str], display_name: str) -> str:
        """Cache the persona and the document set once; raises when Vertex refuses."""
        uris = sorted(set(gcs_uris))
        contents = [types.Content(
            role="user",
            parts=[types.Part.from_uri(file_uri=uri, mime_type="application/pdf") for uri in uris],
        )]
        cache_config = types.CreateCachedContentConfig(
            contents=contents,
            system_instruction=self.system_message,
            display_name=display_name,
            ttl=f"{CONTEXT_CACHE_TTL_S}s",
        )
        cache = await self._retry_admin(
            lambda: self.client.aio.caches.create(model=model, config=cache_config), "caches.create"
        )
        usage = getattr(cache, "usage_metadata", None)
        token_count = getattr(usage, "total_token_count", None) if usage else None
        logging.info(
            f"Created context cache '{display_name}' ({cache.name}) for {len(uris)} document(s) "
            f"on '{model}': {token_count or 0:,} tokens, TTL {CONTEXT_CACHE_TTL_S}s."
        )
        return cache.name

    # ------------------------------------------------------------------ standard calls
    async def generate_json_response(
        self,
        prompt: str,
        json_schema: Dict[str, Any],
        gcs_uris: List[str] = None,
        request_context_log: str = "Generic AI Request",
        model_override: Optional[str] = None,
        max_retries: int = None,
        thinking_level: Optional[str] = None,
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
            max_retries: Optional override for the number of attempts (defaults to MAX_RETRIES).
            thinking_level: Optional thinking level for this call (defaults to THINKING_LEVEL).

        Returns:
            The parsed JSON response from the model.
        """
        retries = max_retries if max_retries is not None else MAX_RETRIES

        model_to_use = model_override if model_override else GROUND_TRUTH_MODEL
        uris = list(gcs_uris or [])

        # Documents go through the context cache when there is one for this set; the
        # system message then lives in the cache and the request carries the prompt only.
        cached_content = await self._get_cached_content(model_to_use, uris)
        gen_config = self._build_generation_config(json_schema, model_to_use, thinking_level, cached_content)
        contents = self._build_contents(prompt, [] if cached_content else uris)
        if uris and self.config.is_test_mode:
            logging.info(f"Attaching {len(uris)} GCS files to the prompt{' via context cache' if cached_content else ''}.")

        for attempt in range(retries):
            try:
                logging.info(f"[{request_context_log}] Attempt {attempt + 1}/{retries}: Calling Gemini model '{model_to_use}'...")
                # The semaphore guards the in-flight call only: holding it across the
                # backoff sleep would idle a concurrency slot for up to 30 seconds.
                async with self.semaphore:
                    response = await self.client.aio.models.generate_content(
                        model=model_to_use,
                        contents=contents,
                        config=gen_config,
                    )

                self._record_usage(request_context_log, model_to_use, getattr(response, "usage_metadata", None))
                response_json = self._extract_json(response)
                logging.info(f"[{request_context_log}] Successfully generated and parsed JSON response on attempt {attempt + 1}.")
                return response_json

            except Exception as e:
                last_attempt = attempt == retries - 1
                if (cached_content and not last_attempt and isinstance(e, genai_errors.APIError)
                        and not _is_transient(e) and _mentions_cache(e)):
                    # The cache expired mid-run or Vertex rejects it: inline for the rest of the run.
                    logging.warning(
                        f"[{request_context_log}] Call with context cache rejected ({_describe(e)}). "
                        "Attaching the documents inline for this document set from now on."
                    )
                    self._reject_cache(model_to_use, uris)
                    cached_content = None
                    gen_config = self._build_generation_config(json_schema, model_to_use, thinking_level, None)
                    contents = self._build_contents(prompt, uris)
                    continue

                # Unparseable output (ValueError) and transient API failures share the budget;
                # permanent API errors raise at once.
                retryable = isinstance(e, ValueError) or _is_transient(e)
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

    # ------------------------------------------------------------------ batch tier
    def _batch_job_key(self, model: str, json_schema: Dict[str, Any], thinking_level: Optional[str], items: Dict[str, str]) -> str:
        """Stable job id over everything that shapes the output, so a rerun finds its folder.

        The undated persona goes in, not the dated system message: a job interrupted before
        midnight must still be found after it.
        """
        digest = hashlib.sha256()
        for part in (model, self._resolve_thinking_level(model, thinking_level), self.base_system_message,
                     json.dumps(json_schema, sort_keys=True)):
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
            finish_reason = candidate.get("finishReason") or candidate.get("finish_reason") or "STOP"
            if finish_reason not in ("STOP", "MAX_TOKENS"):
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
        model: str,
        request_context_log: str,
        known: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """Collect results under the job's output prefix (any job, any run) not yet in `known`.

        Only new results are returned and only their lines are counted as usage, so a second
        read after the job does not double-count what the resume read already found.
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
                new[key] = value
                usage = usage_by_key.get(key)
                if usage:
                    self._record_usage(request_context_log, model, usage, tier="batch")
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
        model: str,
        thinking_level: Optional[str] = None,
        request_context_log: str = "Batch AI Request",
    ) -> Dict[str, Dict[str, Any]]:
        """Bulk JSON generation through the Vertex batch tier (about half the standard price).

        `items` maps a stable key to its prompt. Returns key -> parsed JSON for every item
        that came back parseable; whatever is missing is the caller's to send through
        `generate_json_response`. Items with identical prompts share one request. Results are
        keyed by content, so an interrupted job's output is reused on the next run and a
        result that lands after the deadline meets an existing key. Every failure of the
        batch machinery itself is logged and leaves the open items to the standard path.
        """
        if not self.batch_enabled or not items:
            return {}

        job_key = self._batch_job_key(model, json_schema, thinking_level, items)
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
            results.update(await self._read_batch_results(out_prefix, prompt_to_keys, model, request_context_log, known={}))
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

        gen_config = self._build_generation_config(json_schema, model, thinking_level)
        lines = [self._batch_request_line(prompt, gen_config) for prompt in missing_prompts]
        src_path = f"{job_prefix}requests-{int(time.time())}.jsonl"
        bucket = self.gcs_client.bucket.name
        try:
            await self.gcs_client.upload_from_string_async("\n".join(lines) + "\n", src_path, content_type="application/json")
            job = await self._retry_admin(
                lambda: self.client.aio.batches.create(
                    model=model,
                    src=f"gs://{bucket}/{src_path}",
                    config=types.CreateBatchJobConfig(dest=f"gs://{bucket}/{out_prefix}", display_name=f"audit-{job_key}"),
                ),
                "batches.create",
            )
        except Exception as e:  # noqa: BLE001 — the standard path takes over
            logging.warning(f"[{request_context_log}] batch job not created ({_describe(e)}); {len(missing_prompts)} request(s) run as standard calls.")
            return results

        logging.info(f"[{request_context_log}] batch job {job.name} submitted: {len(missing_prompts)} request(s) on '{model}', deadline {BATCH_DEADLINE_S}s.")
        try:
            state, deadline_hit = await self._wait_for_batch(job.name, request_context_log)
            delivered = await self._read_batch_results(out_prefix, prompt_to_keys, model, request_context_log, known=results)
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
