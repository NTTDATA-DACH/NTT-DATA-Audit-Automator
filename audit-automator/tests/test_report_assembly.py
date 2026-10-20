"""Tests for the report structural check that hid MAX-1 (MAX-9).

ReportGenerator._is_report_structurally_valid is the cheap replacement for the
old no-op template-as-schema validation. It is a staticmethod, tested directly.
Importing report_generator needs google-cloud-storage + jsonschema; skip if absent.
"""
import pytest

pytest.importorskip("google.cloud.storage")

from src.audit.report_generator import ReportGenerator

valid = ReportGenerator._is_report_structurally_valid


def test_accepts_report_with_bsiauditreport_root():
    assert valid({"bsiAuditReport": {"allgemeines": {}}}) is True


def test_rejects_dict_missing_root():
    # This is exactly what the old no-op validation let through.
    assert valid({"somethingElse": 1}) is False


def test_rejects_non_dict_inputs():
    assert valid(None) is False
    assert valid([{"bsiAuditReport": {}}]) is False
    assert valid("bsiAuditReport") is False


# --- Full JSON Schema validation (the real check that replaced the MAX-1 no-op) ---
# These rely on the schema + template assets, so run from the audit-automator/ dir.
import json

schema_validate = ReportGenerator._validate_report_against_schema


def _load_template():
    with open("assets/json/master_report_template.json", "r", encoding="utf-8") as f:
        return json.load(f)


def _generator(audit_type):
    import types
    generator = ReportGenerator.__new__(ReportGenerator)
    generator.config = types.SimpleNamespace(audit_type=audit_type)
    return generator


SCAN = {
    "auswahlBausteineErstRezertifizierung": {"table": {"rows": [{"Baustein": "ISMS.1"}]}},
    "auswahlBausteine1Ueberwachungsaudit": {"table": {"rows": [{"Baustein": "ORP.4"}]}},
    "auswahlStandorte": {"table": {"rows": [{"Standort": "Musterstadt"}]}},
}


def _plan_tables(report):
    plan = report["bsiAuditReport"]["erstellungEinesPruefplans"]["auditplanung"]
    return {k: plan[k]["table"]["rows"] for k in SCAN}


def test_surveillance_audit_carries_the_previous_baustein_tables_over():
    report = _load_template()
    _generator("2. Überwachungsaudit")._populate_from_scan_report(report, SCAN)
    tables = _plan_tables(report)
    assert tables["auswahlBausteineErstRezertifizierung"] == [{"Baustein": "ISMS.1"}]
    assert tables["auswahlBausteine1Ueberwachungsaudit"] == [{"Baustein": "ORP.4"}]
    assert tables["auswahlStandorte"] == [{"Standort": "Musterstadt"}]


def test_certification_audit_leaves_the_previous_baustein_tables_out_but_keeps_the_sites():
    # A (re-)certification starts a new cycle: 4.1.1 is planned by Chapter-4, 4.1.2 stays
    # empty; the old cycle's samples would otherwise show up in the new report (issue #23).
    report = _load_template()
    before = _plan_tables(report)
    _generator("Zertifizierungsaudit")._populate_from_scan_report(report, SCAN)
    tables = _plan_tables(report)
    assert tables["auswahlBausteineErstRezertifizierung"] == before["auswahlBausteineErstRezertifizierung"]
    assert tables["auswahlBausteine1Ueberwachungsaudit"] == before["auswahlBausteine1Ueberwachungsaudit"]
    assert tables["auswahlStandorte"] == [{"Standort": "Musterstadt"}]


def test_real_template_passes_schema():
    # The canonical assembled-report skeleton must satisfy the schema.
    assert schema_validate(_load_template()) is True


def test_schema_rejects_missing_chapter():
    report = _load_template()
    del report["bsiAuditReport"]["anhang"]
    assert schema_validate(report) is False


def test_schema_rejects_non_dict_and_missing_root():
    assert schema_validate(None) is False
    assert schema_validate({"somethingElse": 1}) is False


def test_schema_rejects_findings_rows_not_an_array():
    # Guards the Chapter 7.2 findings path that hid MAX-1.
    report = _load_template()
    report["bsiAuditReport"]["anhang"]["abweichungenUndEmpfehlungen"]["empfehlungen"]["table"]["rows"] = "oops"
    assert schema_validate(report) is False


# --- Feststellung items: one per finding of a section ---
def _details_361(report):
    return report["bsiAuditReport"]["dokumentenpruefung"]["itGrundschutzCheckA4"]["detailsZumItGrundschutzCheck"]["content"]


def test_findings_list_expands_the_single_template_finding_item_in_place():
    report = _load_template()
    _generator("Zertifizierungsaudit")._populate_chapter_3(report, {"detailsZumItGrundschutzCheck": {
        "answers": [True, False, True, True, True],
        "findings": [{"category": "AG", "description": "Zielobjekte fehlen."}, {"category": "E", "description": "Kürzel nicht eindeutig."}],
    }})
    content = _details_361(report)
    assert [item["type"] for item in content] == ["question"] * 5 + ["finding", "finding"]
    assert [item["answer"] for item in content if item["type"] == "question"] == [True, False, True, True, True]
    findings = [item for item in content if item["type"] == "finding"]
    assert [item["findingText"] for item in findings] == ["[AG] Zielobjekte fehlen.", "[E] Kürzel nicht eindeutig."]
    assert [item["label"] for item in findings] == ["Feststellung 1:", "Feststellung 2:"]
    assert schema_validate(report) is True


def test_single_finding_keeps_the_template_label():
    report = _load_template()
    _generator("Zertifizierungsaudit")._populate_chapter_3(report, {"detailsZumItGrundschutzCheck": {
        "answers": [True] * 5, "finding": {"category": "OK", "description": "Alles gut."},
    }})
    findings = [item for item in _details_361(report) if item["type"] == "finding"]
    assert [(item["label"], item["findingText"]) for item in findings] == [("Feststellung:", "[OK] Alles gut.")]


def test_a_section_without_a_finding_slot_gets_no_item():
    content = [{"type": "question", "questionText": "?", "answer": None}, {"type": "prose", "text": ""}]
    ReportGenerator._write_finding_items(content, ["[AG] x"])
    assert [item["type"] for item in content] == ["question", "prose"]
