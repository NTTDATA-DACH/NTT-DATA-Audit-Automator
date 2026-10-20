"""Tests for chapter 5.6 (Risikoanalyse A.5) of the on-site checklist.

The additional measures are the measures the Risikoanalyse defines: the model reads them out
of the document (one cached call), code looks every one of them up in the extracted A.4, by
requirement ID or by wording, and answers 5.6.1 from the result; 5.6.2 shows the customer's
Umsetzungsstatus next to every planned measure. Offline: the runner is built with __new__,
model, document finder and bucket are fakes, the catalog is the real one (so 'RA.1.A1' is
unknown, 'ISMS.1.A1' is a B and 'SYS.1.1.A30' an H requirement).
"""
import asyncio
import json

import pytest

pytest.importorskip("google.cloud.storage")

from google.cloud.exceptions import NotFound

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
    """Answers the measure extraction with `massnahmen` and every targeted question with yes."""
    model = "fake-model"

    def __init__(self, massnahmen=None, answer=True):
        self.requests = []
        self._massnahmen = massnahmen or []
        self._answer = answer

    async def generate_checked_json_response(self, prompt, json_schema, gcs_uris=None, request_context_log="", thinking_level=None):
        self.requests.append({"prompt": prompt, "uris": gcs_uris, "log": request_context_log})
        if "Massnahmen" in request_context_log:
            return {"massnahmen": self._massnahmen}
        return {"answers": [self._answer], "finding": {"category": "OK", "description": "ok"}}


class _FakeGcs:
    """An in-memory bucket: the extraction cache lives here."""

    def __init__(self):
        self.files = {}

    def read_json(self, path):
        if path not in self.files:
            raise NotFound(path)
        return self.files[path]

    def write_json(self, data, path):
        self.files[path] = data


@pytest.fixture(scope="module")
def catalog():
    return ControlCatalog()


def _runner(catalog, rag_client=None, ai_client=None):
    runner = Chapter5Runner.__new__(Chapter5Runner)
    runner.control_catalog = catalog
    runner.rag_client = rag_client
    runner.ai_client = ai_client
    runner.gcs_client = _FakeGcs()
    with open(PROMPT_CONFIG_PATH, encoding="utf-8") as f:
        runner.prompt_config = json.load(f)
    return runner


def _entry(control_id, kuerzel, status, titel="Zusätzliche Härtung", erlaeuterung="x"):
    return (control_id, kuerzel), {
        "id": control_id, "zielobjekt_kuerzel": kuerzel, "titel": titel,
        "umsetzungsstatus": status, "umsetzungserlaeuterung": erlaeuterung,
    }


def _measure(bezeichnung, zielobjekt="SVR-01", anforderung_id="", fundstelle="Kap. 6, Tabelle 3", status=""):
    return {"bezeichnung": bezeichnung, "zielobjekt": zielobjekt, "anforderung_id": anforderung_id,
            "fundstelle": fundstelle, "umsetzungsstatus_laut_dokument": status}


def _chapter_4(*measures):
    rows = [{"Maßnahme": m, "Risikoanalyse": "RA 2026", "Zielobjekt": z, "Begründung zur Auswahl": "…"} for m, z in measures]
    return {"auswahlMassnahmenAusRisikoanalyse": {"rows": rows}}


RA_DOCS = {"Risikoanalyse": ["gs://b/a5.pdf"], "Realisierungsplan": ["gs://b/a6.pdf"]}


def _run(runner, chapter_4, extracted):
    async def go():
        massnahmen = await runner._risikoanalyse_massnahmen()
        rows = runner._generate_risikoanalyse_checklist(chapter_4, extracted, massnahmen or [])
        block = await runner._generate_risikoanalyse_zusammenfassung(chapter_4, extracted, massnahmen)
        return block["zusammenfassungDerRisikoanalyse"], rows["risikoanalyseA5"]["einzelergebnisseDerRisikoanalyse"]["massnahmenPruefungen"]
    return asyncio.run(go())


def _findings(block):
    return block.get("findings") or [block["finding"]]


# ---------------------------------------------------------------- extraction
def test_measures_are_read_once_and_validated(catalog):
    ai = _FakeAiClient([
        _measure("Datenbankverschlüsselung", anforderung_id="SYS.1.1.A30 (H)"),
        _measure("DDoS-Abwehr", "WEB-01", anforderung_id="nicht im Kompendium"),
        {"bezeichnung": "   ", "zielobjekt": "x", "anforderung_id": "", "fundstelle": "", "umsetzungsstatus_laut_dokument": ""},
    ])
    runner = _runner(catalog, _FakeRagClient(RA_DOCS), ai)

    first = asyncio.run(runner._risikoanalyse_massnahmen())
    second = asyncio.run(runner._risikoanalyse_massnahmen())

    assert [m["anforderung_id"] for m in first] == ["SYS.1.1.A30", ""]
    assert [m["level"] for m in first] == ["H", None]
    assert second == first
    assert len([r for r in ai.requests if "Massnahmen" in r["log"]]) == 1, "the second run must come from the cache"
    assert ai.requests[0]["uris"] == ["gs://b/a5.pdf"]
    assert len(runner.gcs_client.files) == 1


def test_without_a_risikoanalyse_nothing_is_read(catalog):
    ai = _FakeAiClient([_measure("x")])
    runner = _runner(catalog, _FakeRagClient({"Realisierungsplan": ["gs://b/a6.pdf"]}), ai)
    assert asyncio.run(runner._risikoanalyse_massnahmen()) is None
    assert ai.requests == []


# ---------------------------------------------------------------- 5.6.1
def test_all_measures_implemented_answers_q1_q3_q4_yes_without_a_targeted_call(catalog):
    ai = _FakeAiClient([
        _measure("Datenbankverschlüsselung", anforderung_id="SYS.1.1.A30"),   # an H requirement of the Kompendium
        _measure("Zusätzliche Härtung"),                                        # the institution's own, found by wording
    ])
    runner = _runner(catalog, _FakeRagClient(RA_DOCS), ai)
    extracted = dict([
        _entry("SYS.1.1.A30", "SVR-01", "Ja", titel="Einsatz von Verschlüsselung"),
        _entry("RA.1.A1", "SVR-01", "Ja"),
        _entry("ISMS.1.A1", "SVR-01", "Nein", titel="Übernahme der Gesamtverantwortung"),  # not a measure of the Risikoanalyse
    ])

    block, _ = _run(runner, _chapter_4(), extracted)

    assert block["answers"] == [True, None, True, True]
    assert _findings(block)[0]["category"] == "OK"
    assert [r["log"] for r in ai.requests] == ["5.6-Risikoanalyse-Massnahmen"]


def test_open_and_missing_measures_are_findings_and_the_open_ones_go_to_the_a6_check(catalog):
    ai = _FakeAiClient([
        _measure("Datenbankverschlüsselung", anforderung_id="SYS.1.1.A30"),
        _measure("Zusätzliche Härtung"),
        _measure("DDoS-Abwehr", "WEB-01", fundstelle="Kap. 6.2"),
        _measure("Sandbox", "MAIL-01", anforderung_id="RA.1.A4"),
    ])
    runner = _runner(catalog, _FakeRagClient(RA_DOCS), ai)
    extracted = dict([
        _entry("SYS.1.1.A30", "SVR-01", "teilweise", titel="Einsatz von Verschlüsselung"),
        _entry("RA.1.A1", "SVR-01", "Nein"),
        _entry("RA.1.A4", "MAIL-01", "entbehrlich", titel="Sandbox"),
    ])

    block, _ = _run(runner, _chapter_4(), extracted)

    assert block["answers"][0] is True
    assert block["answers"][2] is False
    assert block["answers"][3] is True          # the model confirmed the A.6 entries
    texts = [f["description"] for f in _findings(block)]
    missing = next(t for t in texts if "nicht auffindbar" in t)
    assert "DDoS-Abwehr (WEB-01)" in missing and "1 zusätzliche" in missing
    open_ = next(t for t in texts if "nicht als umgesetzt gekennzeichnet" in t)
    assert "Datenbankverschlüsselung (SVR-01; A.4 SYS.1.1.A30: teilweise)" in open_
    assert "Zusätzliche Härtung (SVR-01; A.4 RA.1.A1: Nein)" in open_
    assert "Sandbox (MAIL-01; A.4 RA.1.A4: entbehrlich)" in open_
    assert all(f["category"] == "AG" for f in _findings(block))
    # Only Nein/teilweise go to the Realisierungsplan; entbehrlich is not open.
    q4 = [r for r in ai.requests if r["log"] == "5.6.1-Q4"]
    assert len(q4) == 1 and q4[0]["uris"] == ["gs://b/a6.pdf"]
    assert "SYS.1.1.A30" in q4[0]["prompt"] and "RA.1.A1" in q4[0]["prompt"] and "RA.1.A4" not in q4[0]["prompt"]
    assert "Realisierungsplan (A.6)" in q4[0]["prompt"]


def test_a_risikoanalyse_without_an_h_requirement_is_a_finding(catalog):
    ai = _FakeAiClient([_measure("Zusätzliche Härtung"), _measure("Sandbox", anforderung_id="ISMS.1.A1")])
    runner = _runner(catalog, _FakeRagClient(RA_DOCS), ai)
    extracted = dict([_entry("RA.1.A1", "SVR-01", "Ja"), _entry("ISMS.1.A1", "SVR-01", "Ja", titel="Sandbox")])

    block, _ = _run(runner, _chapter_4(), extracted)

    assert block["answers"][2] is True
    h = [f for f in _findings(block) if "erhöhtem Schutzbedarf (H)" in f["description"]]
    assert len(h) == 1 and h[0]["category"] == "AG"
    assert "2 zusätzliche Maßnahmen" in h[0]["description"]


def test_open_measures_without_a_realisierungsplan_are_a_finding_not_a_model_call(catalog):
    ai = _FakeAiClient([_measure("Zusätzliche Härtung", anforderung_id="RA.1.A1")])
    runner = _runner(catalog, _FakeRagClient({"Risikoanalyse": ["gs://b/a5.pdf"]}), ai)

    block, _ = _run(runner, _chapter_4(), dict([_entry("RA.1.A1", "SVR-01", "Nein")]))

    assert block["answers"][3] is False
    assert any("Realisierungsplan (A.6) wurde nicht gefunden" in f["description"] for f in _findings(block))
    assert [r["log"] for r in ai.requests] == ["5.6-Risikoanalyse-Massnahmen"]


def test_without_a_risikoanalyse_the_a4_requirements_outside_the_kompendium_stand_in(catalog):
    """No document: the consolidated measures in the A.4 are all there is. Nothing is
    'missing' then, and the H check cannot be made."""
    runner = _runner(catalog)
    extracted = dict([_entry("RA.1.A1", "SVR-01", "Nein"), _entry("RA.1.A2", "SVR-01", "Ja"), _entry("ISMS.1.A1", "SVR-01", "Nein")])

    block, _ = _run(runner, _chapter_4(), extracted)

    assert block["answers"] == [True, None, False, False]
    texts = [f["description"] for f in _findings(block)]
    assert any("Zusätzliche Härtung (SVR-01; A.4 RA.1.A1: Nein)" in t for t in texts)
    assert not any("nicht auffindbar" in t or "erhöhtem Schutzbedarf (H)" in t for t in texts)


def test_planned_measures_with_no_evidence_anywhere_are_a_finding(catalog):
    runner = _runner(catalog)
    block, _ = _run(runner, _chapter_4(("DDoS-Abwehr", "WEB-01")), {})
    assert block["answers"][0] is True and block["answers"][2] is False
    assert any("weder die Risikoanalyse noch der IT-Grundschutz-Check" in f["description"] for f in _findings(block))


def test_no_evidence_leaves_the_schutzbedarf_question_open(catalog):
    runner = _runner(catalog)
    block, _ = _run(runner, _chapter_4(), dict([_entry("ISMS.1.A1", "SVR-01", "Ja")]))
    assert block["answers"] == [None, None, None, True]
    assert _findings(block)[0]["category"] == "OK"


# ---------------------------------------------------------------- 5.6.2
def test_planned_measure_rows_carry_the_a4_status(catalog):
    ai = _FakeAiClient([
        _measure("Verschlüsselung der Datenbank", "SVR-01", anforderung_id="SYS.1.1.A30", fundstelle="Kap. 6.1"),
        _measure("DDoS-Abwehr", "WEB-01", fundstelle="Kap. 6.2"),
    ])
    runner = _runner(catalog, _FakeRagClient(RA_DOCS), ai)
    extracted = dict([
        _entry("SYS.1.1.A30", "SVR-01", "teilweise", titel="Einsatz von Verschlüsselung", erlaeuterung="Nur Backups verschlüsselt."),
        _entry("RA.1.A3", "APP-02", "Nein", titel="Härtung der Applikation"),
    ])
    plan = _chapter_4(
        ("Verschlüsselung der Datenbank", "SVR-01"),   # via the Risikoanalyse measure → its requirement ID
        ("Härtung der Applikation", "APP-02"),          # via the A.4 title
        ("DDoS-Abwehr", "WEB-01"),                      # in the Risikoanalyse, not in the A.4
        ("Unbekannte Maßnahme", "X"),
    )

    _, rows = _run(runner, plan, extracted)

    assert [r["bewertung"] for r in rows] == ["Teilweise umgesetzt", "Nicht umgesetzt", "", ""]
    assert rows[0]["dokuAntragsteller"] == (
        "Anforderung SYS.1.1.A30 im Grundschutz-Check (Zielobjekt SVR-01): Einsatz von Verschlüsselung\n"
        "Umsetzungsstatus: teilweise\n\nNur Backups verschlüsselt."
    )
    assert rows[0]["massnahme"] == "Verschlüsselung der Datenbank"   # the plan's wording is kept
    assert rows[2]["dokuAntragsteller"].startswith("In der Risikoanalyse als Maßnahme enthalten (Kap. 6.2).")
    assert "Keine Angabe" in rows[3]["dokuAntragsteller"]
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
    plan = _chapter_4(("RA.1.A2 Verschlüsselung", "Datenbankserver"), ("Verschlüsselung", "SVR-02"), ("Verschlüsselung", "Mail-Gateway"))

    _, rows = _run(runner, plan, extracted)

    # Row 1: ID quoted, Zielobjekt named by its name; row 2: title match narrowed by Kürzel;
    # row 3: two candidates, the Zielobjekt resolves none of them → no entry taken.
    assert [r["bewertung"] for r in rows] == ["Umgesetzt", "Nicht umgesetzt", ""]
