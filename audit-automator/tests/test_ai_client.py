"""Tests for AiClient: response parsing, generation config, retry policy, caching and batch.

Everything here is offline: the pieces under test are staticmethods or need only
`self.system_message`, so an instance created via `__new__` exercises them without a
Vertex client, credentials or network. Module constants that come from the environment
(THINKING_LEVEL, BATCH_MIN_ITEMS) are monkeypatched, so a developer's shell cannot change
the outcome. Importing ai_client needs google-genai + jsonschema (both in requirements);
skip cleanly if the SDK is absent.
"""
import asyncio
import json
import types
from collections import defaultdict

import pytest

pytest.importorskip("google.genai")

import httpx
from google.genai import errors as genai_errors

from src.clients import ai_client as ai_client_module
from src.clients.ai_client import AiClient, _is_transient, _BATCH_TERMINAL_STATES


def _fake_response(*, candidates=True, finish_reason="STOP", text='{"ok": true}'):
    """Build a minimal stand-in for the google-genai response object."""
    if not candidates:
        return types.SimpleNamespace(candidates=[], text=text)
    candidate = types.SimpleNamespace(finish_reason=types.SimpleNamespace(name=finish_reason))
    return types.SimpleNamespace(candidates=[candidate], text=text)


def test_extract_json_parses_valid_stop_response():
    result = AiClient._extract_json(_fake_response(text='{"a": 1, "b": [2, 3]}'))
    assert result == {"a": 1, "b": [2, 3]}


def test_extract_json_rejects_max_tokens_even_when_the_text_parses():
    """A truncated answer that happens to be valid JSON is still incomplete; it must
    be retried, not accepted into the report as a full answer."""
    with pytest.raises(ValueError, match="MAX_TOKENS"):
        AiClient._extract_json(_fake_response(finish_reason="MAX_TOKENS", text='{"a": 1}'))


def test_extract_json_raises_when_no_candidates():
    with pytest.raises(ValueError, match="no candidates"):
        AiClient._extract_json(_fake_response(candidates=False))


def test_extract_json_raises_on_bad_finish_reason():
    with pytest.raises(ValueError, match="non-OK reason"):
        AiClient._extract_json(_fake_response(finish_reason="SAFETY"))


def test_extract_json_raises_valueerror_when_the_response_has_no_text():
    """An all-thinking candidate has text=None. json.loads(None) would raise
    TypeError, which used to escape the retry loop and kill the stage."""
    with pytest.raises(ValueError, match="no text part"):
        AiClient._extract_json(_fake_response(text=None))


def test_extract_json_survives_a_missing_finish_reason():
    response = types.SimpleNamespace(candidates=[types.SimpleNamespace(finish_reason=None)], text='{"a": 1}')
    with pytest.raises(ValueError, match="non-OK reason"):
        AiClient._extract_json(response)


def test_extract_json_raises_on_malformed_json():
    with pytest.raises(ValueError, match="Failed to parse"):
        AiClient._extract_json(_fake_response(text='{"a": 1,'))  # truncated/invalid


# --- Schema validation of replies ---------------------------------------------------
# Constrained decoding makes a mismatch rare, but an unvalidated reply reaches the
# report, where a missing key becomes a silently wrong audit statement.

QUESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "answers": {"type": "array", "items": {"type": "boolean"}, "minItems": 1},
        "finding": {"type": "object"},
    },
    "required": ["answers", "finding"],
}


def test_valid_payload_passes_validation():
    AiClient._validate_against_schema({"answers": [True], "finding": {}}, QUESTION_SCHEMA, "test")


def test_missing_required_key_raises_valueerror_so_the_retry_loop_catches_it():
    with pytest.raises(ValueError, match="does not match the requested schema"):
        AiClient._validate_against_schema({"answers": [True]}, QUESTION_SCHEMA, "test")


def test_wrong_type_reports_the_offending_path():
    with pytest.raises(ValueError, match="answers"):
        AiClient._validate_against_schema({"answers": ["ja"], "finding": {}}, QUESTION_SCHEMA, "test")


def test_a_broken_schema_asset_raises_rather_than_passing_silently():
    with pytest.raises(ValueError, match="Invalid JSON schema"):
        AiClient._validate_against_schema({}, {"type": "not-a-real-type"}, "test")


# --- Generation config -------------------------------------------------------------
# _build_generation_config needs only self.system_message, so an uninitialised instance
# exercises it without a Vertex client, credentials or prompt assets.

SCHEMA = {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object"}


def _client():
    client = AiClient.__new__(AiClient)
    client.base_system_message = "Du bist ein BSI-Auditor."
    client.system_message = "Du bist ein BSI-Auditor.\n\nImportant: Today's date is 2026-10-13."
    client.config = types.SimpleNamespace(is_test_mode=False)
    client.cache_enabled = False
    client._caches = {}
    client._cache_locks = defaultdict(asyncio.Lock)
    client.semaphore = asyncio.Semaphore(1)
    client.usage_totals = defaultdict(int)
    client.batch_enabled = False
    client.gcs_client = None
    return client


def test_generation_config_uses_temperature_one():
    """Gemini 3.x expects temperature 1; lower values degrade reasoning quality."""
    config = _client()._build_generation_config(SCHEMA, "gemini-3.8-flash")
    assert config.temperature == 1


def test_generation_config_strips_meta_schema_key():
    config = _client()._build_generation_config(SCHEMA, "gemini-3.8-flash")
    sent_schema = config.response_json_schema or config.response_schema
    assert "$schema" not in sent_schema
    # The caller's schema must not be mutated.
    assert "$schema" in SCHEMA


def test_generation_config_uses_the_default_thinking_level(monkeypatch):
    """Without a per-call level the module default (THINKING_LEVEL) applies."""
    monkeypatch.setattr(ai_client_module, "THINKING_LEVEL", "medium")
    config = _client()._build_generation_config(SCHEMA, "gemini-3.8-flash")
    assert config.thinking_config.thinking_level.value == "MEDIUM"


def test_generation_config_takes_a_per_call_thinking_level():
    config = _client()._build_generation_config(SCHEMA, "gemini-3.8-flash", thinking_level="low")
    assert config.thinking_config.thinking_level.value == "LOW"


def test_minimal_thinking_is_clamped_where_the_model_rejects_it():
    """3.7+/3.8 Flash and the pro tier return a 400 for 'minimal'; 3.5/3.6 Flash accept it."""
    assert AiClient._resolve_thinking_level("gemini-3.8-flash", "minimal") == "low"
    assert AiClient._resolve_thinking_level("gemini-3.7-flash", "minimal") == "low"
    assert AiClient._resolve_thinking_level("gemini-3.1-pro-preview", "minimal") == "low"
    assert AiClient._resolve_thinking_level("gemini-3.5-flash", "minimal") == "minimal"
    assert AiClient._resolve_thinking_level("gemini-3.5-flash-lite", "minimal") == "minimal"
    assert AiClient._resolve_thinking_level("gemini-3.8-flash", "high") == "high"
    config = _client()._build_generation_config(SCHEMA, "gemini-3.8-flash", thinking_level="minimal")
    assert config.thinking_config.thinking_level.value == "LOW"


def test_cached_content_replaces_the_system_instruction():
    """With a context cache the persona lives in the cache; sending it again is a 400."""
    inline = _client()._build_generation_config(SCHEMA, "gemini-3.8-flash")
    assert inline.system_instruction.startswith("Du bist ein BSI-Auditor.")
    assert inline.cached_content is None

    cached = _client()._build_generation_config(SCHEMA, "gemini-3.8-flash", cached_content="projects/p/locations/global/cachedContents/1")
    assert cached.cached_content == "projects/p/locations/global/cachedContents/1"
    assert cached.system_instruction is None


# --- Retry policy ------------------------------------------------------------------

def _api_error(code, message="x"):
    return genai_errors.APIError(code, {"error": {"message": message, "status": "S"}})


def _fake_vertex(generate_content):
    return types.SimpleNamespace(aio=types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate_content)))


def test_only_transient_failures_are_retried():
    assert _is_transient(_api_error(429))
    assert _is_transient(_api_error(503))
    assert _is_transient(asyncio.TimeoutError())
    assert _is_transient(httpx.ReadTimeout(""))  # str() is empty: matched by type, not message
    assert _is_transient(httpx.ConnectError(""))
    assert _is_transient(RuntimeError("connection reset by peer"))
    for permanent in (400, 401, 403, 404):
        assert not _is_transient(_api_error(permanent)), permanent
    assert not _is_transient(RuntimeError("invalid schema"))


def test_permanent_api_error_raises_without_retrying():
    """A 404 (unknown model) must surface at once instead of burning five attempts."""
    client = _client()
    calls = []

    async def fake_generate_content(**kwargs):
        calls.append(kwargs)
        raise _api_error(404)

    client.client = _fake_vertex(fake_generate_content)
    with pytest.raises(genai_errors.APIError):
        asyncio.run(client.generate_json_response("p", SCHEMA, request_context_log="t"))
    assert len(calls) == 1


def test_transient_error_is_retried_then_succeeds(monkeypatch):
    client = _client()
    calls = []

    async def fake_sleep(_):
        return None

    async def fake_generate_content(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise httpx.ReadTimeout("")
        return _fake_response(text='{"ok": 1}')

    monkeypatch.setattr(ai_client_module.asyncio, "sleep", fake_sleep)
    client.client = _fake_vertex(fake_generate_content)
    assert asyncio.run(client.generate_json_response("p", SCHEMA, request_context_log="t")) == {"ok": 1}
    assert len(calls) == 2


# --- Context caching ---------------------------------------------------------------

def test_cache_key_is_order_independent_and_model_bound():
    client = _client()
    a = client._cache_key("gemini-3.8-flash", ["gs://b/2.pdf", "gs://b/1.pdf"])
    b = client._cache_key("gemini-3.8-flash", ["gs://b/1.pdf", "gs://b/2.pdf", "gs://b/1.pdf"])
    assert a == b
    assert client._cache_key("gemini-3.1-pro-preview", ["gs://b/1.pdf", "gs://b/2.pdf"]) != a
    assert client._cache_key("gemini-3.8-flash", ["gs://b/1.pdf"]) != a


def _caching_client(create):
    client = _client()
    client.cache_enabled = True

    async def fake_find(display_name):
        return None

    client._find_cache = fake_find
    client._create_cache = create
    return client


def test_cache_is_created_once_per_document_set_and_reused():
    """Parallel calls on the same set share one cache; a different set gets its own."""
    created = []

    async def fake_create(model, uris, display_name):
        created.append((model, tuple(uris)))
        name = f"cache-{len(created)}"
        await asyncio.sleep(0)  # yield, so the concurrent callers really interleave
        return name

    client = _caching_client(fake_create)

    async def scenario():
        same = ["gs://b/1.pdf", "gs://b/2.pdf"]
        return await asyncio.gather(
            client._get_cached_content("m", same),
            client._get_cached_content("m", list(reversed(same))),
            client._get_cached_content("m", ["gs://b/3.pdf"]),
            client._get_cached_content("m", []),
        )

    names = asyncio.run(scenario())
    assert names[0] == names[1] == "cache-1"
    assert names[2] == "cache-2"
    assert names[3] is None  # nothing to cache
    assert len(created) == 2


def test_permanent_cache_failure_is_remembered_as_inline():
    attempts = []

    async def fake_create(model, uris, display_name):
        attempts.append(1)
        raise _api_error(400, "Cached content is too small")  # below the 4,096-token minimum

    client = _caching_client(fake_create)

    async def scenario():
        return (await client._get_cached_content("m", ["gs://b/small.pdf"]),
                await client._get_cached_content("m", ["gs://b/small.pdf"]))

    assert asyncio.run(scenario()) == (None, None)
    assert len(attempts) == 1


def test_transient_cache_failure_is_retried_on_the_next_call():
    attempts = []

    async def fake_create(model, uris, display_name):
        attempts.append(1)
        if len(attempts) == 1:
            raise _api_error(503)
        return "cache-ok"

    client = _caching_client(fake_create)

    async def scenario():
        return (await client._get_cached_content("m", ["gs://b/x.pdf"]),
                await client._get_cached_content("m", ["gs://b/x.pdf"]))

    assert asyncio.run(scenario()) == (None, "cache-ok")
    assert len(attempts) == 2


def test_rejected_cache_is_replaced_by_inline_documents_and_memoised():
    """An expired/rejected cache costs one inline retry; afterwards the set stays inline."""
    client = _client()
    client.cache_enabled = True
    uris = ["gs://b/1.pdf"]
    key = client._cache_key("gemini-3.8-flash", uris)
    client._caches[key] = "cache-x"
    calls = []

    async def fake_generate_content(**kwargs):
        calls.append(kwargs)
        if kwargs["config"].cached_content:
            raise _api_error(404, "CachedContent not found")
        return _fake_response(text='{"ok": 1}')

    client.client = _fake_vertex(fake_generate_content)
    result = asyncio.run(client.generate_json_response("p", SCHEMA, gcs_uris=uris, model_override="gemini-3.8-flash", request_context_log="t"))
    assert result == {"ok": 1}
    assert calls[0]["config"].cached_content == "cache-x" and len(calls[0]["contents"]) == 1
    assert calls[1]["config"].cached_content is None and calls[1]["config"].system_instruction
    assert len(calls[1]["contents"]) == 2  # prompt + the PDF part
    assert client._caches[key] is None


def test_unrelated_400_does_not_touch_the_cache():
    client = _client()
    client.cache_enabled = True
    uris = ["gs://b/1.pdf"]
    key = client._cache_key("gemini-3.8-flash", uris)
    client._caches[key] = "cache-x"
    calls = []

    async def fake_generate_content(**kwargs):
        calls.append(kwargs)
        raise _api_error(400, "Invalid JSON schema")

    client.client = _fake_vertex(fake_generate_content)
    with pytest.raises(genai_errors.APIError):
        asyncio.run(client.generate_json_response("p", SCHEMA, gcs_uris=uris, model_override="gemini-3.8-flash"))
    assert len(calls) == 1
    assert client._caches[key] == "cache-x"


# --- Batch tier --------------------------------------------------------------------

def test_paused_is_not_a_terminal_batch_state():
    """A paused job can resume and bill later, so it must be awaited and cancelled, not forgotten."""
    assert "JOB_STATE_PAUSED" not in _BATCH_TERMINAL_STATES
    assert "JOB_STATE_SUCCEEDED" in _BATCH_TERMINAL_STATES


def test_batch_job_key_ignores_the_date_in_the_persona():
    a, b = _client(), _client()
    b.system_message = b.base_system_message + "\n\nImportant: Today's date is 2026-10-14."
    items = {"k": "p"}
    assert a._batch_job_key("m", SCHEMA, "low", items) == b._batch_job_key("m", SCHEMA, "low", items)
    assert a._batch_job_key("m", SCHEMA, "high", items) != a._batch_job_key("m", SCHEMA, "low", items)


def test_batch_request_line_uses_the_vertex_jsonl_format():
    client = _client()
    config = client._build_generation_config(SCHEMA, "gemini-3.8-flash", thinking_level="low")
    line = json.loads(AiClient._batch_request_line("Frage?", config))
    request = line["request"]
    assert request["contents"] == [{"role": "user", "parts": [{"text": "Frage?"}]}]
    assert request["systemInstruction"]["parts"][0]["text"].startswith("Du bist ein BSI-Auditor.")
    gen = request["generationConfig"]
    assert "systemInstruction" not in gen
    assert gen["responseMimeType"] == "application/json"
    assert gen["temperature"] == 1
    assert gen["thinkingConfig"] == {"thinkingLevel": "LOW"}
    assert "responseJsonSchema" in gen or "responseSchema" in gen


def _output_line(prompt, *, text=None, status="", finish="STOP", parts=None):
    response = None
    if text is not None or parts is not None:
        response = {
            "candidates": [{"content": {"role": "model", "parts": parts or [{"text": text}]}, "finishReason": finish}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
        }
    record = {"status": status, "processed_time": "t", "request": {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}}
    if response is not None:
        record["response"] = response
    return json.dumps(record)


def test_parse_batch_output_maps_lines_back_to_keys():
    prompt_to_keys = {"P1": ["A#0"], "P2": ["A#1"], "P3": ["B#0"], "P4": ["C#0"], "P5": ["D#0"], "P6": ["E#0", "F#0"]}
    lines = [
        _output_line("P1", text='{"anforderungen": [1]}'),
        _output_line("P2", status="Internal error"),
        _output_line("P3", text='{"anforderungen": ['),  # truncated JSON
        _output_line("P4", parts=[{"text": "thinking...", "thought": True}, {"text": '{"anforderungen": []}'}]),
        _output_line("P6", text='{"anforderungen": [{"id": "x"}]}'),  # one request, two items
        _output_line("unknown prompt", text='{"x": 1}'),
        "not json at all",
        "",
        _output_line("P1", text='{"anforderungen": [999]}'),  # duplicate: first wins
    ]
    ok, errors, usage = AiClient._parse_batch_output_lines(lines, prompt_to_keys)
    assert ok["A#0"] == {"anforderungen": [1]}
    assert ok["C#0"] == {"anforderungen": []}
    assert ok["E#0"] == ok["F#0"] == {"anforderungen": [{"id": "x"}]}
    assert ok["E#0"] is not ok["F#0"]  # consumers mutate results: no aliasing between items
    assert errors["A#1"] == "Internal error"
    assert errors["B#0"].startswith("JSON parsing error")
    assert "D#0" not in ok and "D#0" not in errors  # never delivered: the caller reruns it
    assert set(usage) == {"A#0", "C#0", "E#0"}  # one usage entry per billed line
    unmatched = [v for k, v in errors.items() if k.startswith("?")]
    assert len(unmatched) == 2


class _FakeGcs:
    bucket = types.SimpleNamespace(name="bkt")

    def __init__(self, blobs=None):
        self.blobs = dict(blobs or {})
        self.uploads = []

    async def list_blob_names_async(self, prefix):
        return [n for n in self.blobs if n.startswith(prefix)]

    async def read_text_file_async(self, name):
        return self.blobs[name]

    async def upload_from_string_async(self, content, destination_blob_name, content_type="application/json"):
        self.uploads.append((destination_blob_name, content))


def _batch_client(monkeypatch, blobs=None, min_items=5):
    monkeypatch.setattr(ai_client_module, "BATCH_MIN_ITEMS", min_items)
    client = _client()
    client.batch_enabled = True
    client.gcs_client = _FakeGcs(blobs)
    client._batch_job_key = lambda *a, **k: "x"
    return client


def test_batch_skips_small_jobs_and_disabled_tier(monkeypatch):
    client = _batch_client(monkeypatch)
    created = []

    async def fake_create(**kwargs):
        created.append(kwargs)

    client.client = types.SimpleNamespace(aio=types.SimpleNamespace(batches=types.SimpleNamespace(create=fake_create)))
    assert asyncio.run(client.generate_json_batch({"k": "p"}, SCHEMA, "gemini-3.8-flash")) == {}  # 1 < 5
    client.batch_enabled = False
    assert asyncio.run(client.generate_json_batch({f"k{i}": f"p{i}" for i in range(9)}, SCHEMA, "gemini-3.8-flash")) == {}
    assert created == []


def test_parse_batch_output_rejects_truncated_answers():
    """MAX_TOKENS is a truncated answer: the standard path rejects it, the batch path must too."""
    ok, errors, _ = AiClient._parse_batch_output_lines(
        [_output_line("P1", text='{"anforderungen": []}', finish="MAX_TOKENS")], {"P1": ["A#0"]}
    )
    assert ok == {}
    assert errors["A#0"] == "finish reason MAX_TOKENS"


def test_resumed_batch_result_that_fails_the_schema_stays_open(monkeypatch):
    """A delivered answer is validated like a standard reply; a mismatch is left to the standard path."""
    strict = {"type": "object", "properties": {"anforderungen": {"type": "array"}},
              "required": ["anforderungen"], "additionalProperties": False}
    blobs = {"output/temp/batch/x/out/old/predictions.jsonl": "\n".join([
        _output_line("P0", text='{"anforderungen": [0]}'),
        _output_line("P1", text='{"wrong": "shape"}'),
    ])}
    client = _batch_client(monkeypatch, blobs)
    client.client = types.SimpleNamespace(aio=types.SimpleNamespace(batches=types.SimpleNamespace()))
    results = asyncio.run(client.generate_json_batch({"K0": "P0", "K1": "P1"}, strict, "gemini-3.8-flash"))
    assert results == {"K0": {"anforderungen": [0]}}  # K1 is open: 1 < BATCH_MIN_ITEMS, no job created
    assert client.usage_totals["batch_calls"] == 1  # only the usable line is counted


def test_small_remainder_still_reuses_resumed_results(monkeypatch):
    """The resume read comes before the minimum-size check, so delivered work is never repaid."""
    blobs = {"output/temp/batch/x/out/old/predictions.jsonl": "\n".join(
        _output_line(f"P{i}", text=f'{{"anforderungen": [{i}]}}') for i in range(4))}
    client = _batch_client(monkeypatch, blobs)
    created = []

    async def fake_create(**kwargs):
        created.append(kwargs)

    client.client = types.SimpleNamespace(aio=types.SimpleNamespace(batches=types.SimpleNamespace(create=fake_create)))
    items = {f"K{i}": f"P{i}" for i in range(6)}
    results = asyncio.run(client.generate_json_batch(items, SCHEMA, "gemini-3.8-flash"))
    assert set(results) == {"K0", "K1", "K2", "K3"}
    assert created == []  # 2 open < 5: the rest goes standard, but nothing already paid is lost
    assert client.usage_totals["batch_calls"] == 4


def test_batch_reads_resumed_results_and_reruns_only_the_rest(monkeypatch):
    """A job that delivers part of the work within the deadline leaves the rest to standard calls."""
    client = _batch_client(monkeypatch, {"output/temp/batch/x/out/old/predictions.jsonl": _output_line("P0", text='{"anforderungen": [0]}')})
    items = {f"K{i}": f"P{i}" for i in range(6)}
    items["K6"] = "P5"  # identical prompt: shares K5's request
    submitted = {}

    async def fake_create(**kwargs):
        submitted.update(kwargs)
        # The job delivers two more items before it is read back.
        client.gcs_client.blobs["output/temp/batch/x/out/prediction-model-1/predictions.jsonl"] = "\n".join([
            _output_line("P1", text='{"anforderungen": [1]}'),
            _output_line("P2", status="error"),
            _output_line("P5", text='{"anforderungen": [5]}'),
        ])
        return types.SimpleNamespace(name="batches/1")

    client.client = types.SimpleNamespace(aio=types.SimpleNamespace(batches=types.SimpleNamespace(create=fake_create)))

    async def fake_wait(name, log):
        return "JOB_STATE_CANCELLED", True

    client._wait_for_batch = fake_wait

    results = asyncio.run(client.generate_json_batch(items, SCHEMA, "gemini-3.8-flash", "low", "t"))
    assert results == {"K0": {"anforderungen": [0]}, "K1": {"anforderungen": [1]},
                       "K5": {"anforderungen": [5]}, "K6": {"anforderungen": [5]}}
    # Only the prompts without a resumed result were submitted, each once.
    path, content = client.gcs_client.uploads[0]
    assert path.startswith("output/temp/batch/x/requests-")
    sent_prompts = [json.loads(l)["request"]["contents"][0]["parts"][0]["text"] for l in content.strip().splitlines()]
    assert sent_prompts == ["P1", "P2", "P3", "P4", "P5"]
    assert submitted["src"] == f"gs://bkt/{path}"
    # Usage: 1 resumed line + 2 delivered lines, the resumed one not counted twice.
    assert client.usage_totals["batch_calls"] == 3


def test_batch_wait_failure_cancels_the_job_and_falls_back(monkeypatch):
    client = _batch_client(monkeypatch)
    cancelled = []

    async def fake_create(**kwargs):
        return types.SimpleNamespace(name="batches/1")

    async def fake_cancel(name):
        cancelled.append(name)

    client.client = types.SimpleNamespace(aio=types.SimpleNamespace(batches=types.SimpleNamespace(create=fake_create, cancel=fake_cancel)))

    async def fake_wait(name, log):
        raise _api_error(403, "permission denied on batches.get")

    client._wait_for_batch = fake_wait
    items = {f"K{i}": f"P{i}" for i in range(6)}
    assert asyncio.run(client.generate_json_batch(items, SCHEMA, "gemini-3.8-flash")) == {}
    assert cancelled == ["batches/1"]
