"""Tests for AI-response parsing/error handling in AiClient._extract_json (MAX-9).

_extract_json is a staticmethod, so it is exercised directly with lightweight
fake response objects — no Vertex/network and no AiClient construction required.
Importing ai_client needs google-genai + jsonschema (both in requirements);
skip cleanly if the SDK is absent.
"""
import types

import pytest

pytest.importorskip("google.genai")

from src.clients.ai_client import AiClient


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
    client.system_message = "Du bist ein BSI-Auditor."
    return client


def test_generation_config_uses_temperature_one():
    """Gemini 3.x expects temperature 1; lower values degrade reasoning quality."""
    config = _client()._build_generation_config(SCHEMA, "gemini-3.7-flash")
    assert config.temperature == 1


def test_generation_config_strips_meta_schema_key():
    config = _client()._build_generation_config(SCHEMA, "gemini-3.7-flash")
    sent_schema = config.response_json_schema or config.response_schema
    assert "$schema" not in sent_schema
    # The caller's schema must not be mutated.
    assert "$schema" in SCHEMA


def test_generation_config_sets_thinking_level():
    config = _client()._build_generation_config(SCHEMA, "gemini-3.7-flash")
    assert config.thinking_config.thinking_level.value == "MINIMAL"


def test_pro_models_clamp_minimal_thinking_to_low():
    """The pro tier has no 'minimal' level, so it must be raised to 'low'."""
    config = _client()._build_generation_config(SCHEMA, "gemini-3.1-pro")
    assert config.thinking_config.thinking_level.value == "LOW"
    assert AiClient._resolve_thinking_level("gemini-3.7-flash") == "minimal"
    assert AiClient._resolve_thinking_level("gemini-3.1-pro") == "low"
