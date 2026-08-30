"""Tests for the deterministic 3.6.1 evaluation of the Grundschutz-Check.

This is where extraction noise turns into audit verdicts, so the two failure modes
that matter are:
  * a status in unexpected casing must not slip past the unmet/entbehrlich filters
    (the report would attest that all unmet requirements are documented while zero
    items were checked);
  * an unparsable Prüfdatum must not be treated as 1970 and produce a fabricated
    "checked more than 12 months ago" deviation.

The runner is built with __new__ and only the collaborators this method uses.
"""
import asyncio
import json
import types

import pytest

pytest.importorskip("google.genai")

from src.audit.stages.stage_3_dokumentenpruefung import Chapter3Runner
from src.constants import PROMPT_CONFIG_PATH


def _anforderung(req_id, status, datum="2026-06-01", kuerzel="SVR-01"):
    return {
        "id": req_id,
        "umsetzungsstatus": status,
        "datumLetztePruefung": datum,
        "zielobjekt_kuerzel": kuerzel,
        "umsetzungserlaeuterung": "x",
    }


class _FakeRagClient:
    def __init__(self, uris_by_category=None):
        self._uris = uris_by_category or {}

    def get_gcs_uris_for_categories(self, categories):
        return [u for c in (categories or []) for u in self._uris.get(c, [])]


class _FakeAiClient:
    """Answers every targeted question with 'yes, all good' and records the payloads."""

    def __init__(self):
        self.requests = []

    async def generate_checked_json_response(self, prompt, json_schema, gcs_uris=None, request_context_log=""):
        self.requests.append({"prompt": prompt, "log": request_context_log})
        return {"answers": [True], "finding": {"category": "OK", "description": "ok"}}


def _runner(anforderungen, rag_client=None, ai_client=None):
    runner = Chapter3Runner.__new__(Chapter3Runner)
    with open(PROMPT_CONFIG_PATH, encoding="utf-8") as f:
        runner.prompt_config = json.load(f)
    runner.gcs_client = types.SimpleNamespace(read_json=lambda path: {"anforderungen": anforderungen})
    runner.rag_client = rag_client or _FakeRagClient()
    runner.ai_client = ai_client or _FakeAiClient()
    runner.control_catalog = types.SimpleNamespace(
        get_control_level=lambda control_id: "B",
        get_muss_control_ids=lambda: [],
    )
    runner._ground_truth_map = {"zielobjekte": [], "baustein_to_zielobjekt_mapping": {}}
    return runner


def _run(runner):
    result = asyncio.run(runner._process_details_zum_it_grundschutz_check())
    return result["detailsZumItGrundschutzCheck"]


def test_lowercase_nein_is_still_an_unmet_requirement():
    """'nein' used to miss the ["Nein", "teilweise"] filter, so Q4 answered True
    with zero items checked."""
    ai = _FakeAiClient()
    rag = _FakeRagClient({"Realisierungsplan": ["gs://b/plan.pdf"]})
    runner = _runner([_anforderung("ORP.1.A1", "nein")], rag, ai)

    result = _run(runner)

    q4_requests = [r for r in ai.requests if "Q4" in r["log"]]
    assert q4_requests, "the unmet requirement never reached the Realisierungsplan check"
    assert "ORP.1.A1" in q4_requests[0]["prompt"]
    assert result["answers"][3] is True  # answered by the AI, not by an empty filter


def test_mixed_case_entbehrlich_still_triggers_the_plausibility_check():
    ai = _FakeAiClient()
    rag = _FakeRagClient({"Risikoanalyse": ["gs://b/risiko.pdf"]})
    runner = _runner([_anforderung("ORP.1.A2", "Entbehrlich")], rag, ai)

    _run(runner)

    assert [r for r in ai.requests if "Q2" in r["log"]], "the entbehrlich audit was skipped"


def test_entbehrlich_without_a_risikoanalyse_is_flagged_not_silently_passed():
    runner = _runner([_anforderung("ORP.1.A2", "entbehrlich")], _FakeRagClient())
    result = _run(runner)

    assert result["answers"][1] is False
    assert "Risikoanalyse" in result["finding"]["description"]


def test_unparsable_date_does_not_fabricate_an_outdated_finding():
    result = _run(_runner([_anforderung("ORP.1.A3", "Ja", datum="Q1 2025")]))

    assert "mehr als 12 Monate" not in result["finding"]["description"]
    assert "nicht auswertbar" in result["finding"]["description"]
    assert result["answers"][4] is False  # actuality could not be confirmed


def test_null_date_does_not_crash_the_stage():
    result = _run(_runner([_anforderung("ORP.1.A4", "Ja", datum=None)]))
    assert result["answers"][4] is False


def test_genuinely_old_date_is_still_reported_as_outdated():
    result = _run(_runner([_anforderung("ORP.1.A5", "Ja", datum="01.03.2019")]))
    assert "mehr als 12 Monate" in result["finding"]["description"]


def test_recent_dates_and_met_requirements_pass_cleanly():
    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    result = _run(_runner([_anforderung("ORP.1.A6", "ja", datum=today)]))

    assert result["finding"]["category"] == "OK"
    assert result["answers"][0] is True and result["answers"][4] is True
