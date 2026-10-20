"""Tests for chapter 5.6 (Risikoanalyse A.5) of the on-site checklist.

5.6.1 answers three of the Muster's questions from the audit plan (4.1.5) and the extracted
Grundschutz-Check: the additional measures of the Risikoanalyse are the A.4 requirements
outside the official Kompendium. 5.6.2 shows the customer's Umsetzungsstatus next to every
planned measure. Offline: the runner is built with __new__, the model and the document finder
are fakes, the catalog is the real one (so 'RA.1.A1' is unknown and 'ISMS.1.A1' is known).
"""
import asyncio
import json

import pytest

pytest.importorskip("google.cloud.storage")

from src.audit.stages.control_catalog import ControlCatalog
from src.audit.stages.gs_extraction.zielobjekt_index import ZielobjektIndex
from src.audit.stages.stage_5_vor_ort_audit import Chapter5Runner
from src.constants import PROMPT_CONFIG_PATH


class _FakeRagClient:
    def __init__(self, uris_by_category=None):
        self._uris = uris_by_category or {}

    def get_gcs_uris_for_categories(self, categories):
        return [u for c in (categories or []) for u in self._uris.get(c, [])]


class _FakeAiClient:
    def __init__(self, answer=True, finding=None):
        self.requests = []
        self._answer = answer
        self._finding = finding or {"category": "OK", "description": "ok"}

    async def generate_checked_json_response(self, prompt, json_schema, gcs_uris=None, request_context_log="", thinking_level=None):
        self.requests.append({"prompt": prompt, "uris": gcs_uris, "log": request_context_log})
        return {"answers": [self._answer], "finding": self._finding}


@pytest.fixture(scope="module")
def catalog():
    return ControlCatalog()


def _runner(catalog, rag_client=None, ai_client=None):
    runner = Chapter5Runner.__new__(Chapter5Runner)
    runner.control_catalog = catalog
    runner.rag_client = rag_client
    runner.ai_client = ai_client
    with open(PROMPT_CONFIG_PATH, encoding="utf-8") as f:
        runner.prompt_config = json.load(f)
    return runner


def _entry(control_id, kuerzel, status, titel="Zusätzliche Härtung", erlaeuterung="x"):
    return (control_id, kuerzel), {
        "id": control_id, "zielobjekt_kuerzel": kuerzel, "titel": titel,
        "umsetzungsstatus": status, "umsetzungserlaeuterung": erlaeuterung,
    }


def _chapter_4(*measures):
    rows = [{"Maßnahme": m, "Risikoanalyse": "RA 2026", "Zielobjekt": z, "Begründung zur Auswahl": "…"} for m, z in measures]
    return {"auswahlMassnahmenAusRisikoanalyse": {"rows": rows}}


def _zusammenfassung(runner, chapter_4, extracted):
    result = asyncio.run(runner._generate_risikoanalyse_zusammenfassung(chapter_4, extracted))
    return result["zusammenfassungDerRisikoanalyse"]


def _findings(block):
    return block.get("findings") or [block["finding"]]


# ---------------------------------------------------------------- 5.6.1
def test_all_additional_measures_implemented_answers_q1_and_q3_yes_without_a_model_call(catalog):
    ai = _FakeAiClient()
    runner = _runner(catalog, _FakeRagClient({"Realisierungsplan": ["gs://b/a6.pdf"]}), ai)
    extracted = dict([_entry("RA.1.A1", "SVR-01", "Ja"), _entry("ISMS.1.A1", "SVR-01", "Nein")])

    block = _zusammenfassung(runner, _chapter_4(("Zusätzliche Härtung", "SVR-01")), extracted)

    # Q2 is the on-site verdict and stays open; the official ISMS.1.A1 is not an additional measure.
    assert block["answers"] == [True, None, True, True]
    assert _findings(block)[0]["category"] == "OK"
    assert ai.requests == []


def test_open_additional_measure_becomes_a_finding_and_is_checked_against_the_a6(catalog):
    ai = _FakeAiClient(answer=True)
    runner = _runner(catalog, _FakeRagClient({"Realisierungsplan": ["gs://b/a6.pdf"]}), ai)
    extracted = dict([
        _entry("RA.1.A1", "SVR-01", "Ja"),
        _entry("RA.1.A2", "SVR-01", "teilweise", titel="Datenbankverschlüsselung"),
        _entry("RA.1.A3", "APP-02", "Nein", titel="DDoS-Abwehr"),
        _entry("RA.1.A4", "APP-02", "entbehrlich", titel="Sandbox"),
    ])

    block = _zusammenfassung(runner, _chapter_4(), extracted)

    assert block["answers"][0] is True          # additional requirements exist in the A.4
    assert block["answers"][2] is False
    assert block["answers"][3] is True          # the model confirmed the A.6 entries
    q3 = next(f for f in _findings(block) if "nicht als umgesetzt" in f["description"])
    assert q3["category"] == "AG"
    assert "RA.1.A2 (SVR-01: teilweise)" in q3["description"]
    assert "RA.1.A4 (APP-02: entbehrlich)" in q3["description"]
    assert "RA.1.A1" not in q3["description"]
    # Only the open (Nein/teilweise) measures go to the Realisierungsplan check.
    assert len(ai.requests) == 1
    assert ai.requests[0]["log"] == "5.6.1-Q4"
    assert ai.requests[0]["uris"] == ["gs://b/a6.pdf"]
    assert "RA.1.A2" in ai.requests[0]["prompt"] and "RA.1.A3" in ai.requests[0]["prompt"]
    assert "RA.1.A4" not in ai.requests[0]["prompt"]
    assert "Realisierungsplan (A.6)" in ai.requests[0]["prompt"]


def test_open_measures_without_a_realisierungsplan_are_a_finding_not_a_model_call(catalog):
    ai = _FakeAiClient()
    runner = _runner(catalog, _FakeRagClient(), ai)
    extracted = dict([_entry("RA.1.A1", "SVR-01", "Nein")])

    block = _zusammenfassung(runner, _chapter_4(), extracted)

    assert block["answers"][3] is False
    assert any("Realisierungsplan (A.6) wurde nicht gefunden" in f["description"] for f in _findings(block))
    assert ai.requests == []


def test_runner_without_clients_still_answers_deterministically(catalog):
    """Chapter-5 used to be built without model and document clients; a caller that still
    does so gets the computed answers and no AttributeError."""
    runner = _runner(catalog)
    block = _zusammenfassung(runner, _chapter_4(), dict([_entry("RA.1.A1", "SVR-01", "Nein")]))
    assert block["answers"][2] is False and block["answers"][3] is False


def test_planned_measures_missing_from_the_a4_are_a_finding(catalog):
    runner = _runner(catalog)
    block = _zusammenfassung(runner, _chapter_4(("DDoS-Abwehr", "WEB-01")), {})

    assert block["answers"][0] is True          # the plan holds measures for hoch/sehr hoch
    assert block["answers"][2] is False
    assert any("keine Anforderungen außerhalb des Kompendiums" in f["description"] for f in _findings(block))


def test_no_evidence_leaves_the_schutzbedarf_question_open(catalog):
    runner = _runner(catalog)
    block = _zusammenfassung(runner, _chapter_4(), dict([_entry("ISMS.1.A1", "SVR-01", "Ja")]))
    assert block["answers"] == [None, None, None, True]
    assert _findings(block)[0]["category"] == "OK"


# ---------------------------------------------------------------- 5.6.2
def _rows(runner, chapter_4, extracted):
    return runner._generate_risikoanalyse_checklist(chapter_4, extracted)["risikoanalyseA5"]["einzelergebnisseDerRisikoanalyse"]["massnahmenPruefungen"]


def test_measure_rows_carry_the_a4_status_matched_by_title(catalog):
    runner = _runner(catalog)
    extracted = dict([
        _entry("RA.1.A2", "SVR-01", "teilweise", titel="Datenbankverschlüsselung", erlaeuterung="Nur Backups verschlüsselt."),
        _entry("RA.1.A3", "APP-02", "Nein", titel="DDoS-Abwehr"),
    ])
    rows = _rows(runner, _chapter_4(("Datenbankverschlüsselung", "SVR-01"), ("Erweiterte DDoS-Abwehr", "APP-02"), ("Unbekannte Maßnahme", "X")), extracted)

    assert [r["bewertung"] for r in rows] == ["Teilweise umgesetzt", "Nicht umgesetzt", ""]
    assert rows[0]["dokuAntragsteller"] == (
        "Anforderung RA.1.A2 im Grundschutz-Check (Zielobjekt SVR-01): Datenbankverschlüsselung\n"
        "Umsetzungsstatus: teilweise\n\nNur Backups verschlüsselt."
    )
    assert rows[1]["massnahme"] == "Erweiterte DDoS-Abwehr"   # the plan's wording is kept
    assert "Keine Angabe" in rows[2]["dokuAntragsteller"]
    assert all(r["abweichungen"] == "" and r["auditfeststellung"] == "" for r in rows)


def test_a_quoted_requirement_id_wins_and_the_zielobjekt_breaks_ties(catalog):
    runner = _runner(catalog)
    runner._zielobjekt_index = ZielobjektIndex([
        {"kuerzel": "SVR-01", "name": "Datenbankserver"}, {"kuerzel": "SVR-02", "name": "Testserver"},
    ])
    extracted = dict([
        _entry("RA.1.A2", "SVR-01", "Ja", titel="Verschlüsselung"),
        _entry("RA.1.A2", "SVR-02", "Nein", titel="Verschlüsselung"),
    ])
    rows = _rows(runner, _chapter_4(("RA.1.A2 Verschlüsselung", "Datenbankserver"), ("Verschlüsselung", "SVR-02"), ("Verschlüsselung", "Mail-Gateway")), extracted)

    # Row 1: ID quoted, Zielobjekt named by its name; row 2: title match narrowed by Kürzel;
    # row 3: two candidates, the Zielobjekt resolves none of them → no entry taken.
    assert [r["bewertung"] for r in rows] == ["Umgesetzt", "Nicht umgesetzt", ""]


def test_official_requirements_are_never_offered_as_additional_measures(catalog):
    runner = _runner(catalog)
    extracted = dict([_entry("ISMS.1.A1", "SVR-01", "Ja", titel="Übernahme der Gesamtverantwortung für Informationssicherheit durch die Institutionsleitung")])
    rows = _rows(runner, _chapter_4(("Übernahme der Gesamtverantwortung für Informationssicherheit durch die Institutionsleitung", "SVR-01")), extracted)
    assert rows[0]["bewertung"] == ""
