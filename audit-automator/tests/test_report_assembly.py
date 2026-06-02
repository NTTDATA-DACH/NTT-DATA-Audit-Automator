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
import copy

schema_validate = ReportGenerator._validate_report_against_schema


def _load_template():
    with open("assets/json/master_report_template.json", "r", encoding="utf-8") as f:
        return json.load(f)


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
