"""Tests for the final report stage (duplicate findings, final numbers, chapter references).

GCS is an in-memory dict, the model is a scripted stub. The report is the real template
with a few findings added, so the schema validation of the output is exercised too.
"""
import asyncio
import copy
import json

import pytest

pytest.importorskip("google.cloud.storage")
pytest.importorskip("google.genai")

from google.cloud.exceptions import NotFound

from src.audit import report_finalizer as rf
from src.audit.report_finalizer import ReportFinalizer, collect_findings, normalize_text, parse_finding_id
from src.constants import FINALIZER_BASE


class _FakeGcs:
    def __init__(self, stored=None):
        self.stored = dict(stored or {})

    async def read_json_async(self, path):
        if path not in self.stored:
            raise NotFound(path)
        return json.loads(self.stored[path])

    async def write_json_async(self, data, path):
        self.stored[path] = json.dumps(data, ensure_ascii=False)

    async def list_blob_names_async(self, prefix):
        return [k for k in self.stored if k.startswith(prefix)]


class _FakeAi:
    model = "gemini-4-argon"

    def __init__(self, response):
        self.response = response
        self.calls = 0
        self.checker_log = []
        self.last_prompt = None

    def _resolve_thinking_level(self, level=None):
        return "medium"

    async def generate_checked_json_response(self, prompt, json_schema, gcs_uris=None, request_context_log="", **kw):
        self.calls += 1
        self.last_prompt = prompt
        return self.response


class _Config:
    bucket_name = "test-bucket"


def _template():
    with open("assets/json/master_report_template.json", "r", encoding="utf-8") as f:
        return json.load(f)


def _row(nummer, text, source, status=None, frist=None, category="AG"):
    col = "Beschreibung der Empfehlung" if category == "E" else "Beschreibung der Abweichung"
    row = {"Nummer": nummer, col: text, "Quelle (Kapitel)": source}
    row["Status"] = status or ("Korrekt (Audit-3)" if source.startswith("Previous") else "Offen")
    row["Behebungsfrist"] = frist or "N/A"
    return row


PREV = "Previous Audit (N/A)"
OLD_MODELLIERUNG = "Die Nachvollziehbarkeit der Modellierung ist im A.2 und A.4 nicht ausreichend."
NEW_MODELLIERUNG = ("Die Modellierung des Informationsverbunds weist erhebliche Lücken auf; die Vorgaben "
                    "des BSI-Standards 200-2 Kapitel 8.3 sind in wesentlichen Teilen nicht umgesetzt.")
NEW_NETZPLAN = "Der Netzplan ist nicht mit der Strukturanalyse synchronisiert (NET-07 doppelt vergeben)."
OLD_NETZPLAN = "Der Bezeichner 'NET-07' wird für zwei Zielobjekte verwendet."
NEW_TERMINE = "Im A.6 fehlen für zahlreiche Maßnahmen konkrete Umsetzungstermine."
NEW_E_SCOPE = "Die Abgrenzung des Informationsverbundes weist redaktionelle Schwächen auf."


def _report():
    """Template + findings: two previous AG, four new AG, one previous E, one new E (E-1 colliding with E-01)."""
    report = _template()
    tables = report["bsiAuditReport"]["anhang"]["abweichungenUndEmpfehlungen"]
    tables["geringfuegigeAbweichungen"]["table"]["rows"] = [
        _row("AG-100", OLD_MODELLIERUNG, PREV),
        _row("AG-85", OLD_NETZPLAN, PREV, status="Offen"),
        _row("AG-190", NEW_MODELLIERUNG, "3"),
        _row("AG-191", NEW_NETZPLAN, "3"),
        _row("AG-192", NEW_TERMINE, "3"),
        _row("AG-193", NEW_TERMINE, "3"),             # exact duplicate of AG-192
    ]
    tables["schwerwiegendeAbweichungen"]["table"]["rows"] = []
    tables["empfehlungen"]["table"]["rows"] = [
        _row("E-01", "Stellvertreterregelung im Organigramm festschreiben.", PREV, category="E"),
        _row("E-1", NEW_E_SCOPE, "1", category="E"),
    ]
    dp = report["bsiAuditReport"]["dokumentenpruefung"]
    dp["modellierungDesInformationsverbundesA3"]["content"][1]["findingText"] = f"[AG] {NEW_MODELLIERUNG}"
    dp["strukturanalyseA1"]["stichprobenDokuStrukturanalyse"]["content"][1]["findingText"] = f"[AG] {NEW_NETZPLAN}"
    dp["realisierungsplanA6"]["content"][3]["findingText"] = f"[AG] {NEW_TERMINE}"
    dp["strukturanalyseA1"]["listeDerDienstleister"]["content"][1]["findingText"] = "[OK] Nichts zu bemängeln."
    plan = report["bsiAuditReport"]["erstellungEinesPruefplans"]["auditplanung"]
    plan["auswahlBausteineErstRezertifizierung"]["table"]["rows"] = [
        {"Baustein": "ISMS.1", "Zielobjekt-Kürzel": "Verbund", "Begründung zur Auswahl": "Vorgeschrieben"},
        {"Baustein": "NET.3.2 Firewall", "Zielobjekt-Kürzel": "FW-ALL", "Begründung zur Auswahl": "Kritischer Kontrollpunkt"},
    ]
    plan["auswahlMassnahmenAusRisikoanalyse"]["table"]["rows"] = [
        {"Maßnahme": "M-RA-01 Brandfrüherkennung", "Zielobjekt": "Serverraum 1"},
    ]
    return report


def _rows(report, key):
    return report["bsiAuditReport"]["anhang"]["abweichungenUndEmpfehlungen"][key]["table"]["rows"]


def _finalize(report, response, gcs=None, ai=None):
    gcs = gcs if gcs is not None else _FakeGcs()
    ai = ai if ai is not None else _FakeAi(response)
    finalizer = ReportFinalizer(_Config(), gcs, ai)
    out, log = asyncio.run(finalizer.finalize(report))
    return out, log, gcs, ai


NO_GROUPS = {"gruppen": [], "dokumentenpruefung_ids": []}


# ----------------------------------------------------------------------------- helpers
def test_parse_and_normalize():
    assert parse_finding_id("AG-08") == ("AG", 8)
    assert parse_finding_id("E-1") == ("E", 1)
    assert parse_finding_id("Nr. 7") == (None, None)
    assert normalize_text("AG-8.: Unterschriften  einholen!") == normalize_text("Unterschriften einholen")


def test_collect_findings_marks_previous_and_category_by_table():
    findings = collect_findings(_report())
    by_key = {f.key: f for f in findings}
    assert by_key["AG-100"].previous and by_key["AG-190"].previous is False
    assert by_key["E-1"].category == "E" and by_key["E-01"].number == 1 and by_key["E-1"].number == 1


def test_output_and_log_paths_are_idempotent():
    assert ReportFinalizer.output_path_for("output/results/report_261019.json") == "output/results/report_261019_final.json"
    assert ReportFinalizer.output_path_for("output/results/report_261019_final.json") == "output/results/report_261019_final.json"
    assert ReportFinalizer.log_path_for("output/results/report_261019_final.json") == f"{FINALIZER_BASE}/report_261019_finalize_log.json"


# ----------------------------------------------------------------------------- deterministic path
def test_identical_text_is_folded_without_the_model_and_new_findings_are_renumbered():
    out, log, _, ai = _finalize(_report(), NO_GROUPS)
    ag = _rows(out, "geringfuegigeAbweichungen")
    nummern = [r["Nummer"] for r in ag]
    # AG-193 (identical to AG-192) is gone; the new ones continue after the previous max 100.
    assert nummern == ["AG-85", "AG-100", "AG-101", "AG-102", "AG-103"]
    assert ag[-1]["Beschreibung der Abweichung"] == NEW_TERMINE
    assert ai.calls == 1  # the model was asked for the semantic rest
    det = [d for d in log["decisions"] if d["quelle"] == "deterministisch"]
    assert len(det) == 1 and det[0]["status"] == "angewendet" and det[0]["behalten"] == "AG-192"
    assert log["counts"]["new_dropped"] == 1 and log["counts"]["renumbered"] == 4


def test_colliding_new_e_number_gets_a_fresh_number_and_previous_ids_stay_as_written():
    out, log, _, _ = _finalize(_report(), NO_GROUPS)
    e = _rows(out, "empfehlungen")
    assert [r["Nummer"] for r in e] == ["E-01", "E-02"]
    assert e[1]["Beschreibung der Empfehlung"] == NEW_E_SCOPE
    assert log["renumbering"]["E-1"] == "E-02"


def test_chapter_items_get_the_final_number_and_ok_items_stay():
    out, log, _, _ = _finalize(_report(), NO_GROUPS)
    dp = out["bsiAuditReport"]["dokumentenpruefung"]
    assert dp["modellierungDesInformationsverbundesA3"]["content"][1]["findingText"] == f"[AG-101] {NEW_MODELLIERUNG}"
    assert dp["strukturanalyseA1"]["stichprobenDokuStrukturanalyse"]["content"][1]["findingText"] == f"[AG-102] {NEW_NETZPLAN}"
    assert dp["realisierungsplanA6"]["content"][3]["findingText"] == f"[AG-103] {NEW_TERMINE}"
    assert dp["strukturanalyseA1"]["listeDerDienstleister"]["content"][1]["findingText"] == "[OK] Nichts zu bemängeln."
    assert log["counts"]["chapter_references"] == 3 and log["counts"]["chapter_unmatched"] == 0


def test_lightly_edited_chapter_text_still_matches_its_row():
    report = _report()
    dp = report["bsiAuditReport"]["dokumentenpruefung"]
    dp["realisierungsplanA6"]["content"][3]["findingText"] = f"[AG] {NEW_TERMINE} Siehe Kapitel 3.8."
    out, log, _, _ = _finalize(report, NO_GROUPS)
    text = out["bsiAuditReport"]["dokumentenpruefung"]["realisierungsplanA6"]["content"][3]["findingText"]
    assert text.startswith("[AG-103] ")
    assert any(r["match"] == "enthalten" for r in log["chapter_references"])


def test_chapter_text_with_a_changed_word_still_matches_by_similarity():
    report = _report()
    dp = report["bsiAuditReport"]["dokumentenpruefung"]
    dp["modellierungDesInformationsverbundesA3"]["content"][1]["findingText"] = (
        f"[AG] {NEW_MODELLIERUNG.replace('erhebliche', 'deutliche')}"
    )
    out, log, _, _ = _finalize(report, NO_GROUPS)
    text = out["bsiAuditReport"]["dokumentenpruefung"]["modellierungDesInformationsverbundesA3"]["content"][1]["findingText"]
    assert text.startswith("[AG-101] ")
    assert any(r["match"].startswith("aehnlich") for r in log["chapter_references"])


# ----------------------------------------------------------------------------- model groups
def test_new_finding_contained_in_previous_one_is_dropped_and_its_chapter_points_to_the_old_number():
    response = {"gruppen": [{
        "ids": ["AG-85", "AG-191"], "behalten": "AG-85", "konfidenz": 0.95,
        "begruendung": "Beide bemängeln den doppelt vergebenen Bezeichner NET-07.",
    }]}
    out, log, _, _ = _finalize(_report(), response)
    ag = {r["Nummer"]: r for r in _rows(out, "geringfuegigeAbweichungen")}
    assert NEW_NETZPLAN not in {r["Beschreibung der Abweichung"] for r in ag.values()}
    assert ag["AG-85"]["Status"] == "Offen (erneut festgestellt, Kapitel 3)"
    text = out["bsiAuditReport"]["dokumentenpruefung"]["strukturanalyseA1"]["stichprobenDokuStrukturanalyse"]["content"][1]["findingText"]
    assert text == f"[AG-85] {NEW_NETZPLAN}"
    # The numbers of the remaining new findings close the gap.
    assert sorted(ag) == ["AG-100", "AG-101", "AG-102", "AG-85"]


def test_previous_finding_superseded_by_a_more_comprehensive_new_one_is_closed_with_a_reference():
    response = {"gruppen": [{
        "ids": ["AG-100", "AG-190"], "behalten": "AG-190", "konfidenz": 0.92,
        "begruendung": "Dieselbe mangelhafte Modellierung; die neue Feststellung benennt die Vorgabe und den Umfang.",
    }]}
    out, log, _, _ = _finalize(_report(), response)
    ag = {r["Nummer"]: r for r in _rows(out, "geringfuegigeAbweichungen")}
    # AG-190 is the first new finding -> AG-101; the old row stays, closed with the new number.
    assert ag["AG-101"]["Beschreibung der Abweichung"] == NEW_MODELLIERUNG
    assert ag["AG-100"]["Status"] == "Geschlossen, siehe AG-101"
    assert ag["AG-100"]["Beschreibung der Abweichung"] == OLD_MODELLIERUNG
    text = out["bsiAuditReport"]["dokumentenpruefung"]["modellierungDesInformationsverbundesA3"]["content"][1]["findingText"]
    assert text == f"[AG-101] {NEW_MODELLIERUNG}"
    assert log["counts"]["previous_closed"] == 1


def test_group_below_the_confidence_threshold_is_logged_but_not_applied():
    response = {"gruppen": [{
        "ids": ["AG-85", "AG-191"], "behalten": "AG-85", "konfidenz": 0.6, "begruendung": "Vielleicht dasselbe.",
    }]}
    out, log, _, _ = _finalize(_report(), response)
    assert {r["Nummer"] for r in _rows(out, "geringfuegigeAbweichungen")} == {"AG-85", "AG-100", "AG-101", "AG-102", "AG-103"}
    decision = [d for d in log["decisions"] if d["quelle"] == "modell"][0]
    assert decision["status"].startswith("manuell pruefen")
    assert log["counts"]["groups_below_threshold"] == 1


def test_group_with_unknown_or_invented_ids_is_rejected():
    response = {"gruppen": [
        {"ids": ["AG-85", "AG-999"], "behalten": "AG-85", "konfidenz": 0.99, "begruendung": "x"},
        {"ids": ["AG-85", "AG-191"], "behalten": "AG-190", "konfidenz": 0.99, "begruendung": "y"},
        {"ids": ["AG-85"], "behalten": "AG-85", "konfidenz": 0.99, "begruendung": "z"},
    ]}
    out, log, _, _ = _finalize(_report(), response)
    statuses = [d["status"] for d in log["decisions"] if d["quelle"] == "modell"]
    assert all(s.startswith("verworfen") for s in statuses) and len(statuses) == 3
    assert log["counts"]["groups_rejected"] == 3 and log["counts"]["new_dropped"] == 1  # only the exact duplicate


def test_group_overlapping_an_earlier_group_follows_the_survivor():
    # AG-193 was folded into AG-192 by code; the model names AG-193 as the one to keep.
    response = {"gruppen": [{
        "ids": ["AG-193", "AG-191"], "behalten": "AG-193", "konfidenz": 0.95, "begruendung": "x",
    }]}
    out, log, _, _ = _finalize(_report(), response)
    ag = {r["Nummer"]: r for r in _rows(out, "geringfuegigeAbweichungen")}
    assert NEW_NETZPLAN not in {r["Beschreibung der Abweichung"] for r in ag.values()}
    decision = [d for d in log["decisions"] if d["quelle"] == "modell"][0]
    assert decision["status"] == "angewendet"
    assert any("AG-192" in w for w in decision["wirkung"])


# ----------------------------------------------------------------------------- previous findings not met again
def test_open_document_review_finding_not_found_again_is_closed_and_done_ones_stay():
    # AG-85 is open ("Offen"), AG-100 is done ("Korrekt (Audit-3)"): the model names both.
    response = {"gruppen": [], "dokumentenpruefung_ids": ["AG-85", "AG-100", "E-01", "AG-999"]}
    report = _report()
    _rows(report, "empfehlungen")[0]["Status"] = "Zur Umsetzung empfohlen"   # E-01 counts as open
    out, log, _, _ = _finalize(report, response)
    ag = {r["Nummer"]: r for r in _rows(out, "geringfuegigeAbweichungen")}
    e = {r["Nummer"]: r for r in _rows(out, "empfehlungen")}
    assert ag["AG-85"]["Status"] == "Geschlossen (Dokumentenprüfung: nicht erneut festgestellt)"
    assert ag["AG-100"]["Status"] == "Korrekt (Audit-3)"
    assert e["E-01"]["Status"] == "Geschlossen (Dokumentenprüfung: nicht erneut festgestellt)"
    assert log["closed_not_refound"] == ["AG-85", "E-01"]
    assert log["document_review_ids_ignored"] == ["AG-100", "AG-999"]
    assert log["counts"]["previous_open"] == 2 and log["counts"]["previous_closed_not_refound"] == 2


def test_open_finding_met_again_in_the_current_audit_is_not_closed():
    response = {
        "gruppen": [{"ids": ["AG-85", "AG-191"], "behalten": "AG-85", "konfidenz": 0.95, "begruendung": "x"}],
        "dokumentenpruefung_ids": ["AG-85"],
    }
    out, log, _, _ = _finalize(_report(), response)
    ag = {r["Nummer"]: r for r in _rows(out, "geringfuegigeAbweichungen")}
    assert ag["AG-85"]["Status"] == "Offen (erneut festgestellt, Kapitel 3)"
    assert log["closed_not_refound"] == []


def test_previous_finding_superseded_by_a_new_one_keeps_its_reference_status():
    response = {
        "gruppen": [{"ids": ["AG-100", "AG-190"], "behalten": "AG-190", "konfidenz": 0.95, "begruendung": "x"}],
        "dokumentenpruefung_ids": ["AG-100"],
    }
    report = _report()
    _rows(report, "geringfuegigeAbweichungen")[0]["Status"] = "Teilweise (Re-Zert)"
    out, log, _, _ = _finalize(report, response)
    ag = {r["Nummer"]: r for r in _rows(out, "geringfuegigeAbweichungen")}
    assert ag["AG-100"]["Status"] == "Geschlossen, siehe AG-101"
    assert log["closed_not_refound"] == []


def test_new_findings_are_never_closed_as_not_refound():
    response = {"gruppen": [], "dokumentenpruefung_ids": ["AG-190", "E-1"]}
    out, log, _, _ = _finalize(_report(), response)
    statuses = {r["Status"] for r in _rows(out, "geringfuegigeAbweichungen") + _rows(out, "empfehlungen")}
    assert "Geschlossen (Dokumentenprüfung: nicht erneut festgestellt)" not in statuses
    assert log["document_review_ids_ignored"] == ["AG-190", "E-1"]


# ----------------------------------------------------------------------------- invariants
def test_audit_plan_in_chapter_4_is_untouched():
    report = _report()
    before = copy.deepcopy(report["bsiAuditReport"]["erstellungEinesPruefplans"])
    response = {"gruppen": [{"ids": ["AG-100", "AG-190"], "behalten": "AG-190", "konfidenz": 0.95, "begruendung": "x"}]}
    out, _, _, _ = _finalize(report, response)
    assert out["bsiAuditReport"]["erstellungEinesPruefplans"] == before


def test_finalized_report_passes_the_report_schema():
    from src.audit.report_generator import ReportGenerator
    out, _, _, _ = _finalize(_report(), NO_GROUPS)
    assert ReportGenerator._validate_report_against_schema(out) is True


def test_second_run_on_the_same_report_costs_no_model_call_and_is_identical():
    gcs = _FakeGcs()
    ai = _FakeAi(NO_GROUPS)
    out1, _, _, _ = _finalize(_report(), NO_GROUPS, gcs=gcs, ai=ai)
    out2, log2, _, _ = _finalize(_report(), NO_GROUPS, gcs=gcs, ai=ai)
    assert ai.calls == 1 and log2["groups_source"] == "cache"
    assert out1 == out2


def test_run_picks_the_newest_report_and_writes_final_and_log():
    report = _report()
    gcs = _FakeGcs({
        "output/results/report_261016.json": json.dumps(report),
        "output/results/report_261019.json": json.dumps(report),
        "output/results/report_261001_final.json": json.dumps(report),
        "output/results/all_findings.json": "[]",
    })
    finalizer = ReportFinalizer(_Config(), gcs, _FakeAi(NO_GROUPS))
    out_path = asyncio.run(finalizer.run())
    assert out_path == "output/results/report_261019_final.json"
    assert out_path in gcs.stored
    log = json.loads(gcs.stored[f"{FINALIZER_BASE}/report_261019_finalize_log.json"])
    assert log["input"] == "output/results/report_261019.json"


def test_run_without_a_report_fails_with_a_clear_message():
    finalizer = ReportFinalizer(_Config(), _FakeGcs(), _FakeAi(NO_GROUPS))
    with pytest.raises(FileNotFoundError, match="generate-report"):
        asyncio.run(finalizer.run())


def test_prompt_carries_every_finding_and_its_origin():
    out, _, _, ai = _finalize(_report(), NO_GROUPS)
    assert "vorheriges Audit" in ai.last_prompt and "aktuelles Audit" in ai.last_prompt
    for key in ("AG-100", "AG-85", "AG-190", "E-01", "E-1"):
        assert f'"id": "{key}"' in ai.last_prompt


def test_prompt_placeholder_and_schema_agree_with_the_code():
    cfg = rf.load_asset_json(rf.PROMPT_CONFIG_PATH)["stages"][rf.STAGE_NAME]["duplikate"]
    assert "{findings_json}" in cfg["prompt"]
    schema = rf.load_asset_json(cfg["schema_path"])
    assert set(schema["properties"]["gruppen"]["items"]["required"]) == {"ids", "behalten", "begruendung", "konfidenz"}
    assert "dokumentenpruefung_ids" in schema["required"] and "dokumentenpruefung_ids" in cfg["prompt"]
    assert "offen=true" in cfg["prompt"]


# ----------------------------------------------------------------------------- structural guards
def _model_decision(log):
    return [d for d in log["decisions"] if d["quelle"] == "modell"][0]


def test_mixed_categories_within_the_current_audit_are_left_for_manual_review():
    response = {"gruppen": [{"ids": ["AG-191", "E-1"], "behalten": "AG-191", "konfidenz": 0.95, "begruendung": "Beides A.1."}]}
    out, log, _, _ = _finalize(_report(), response)
    assert _model_decision(log)["status"].startswith("manuell pruefen: Kategorien gemischt (AG, E)")
    assert NEW_E_SCOPE in {r["Beschreibung der Empfehlung"] for r in _rows(out, "empfehlungen")}
    assert NEW_NETZPLAN in {r["Beschreibung der Abweichung"] for r in _rows(out, "geringfuegigeAbweichungen")}
    assert log["counts"]["groups_guarded"] == 1 and log["counts"]["groups_below_threshold"] == 0


def test_regrading_keeps_the_current_finding_and_closes_the_previous_one():
    # Yesterday's AG-85 is only a recommendation today: the current E survives, the old AG closes.
    response = {"gruppen": [{"ids": ["AG-85", "E-1"], "behalten": "E-1", "konfidenz": 0.95, "begruendung": "Neubewertung AG -> E."}]}
    out, log, _, _ = _finalize(_report(), response)
    assert _model_decision(log)["status"] == "angewendet (Neubewertung AG -> E)"
    ag = {r["Nummer"]: r for r in _rows(out, "geringfuegigeAbweichungen")}
    e = {r["Nummer"]: r for r in _rows(out, "empfehlungen")}
    assert ag["AG-85"]["Status"] == "Geschlossen, siehe E-02"
    assert e["E-02"]["Beschreibung der Empfehlung"] == NEW_E_SCOPE
    assert log["counts"]["regraded"] == 1 and log["counts"]["previous_closed"] == 1


def test_regrading_that_keeps_the_previous_finding_is_left_for_manual_review():
    response = {"gruppen": [{"ids": ["AG-85", "E-1"], "behalten": "AG-85", "konfidenz": 0.95, "begruendung": "x"}]}
    out, log, _, _ = _finalize(_report(), response)
    assert _model_decision(log)["status"].startswith("manuell pruefen: Neubewertung (AG -> E)")
    ag = {r["Nummer"]: r for r in _rows(out, "geringfuegigeAbweichungen")}
    assert ag["AG-85"]["Status"] == "Offen"
    assert NEW_E_SCOPE in {r["Beschreibung der Empfehlung"] for r in _rows(out, "empfehlungen")}


def test_group_with_too_many_new_findings_is_left_for_manual_review():
    report = _report()
    _rows(report, "geringfuegigeAbweichungen").extend([
        _row("AG-194", "Die Liste der Anwendungen nennt keine Verantwortlichen.", "3"),
        _row("AG-195", "Die Liste der IT-Systeme nennt keine Aufstellungsorte.", "3"),
    ])
    response = {"gruppen": [{
        "ids": ["AG-190", "AG-191", "AG-194", "AG-195"], "behalten": "AG-190", "konfidenz": 0.93, "begruendung": "Alles A.1.",
    }]}
    out, log, _, _ = _finalize(report, response)
    assert _model_decision(log)["status"].startswith("manuell pruefen: Gruppe zu gross (4 Feststellungen")
    assert len(_rows(out, "geringfuegigeAbweichungen")) == 7  # only the identical-text pair was folded
    assert log["max_new_per_group"] == rf.FINALIZER_MAX_NEW_PER_GROUP


def test_identical_text_in_an_ag_and_an_e_of_the_same_audit_is_not_folded_silently():
    # The old rule kept the lower number, so two AGs would have vanished into an E.
    report = _report()
    _rows(report, "empfehlungen").append(_row("E-2", NEW_TERMINE, "3", category="E"))  # same text as AG-192/AG-193
    out, log, _, _ = _finalize(report, NO_GROUPS)
    [decision] = [d for d in log["decisions"] if d["quelle"] == "deterministisch"]
    assert decision["status"].startswith("manuell pruefen: Kategorien gemischt (AG, E)")
    assert set(decision["ids"]) == {"AG-192", "AG-193", "E-2"}
    assert NEW_TERMINE in {r["Beschreibung der Empfehlung"] for r in _rows(out, "empfehlungen")}
    assert sum(1 for r in _rows(out, "geringfuegigeAbweichungen") if r["Beschreibung der Abweichung"] == NEW_TERMINE) == 2


def test_identical_text_with_a_changed_category_across_audits_keeps_the_current_grading():
    report = _report()
    _rows(report, "empfehlungen").append(_row("E-2", OLD_NETZPLAN, "3", category="E"))  # yesterday's AG-85, now an E
    out, log, _, _ = _finalize(report, NO_GROUPS)
    [decision] = [d for d in log["decisions"] if d["quelle"] == "deterministisch" and "AG-85" in d["ids"]]
    assert decision["behalten"] == "E-2" and decision["status"] == "angewendet (Neubewertung AG -> E)"
    ag = {r["Nummer"]: r for r in _rows(out, "geringfuegigeAbweichungen")}
    assert ag["AG-85"]["Status"] == "Geschlossen, siehe E-03"


def test_prompt_names_the_sammelfeststellung_and_neubewertung_rules():
    prompt = rf.load_asset_json(rf.PROMPT_CONFIG_PATH)["stages"][rf.STAGE_NAME]["duplikate"]["prompt"]
    assert "andere Mängel benennen" in prompt
    assert "desselben Audits mit unterschiedlicher Kategorie" in prompt
    assert "Neubewertung" in prompt and "behalten ist dann immer die aktuelle Feststellung" in prompt
