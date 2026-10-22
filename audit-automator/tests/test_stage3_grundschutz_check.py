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

from src.audit.stages.gs_extraction.anforderung_fields import STATUS_NEIN, STATUS_TEILWEISE
from src.audit.stages.stage_3_dokumentenpruefung import Chapter3Runner
from src.constants import PROMPT_CONFIG_PATH


def _anforderung(req_id, status, datum="2026-06-01", kuerzel="SVR-01", name="Server"):
    return {
        "id": req_id,
        "umsetzungsstatus": status,
        "datumLetztePruefung": datum,
        "zielobjekt_kuerzel": kuerzel,
        "zielobjekt_name": name,
        "umsetzungserlaeuterung": "x",
    }


def _findings(result):
    """The section's findings: one per deficiency, or the single OK finding."""
    return result.get("findings") or [result["finding"]]


def _text(result):
    return " | ".join(f["description"] for f in _findings(result))


class _FakeRagClient:
    def __init__(self, uris_by_category=None):
        self._uris = uris_by_category or {}

    def get_gcs_uris_for_categories(self, categories):
        return [u for c in (categories or []) for u in self._uris.get(c, [])]


class _FakeAiClient:
    """Answers every targeted question with 'yes, all good' and records the payloads."""

    def __init__(self):
        self.requests = []

    async def generate_checked_json_response(self, prompt, json_schema, gcs_uris=None, request_context_log="", thinking_level=None):
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
        is_known_control=lambda control_id: True,
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
    assert "Risikoanalyse" in _text(result)


def test_unparsable_date_does_not_fabricate_an_outdated_finding():
    result = _run(_runner([_anforderung("ORP.1.A3", "Ja", datum="Q1 2025")]))

    assert "mehr als 12 Monate" not in _text(result)
    assert "nicht auswertbar" in _text(result)
    assert result["answers"][4] is False  # actuality could not be confirmed


def test_null_date_does_not_crash_the_stage():
    result = _run(_runner([_anforderung("ORP.1.A4", "Ja", datum=None)]))
    assert result["answers"][4] is False


def test_genuinely_old_date_is_still_reported_as_outdated():
    result = _run(_runner([_anforderung("ORP.1.A5", "Ja", datum="01.03.2019")]))
    assert "mehr als 12 Monate" in _text(result)


def test_recent_dates_and_met_requirements_pass_cleanly():
    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    result = _run(_runner([_anforderung("ORP.1.A6", "ja", datum=today)]))

    assert result["finding"]["category"] == "OK"
    assert result["answers"][0] is True and result["answers"][4] is True


def test_unmet_muss_requirements_are_listed_in_code_without_a_model_call():
    """Q3 is a filter over catalog ids and statuses; the model used to be asked a question
    whose input was already the list of unmet items."""
    ai = _FakeAiClient()
    runner = _runner(
        [_anforderung("ISMS.1.A1", "nein"), _anforderung("ISMS.1.A2", "teilweise"), _anforderung("ISMS.1.A3", "Ja")],
        ai_client=ai,
    )
    runner.control_catalog.get_muss_control_ids = lambda: ["ISMS.1.A1", "ISMS.1.A2", "ISMS.1.A3"]

    result = _run(runner)

    assert result["answers"][2] is False
    assert not [r for r in ai.requests if "Q3" in r["log"]]
    desc = _text(result)
    # statuses are normalised to their canonical spelling before the check
    assert f"ISMS.1.A1 ({STATUS_NEIN})" in desc and f"ISMS.1.A2 ({STATUS_TEILWEISE})" in desc
    assert "ISMS.1.A3" not in desc


def test_all_muss_requirements_met_answers_q3_true():
    runner = _runner([_anforderung("ISMS.1.A1", "ja")])
    runner.control_catalog.get_muss_control_ids = lambda: ["ISMS.1.A1"]
    assert _run(runner)["answers"][2] is True


def test_requirements_outside_the_official_catalog_are_named_in_the_report():
    """Custom Bausteine are not in the Kompendium, so no MUSS check covers them.
    Losing them silently would shrink the audited scope."""
    runner = _runner([_anforderung("ORP.6.A1", "Ja"), _anforderung("ISMS.1.A1", "Ja")])
    runner.control_catalog.is_known_control = lambda control_id: control_id.startswith("ISMS.")

    result = _run(runner)

    assert [f["category"] for f in _findings(result)] == ["AS"]
    assert "ORP.6.A1" in _text(result)
    assert "ISMS.1.A1" not in _text(result)


# ----------------------------------------------------------------------------- 3.6.1 coverage and findings
def _runner_with_check(anforderungen, zielobjekte=None, kollisionen=None, ground_truth=None):
    runner = _runner(anforderungen)
    check_data = {"anforderungen": anforderungen}
    if zielobjekte is not None:
        check_data["zielobjekte"] = zielobjekte
    if kollisionen is not None:
        check_data["kuerzel_kollisionen"] = kollisionen
    runner.gcs_client = types.SimpleNamespace(read_json=lambda path: check_data)
    runner._ground_truth_map = ground_truth or {"zielobjekte": [], "baustein_to_zielobjekt_mapping": {}}
    return runner


FW_REGISTRY = [
    {"kuerzel": "Firewall FW-01", "name": "FW-01", "kuerzel_im_dokument": "Firewall", "aliases": ["Firewall", "FW-01"]},
    {"kuerzel": "Firewall FW-02", "name": "FW-02", "kuerzel_im_dokument": "Firewall", "aliases": ["Firewall", "FW-02"]},
]


def test_mapped_kuerzel_that_resolves_through_the_name_counts_as_covered():
    """The model's map says 'FW-01' (the name column of the swapped export); the sheet parser
    addresses that firewall as 'Firewall FW-01'. A plain set comparison reported it as missing."""
    anforderungen = [_anforderung("NET.3.2.A1", "Ja", kuerzel="Firewall FW-01", name="FW-01"),
                     _anforderung("NET.3.2.A1", "Ja", kuerzel="Firewall FW-02", name="FW-02")]
    ground_truth = {"zielobjekte": [{"kuerzel": "Firewall", "name": "FW-01"}, {"kuerzel": "Firewall", "name": "FW-02"}],
                    "baustein_to_zielobjekt_mapping": {"NET.3.2": ["FW-01", "FW-02"]}}
    result = _run(_runner_with_check(anforderungen, FW_REGISTRY, {"Firewall": ["FW-01", "FW-02"]}, ground_truth))
    assert not [f for f in _findings(result) if "keine Anforderungen" in f["description"]]
    # The shared Kürzel is a finding of its own (200-2 requires unique Kennungen), not a coverage gap.
    [collision] = [f for f in _findings(result) if "nicht eindeutig" in f["description"]]
    assert collision["category"] == "AG"
    assert "'Firewall' (2 Zielobjekte: FW-01, FW-02)" in collision["description"]
    assert "'Firewall FW-01'" in collision["description"]


def test_ambiguous_map_kuerzel_is_covered_when_any_candidate_has_requirements():
    registry = [
        {"kuerzel": "GP Cluster-A", "name": "Cluster-A", "kuerzel_im_dokument": "GP", "aliases": ["GP", "Cluster-A"]},
        {"kuerzel": "GP Portal", "name": "Portal", "kuerzel_im_dokument": "GP", "aliases": ["GP", "Portal"]},
    ]
    anforderungen = [_anforderung("NET.1.1.A1", "Ja", kuerzel="GP Cluster-A", name="Cluster-A")]
    ground_truth = {"zielobjekte": [{"kuerzel": "GP", "name": "Cluster-A"}, {"kuerzel": "GP", "name": "Portal"}],
                    "baustein_to_zielobjekt_mapping": {"NET.1.1": ["GP"]}}
    result = _run(_runner_with_check(anforderungen, registry, ground_truth=ground_truth))
    assert not [f for f in _findings(result) if "keine Anforderungen" in f["description"]]
    assert [f["category"] for f in _findings(result) if "nicht eindeutig" in f["description"]] == ["AG"]


def test_unresolvable_mapped_kuerzel_is_reported_as_missing_with_its_name():
    anforderungen = [_anforderung("NET.3.2.A1", "Ja", kuerzel="Firewall FW-01", name="FW-01")]
    ground_truth = {"zielobjekte": [{"kuerzel": "FW-ALL", "name": "Firewall-Gruppe"}, {"kuerzel": "Firewall", "name": "FW-01"}],
                    "baustein_to_zielobjekt_mapping": {"NET.3.2": ["FW-ALL", "FW-01"]}}
    result = _run(_runner_with_check(anforderungen, FW_REGISTRY[:1], ground_truth=ground_truth))
    [missing] = [f for f in _findings(result) if "keine Anforderungen" in f["description"]]
    assert missing["category"] == "AG"
    assert "FW-ALL (Firewall-Gruppe)" in missing["description"] and "FW-01" not in missing["description"].split(":")[1].split("FW-ALL")[0]


def test_without_a_registry_the_index_is_derived_from_the_requirements():
    anforderungen = [_anforderung("SYS.1.1.A1", "Ja", kuerzel="S001", name="Fileserver")]
    ground_truth = {"zielobjekte": [{"kuerzel": "SRV-01", "name": "Fileserver"}], "baustein_to_zielobjekt_mapping": {"SYS.1.1": ["SRV-01"]}}
    result = _run(_runner_with_check(anforderungen, ground_truth=ground_truth))
    assert result["finding"]["category"] == "OK"  # SRV-01 -> name Fileserver -> S001


def test_each_deficiency_is_its_own_finding_and_nothing_is_summarised():
    """Chapter 7.2 used to get one 'Zusammenfassung: a | b' row mixing unrelated deficiencies."""
    anforderungen = [_anforderung("ORP.1.A1", "unklar", datum="01.03.2019"), _anforderung("ORP.1.A2", "Ja", datum="Q1 2025")]
    result = _run(_runner_with_check(anforderungen))
    assert "finding" not in result
    descriptions = [f["description"] for f in result["findings"]]
    assert len(descriptions) == 3  # unknown status, outdated check, unassessable date
    assert not any("Zusammenfassung" in d or " | " in d for d in descriptions)
    assert all(f["category"] == "AG" for f in result["findings"])


def test_no_deficiency_yields_the_single_ok_finding():
    from datetime import datetime

    result = _run(_runner_with_check([_anforderung("ORP.1.A1", "Ja", datum=datetime.now().strftime("%Y-%m-%d"))]))
    assert "findings" not in result and result["finding"]["category"] == "OK"


def test_summary_prompts_see_list_findings_too():
    runner = _runner([])
    text = runner._get_findings_from_results([
        {"a": {"findings": [{"category": "AG", "description": "fehlt"}, {"category": "OK", "description": "ok"}]}},
        {"b": {"finding": {"category": "E", "description": "empfohlen"}}},
        {"c": {"finding": {"category": "OK", "description": "passt"}}},
    ])
    assert text == "- [AG]: fehlt\n- [E]: empfohlen"
