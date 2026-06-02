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
