"""Tests for the maker/checker second pass in AiClient.

Offline: the Vertex call is replaced by a stub that returns queued responses, so the
whole verdict logic (approve / correct / reject-without-correction / checker crash)
is exercised without credentials or network.
"""

import asyncio
import json

import pytest

pytest.importorskip("google.genai")

from src.clients.ai_client import AiClient

TASK_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}

MAKER_ANSWER = {"answer": "Ja, die Leitlinie liegt vor."}
CORRECTION = {"answer": "Nein, die Leitlinie fehlt (Beleg: A.0, S. 3)."}


def _client(responses, enabled=True):
    """An AiClient whose generate_json_response replays `responses` in order."""
    client = AiClient.__new__(AiClient)
    client.system_message = "x"
    client.checker_enabled = enabled
    client.checker_prompt_template = "Prüfe:\n{original_prompt}\n---\n{antwort_json}"
    client.checker_log = []
    client.calls = []

    async def fake_generate(prompt, json_schema, gcs_uris=None, request_context_log="", model_override=None, max_retries=None):
        client.calls.append({"prompt": prompt, "schema": json_schema, "log": request_context_log})
        result = responses[len(client.calls) - 1]
        if isinstance(result, Exception):
            raise result
        return result

    client.generate_json_response = fake_generate
    return client


def _run(client, **kwargs):
    return asyncio.run(client.generate_checked_json_response(
        prompt="Liegt eine Sicherheitsleitlinie vor?",
        json_schema=TASK_SCHEMA,
        request_context_log="Chapter-3: leitlinie",
        **kwargs,
    ))


def test_disabled_checker_makes_a_single_call():
    client = _client([MAKER_ANSWER], enabled=False)
    assert _run(client) == MAKER_ANSWER
    assert len(client.calls) == 1
    assert client.checker_log == []


def test_approved_answer_is_passed_through():
    client = _client([MAKER_ANSWER, {"freigabe": True, "probleme": []}])
    assert _run(client) == MAKER_ANSWER
    assert len(client.calls) == 2
    entry = client.checker_log[0]
    assert entry["freigabe"] is True
    assert entry["korrektur_uebernommen"] is False


def test_rejected_answer_is_replaced_by_the_correction():
    verdict = {
        "freigabe": False,
        "probleme": ["Beleg fehlt", "Kategorie zu milde"],
        "korrigierte_antwort": CORRECTION,
    }
    client = _client([MAKER_ANSWER, verdict])
    assert _run(client) == CORRECTION
    entry = client.checker_log[0]
    assert entry["freigabe"] is False
    assert entry["korrektur_uebernommen"] is True
    assert entry["probleme"] == ["Beleg fehlt", "Kategorie zu milde"]


def test_rejection_without_correction_keeps_the_original():
    client = _client([MAKER_ANSWER, {"freigabe": False, "probleme": ["unbelegt"], "korrigierte_antwort": None}])
    assert _run(client) == MAKER_ANSWER
    assert client.checker_log[0]["korrektur_uebernommen"] is False


def test_schema_invalid_correction_is_rejected():
    """A correction that does not fit the task schema must not reach the report."""
    verdict = {"freigabe": False, "probleme": ["x"], "korrigierte_antwort": {"antwort": "falsches Feld"}}
    client = _client([MAKER_ANSWER, verdict])
    assert _run(client) == MAKER_ANSWER
    entry = client.checker_log[0]
    assert entry["korrektur_uebernommen"] is False
    assert any("schema-invalide" in p for p in entry["probleme"])


def test_checker_failure_fails_open():
    client = _client([MAKER_ANSWER, RuntimeError("Vertex 503")])
    assert _run(client) == MAKER_ANSWER
    entry = client.checker_log[0]
    assert entry["freigabe"] is None
    assert any("Checker-Aufruf fehlgeschlagen" in p for p in entry["probleme"])


def test_checker_prompt_carries_task_and_answer_without_format_errors():
    """Both the task prompt and the answer contain JSON braces; str.format would choke."""
    client = _client([MAKER_ANSWER, {"freigabe": True, "probleme": []}])
    asyncio.run(client.generate_checked_json_response(
        prompt='Prüfe diese Daten: {"id": "ISMS.1.A1", "level": "B"}',
        json_schema=TASK_SCHEMA,
        request_context_log="3.6.1-Q3",
    ))
    checker_prompt = client.calls[1]["prompt"]
    assert '{"id": "ISMS.1.A1", "level": "B"}' in checker_prompt
    assert MAKER_ANSWER["answer"] in checker_prompt
    assert "{original_prompt}" not in checker_prompt
    assert "{antwort_json}" not in checker_prompt


def test_checker_uses_the_checker_model_and_its_own_schema():
    client = _client([MAKER_ANSWER, {"freigabe": True, "probleme": []}])
    _run(client)
    checker_schema = client.calls[1]["schema"]
    assert set(checker_schema["required"]) == {"freigabe", "probleme"}
    # The correction is nullable via anyOf, carrying the task schema unchanged.
    correction = checker_schema["properties"]["korrigierte_antwort"]["anyOf"]
    assert {"type": "null"} in correction
    assert any(branch.get("properties", {}).get("answer") for branch in correction)
    # The meta-schema key is stripped so it can be embedded.
    assert all("$schema" not in branch for branch in correction)
    assert client.calls[1]["log"].startswith("Checker[")


def test_build_checker_schema_does_not_mutate_the_task_schema():
    before = json.dumps(TASK_SCHEMA, sort_keys=True)
    AiClient._build_checker_schema(TASK_SCHEMA)
    assert json.dumps(TASK_SCHEMA, sort_keys=True) == before
