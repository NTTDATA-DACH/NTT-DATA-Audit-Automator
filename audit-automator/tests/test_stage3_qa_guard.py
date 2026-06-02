"""Tests for the stage-3 targeted Q-handler guard (MAX-15b / MAX-9).

Chapter3Runner._record_targeted_answer is a staticmethod that only touches its
arguments + logging, so it is tested directly without building the runner (which
would need GCS/RAG clients). Importing the stage module needs google-genai +
jsonschema (via AiClient); skip cleanly if absent.
"""
import pytest

pytest.importorskip("google.genai")

from src.audit.stages.stage_3_dokumentenpruefung import Chapter3Runner

record = Chapter3Runner._record_targeted_answer


def test_wellformed_ok_finding_sets_answer_and_adds_no_finding():
    answers = [None] * 5
    findings = []
    record({"answers": [True], "finding": {"category": "OK", "description": "fine"}}, answers, 1, findings, "Q2")
    assert answers[1] is True
    assert findings == []


def test_wellformed_non_ok_finding_is_appended():
    answers = [None] * 5
    findings = []
    res = {"answers": [False], "finding": {"category": "AG", "description": "issue"}}
    record(res, answers, 2, findings, "Q3")
    assert answers[2] is False
    assert findings == [{"category": "AG", "description": "issue"}]


def test_missing_answers_scores_false_and_flags_ag():
    answers = [None] * 5
    findings = []
    record({"finding": {"category": "OK"}}, answers, 1, findings, "Q2")
    assert answers[1] is False  # conservative default
    assert len(findings) == 1 and findings[0]["category"] == "AG"


def test_empty_answers_list_scores_false_and_flags_ag():
    answers = [None] * 5
    findings = []
    record({"answers": [], "finding": {"category": "OK"}}, answers, 3, findings, "Q4")
    assert answers[3] is False
    assert len(findings) == 1 and findings[0]["category"] == "AG"


def test_missing_finding_flags_ag_without_crashing():
    answers = [None] * 5
    findings = []
    record({"answers": [True]}, answers, 1, findings, "Q2")
    assert answers[1] is True
    assert len(findings) == 1 and findings[0]["category"] == "AG"


def test_completely_malformed_response_does_not_raise():
    answers = [None] * 5
    findings = []
    record("not-a-dict", answers, 1, findings, "Q2")  # degraded AI response
    assert answers[1] is False
    # one AG for missing answers + one AG for missing finding
    assert len(findings) == 2 and all(f["category"] == "AG" for f in findings)
