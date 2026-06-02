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


def test_extract_json_allows_max_tokens_finish_reason():
    # MAX_TOKENS is treated as acceptable (truncation still parsed if valid JSON).
    result = AiClient._extract_json(_fake_response(finish_reason="MAX_TOKENS", text='{"a": 1}'))
    assert result == {"a": 1}


def test_extract_json_raises_when_no_candidates():
    with pytest.raises(ValueError, match="no candidates"):
        AiClient._extract_json(_fake_response(candidates=False))


def test_extract_json_raises_on_bad_finish_reason():
    with pytest.raises(ValueError, match="non-OK reason"):
        AiClient._extract_json(_fake_response(finish_reason="SAFETY"))


def test_extract_json_raises_on_malformed_json():
    with pytest.raises(ValueError, match="Failed to parse"):
        AiClient._extract_json(_fake_response(text='{"a": 1,'))  # truncated/invalid
