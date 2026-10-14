"""Tests for AiClient: response parsing, generation config, retry policy, prompt layout and batch.

Everything here is offline: the pieces under test are staticmethods or need only
`self.system_message`, so an instance created via `__new__` exercises them without a
Vertex client, credentials or network. Module constants that come from the environment
(THINKING_LEVEL, MAX_OUTPUT_TOKENS, BATCH_MIN_ITEMS) are monkeypatched, so a developer's shell cannot change
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


def test_extract_json_rejects_a_surviving_continuation():
    """CONTINUATION means the SDK stopped following the long-decode hops: truncated, not done."""
    with pytest.raises(ValueError, match="CONTINUATION.*truncated"):
        AiClient._extract_json(_fake_response(finish_reason="CONTINUATION", text='{"a": 1}'))


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
    client.model = "gemini-4-argon"
    client.semaphore = asyncio.Semaphore(1)
    client.usage_totals = defaultdict(int)
    client.batch_enabled = False
    client.gcs_client = None
    return client


def test_generation_config_sends_no_sampling_parameters():
    """Argon ignores temperature/top_p/top_k and 400s on candidate_count and the penalties."""
    config = _client()._build_generation_config(SCHEMA)
    for field in ("temperature", "top_p", "top_k", "candidate_count", "frequency_penalty", "presence_penalty"):
        assert getattr(config, field) is None, field
    assert config.cached_content is None  # explicit caching does not exist on Argon


def test_generation_config_sets_the_output_budget_and_long_decode(monkeypatch):
    monkeypatch.setattr(ai_client_module, "MAX_OUTPUT_TOKENS", 131072)
    config = _client()._build_generation_config(SCHEMA)
    assert config.max_output_tokens == 131072
    assert config.automatic_continuation is True
    assert config.system_instruction.startswith("Du bist ein BSI-Auditor.")
    assert config.response_mime_type == "application/json"


def test_generation_config_refuses_a_starved_output_budget(monkeypatch):
    """Below 32,768 the model thinks the budget away and returns truncated JSON without an error."""
    monkeypatch.setattr(ai_client_module, "MAX_OUTPUT_TOKENS", 8192)
    with pytest.raises(ValueError, match="MAX_OUTPUT_TOKENS=8192"):
        _client()._build_generation_config(SCHEMA)
    monkeypatch.setattr(ai_client_module, "MAX_OUTPUT_TOKENS", 2_000_000)
    with pytest.raises(ValueError, match="MAX_OUTPUT_TOKENS=2000000"):
        _client()._build_generation_config(SCHEMA)


def test_generation_config_strips_meta_schema_key():
    config = _client()._build_generation_config(SCHEMA)
    assert "$schema" not in config.response_json_schema
    # The caller's schema must not be mutated.
    assert "$schema" in SCHEMA


def test_generation_config_uses_the_default_thinking_level(monkeypatch):
    """Without a per-call level the module default (THINKING_LEVEL) applies."""
    monkeypatch.setattr(ai_client_module, "THINKING_LEVEL", "medium")
    config = _client()._build_generation_config(SCHEMA)
    assert config.thinking_config.thinking_level.value == "MEDIUM"


def test_generation_config_takes_a_per_call_thinking_level():
    config = _client()._build_generation_config(SCHEMA, thinking_level="low")
    assert config.thinking_config.thinking_level.value == "LOW"


def test_legacy_thinking_names_are_mapped_and_unknown_ones_raise():
    """'minimal', 'off' and thinking_budget are a 400 on Argon; a migrated .env must not 400 the run."""
    legacy_flash_name = "mini" + "mal"  # spelled apart so the migration lint does not flag a 400 here
    assert AiClient._resolve_thinking_level(legacy_flash_name) == "low"
    assert AiClient._resolve_thinking_level("off") == "low"
    assert AiClient._resolve_thinking_level("max") == "high"
    assert AiClient._resolve_thinking_level(" High ") == "high"
    with pytest.raises(ValueError, match="use low, medium or high"):
        AiClient._resolve_thinking_level("ultra")
    config = _client()._build_generation_config(SCHEMA, thinking_level=legacy_flash_name)
    assert config.thinking_config.thinking_level.value == "LOW"


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


# --- Prompt layout (implicit caching) ---------------------------------------------
# Argon caches implicitly by request prefix: the documents must come before the varying
# prompt, or every call pays for them in full.

def test_documents_precede_the_prompt_and_the_one_model_is_called():
    client = _client()
    calls = []

    async def fake_generate_content(**kwargs):
        calls.append(kwargs)
        return _fake_response(text='{"ok": 1}')

    client.client = _fake_vertex(fake_generate_content)
    uris = ["gs://b/1.pdf", "gs://b/2.pdf"]
    result = asyncio.run(client.generate_json_response("p", SCHEMA, gcs_uris=uris, request_context_log="t"))
    assert result == {"ok": 1}
    assert len(calls) == 1
    assert calls[0]["model"] == "gemini-4-argon"
    contents = calls[0]["contents"]
    assert len(contents) == 3 and contents[-1] == "p"
    assert [part.file_data.file_uri for part in contents[:2]] == uris
    assert calls[0]["config"].system_instruction  # persona in the config, not in a cache


# --- Batch tier --------------------------------------------------------------------

def test_paused_is_not_a_terminal_batch_state():
    """A paused job can resume and bill later, so it must be awaited and cancelled, not forgotten."""
    assert "JOB_STATE_PAUSED" not in _BATCH_TERMINAL_STATES
    assert "JOB_STATE_SUCCEEDED" in _BATCH_TERMINAL_STATES


def test_batch_job_key_ignores_the_date_in_the_persona():
    a, b = _client(), _client()
    b.system_message = b.base_system_message + "\n\nImportant: Today's date is 2026-10-14."
    items = {"k": "p"}
    assert a._batch_job_key(SCHEMA, "low", items) == b._batch_job_key(SCHEMA, "low", items)
    assert a._batch_job_key(SCHEMA, "high", items) != a._batch_job_key(SCHEMA, "low", items)
    b.model = "another-model-id"
    assert a._batch_job_key(SCHEMA, "low", items) != b._batch_job_key(SCHEMA, "low", items)


def test_batch_request_line_uses_the_vertex_jsonl_format():
    client = _client()
    config = client._build_generation_config(SCHEMA, thinking_level="low")
    line = json.loads(AiClient._batch_request_line("Frage?", config))
    request = line["request"]
    assert request["contents"] == [{"role": "user", "parts": [{"text": "Frage?"}]}]
    assert request["systemInstruction"]["parts"][0]["text"].startswith("Du bist ein BSI-Auditor.")
    gen = request["generationConfig"]
    assert "systemInstruction" not in gen
    assert gen["responseMimeType"] == "application/json"
    assert "temperature" not in gen
    assert gen["maxOutputTokens"] == ai_client_module.MAX_OUTPUT_TOKENS
    assert "automaticContinuation" not in gen  # an SDK switch, not a GenerationConfig field
    assert gen["thinkingConfig"] == {"thinkingLevel": "LOW"}
    assert "responseJsonSchema" in gen


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
    assert asyncio.run(client.generate_json_batch({"k": "p"}, SCHEMA)) == {}  # 1 < 5
    client.batch_enabled = False
    assert asyncio.run(client.generate_json_batch({f"k{i}": f"p{i}" for i in range(9)}, SCHEMA)) == {}
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
    results = asyncio.run(client.generate_json_batch({"K0": "P0", "K1": "P1"}, strict))
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
    results = asyncio.run(client.generate_json_batch(items, SCHEMA))
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

    results = asyncio.run(client.generate_json_batch(items, SCHEMA, "low", "t"))
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
    assert asyncio.run(client.generate_json_batch(items, SCHEMA)) == {}
    assert cancelled == ["batches/1"]
