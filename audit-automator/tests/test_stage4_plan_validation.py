"""Chapter 4 Baustein plans: prompt rendering from the shared template and the code check
of every row against the ground-truth map.

The prompt tells the model which Zielobjekt a Baustein may be paired with, but Chapter 5
joins the plan's Kürzel against the extracted Grundschutz-Check, so a row the map does not
support produces a checklist without customer data. The check therefore lives in code.
"""
import asyncio
import json
import types

import pytest

pytest.importorskip("google.genai")

from src.audit.stages.stage_4_pruefplan import Chapter4Runner
from src.constants import PROMPT_CONFIG_PATH

GROUND_TRUTH = {
    "zielobjekte": [{"kuerzel": "SRV-01", "name": "Fileserver"}, {"kuerzel": "NET-01", "name": "Firewall"}],
    "baustein_to_zielobjekt_mapping": {"SYS.1.1": ["SRV-01"], "NET.3.2": ["NET-01"]},
    "informationsverbund_name": "IV Test",
}


def _row(baustein, kuerzel, schicht=None):
    return {
        "Schicht": schicht or baustein.split(".")[0],
        "Baustein": baustein,
        "Zielobjekt-Name": "x",
        "Zielobjekt-Kürzel": kuerzel,
        "Begründung zur Auswahl": "Weil.",
    }


class _FakeAiClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def generate_checked_json_response(self, prompt, json_schema, gcs_uris=None, request_context_log="", thinking_level=None):
        self.calls.append({"prompt": prompt, "log": request_context_log})
        return self.responses.pop(0)


def _runner(audit_type, responses, ground_truth=GROUND_TRUTH):
    runner = Chapter4Runner.__new__(Chapter4Runner)
    with open(PROMPT_CONFIG_PATH, encoding="utf-8") as f:
        runner.prompt_config = json.load(f)
    runner.config = types.SimpleNamespace(audit_type=audit_type)
    runner.ai_client = _FakeAiClient(responses)
    runner.rag_client = types.SimpleNamespace(get_gcs_uris_for_categories=lambda cats: [])
    runner.ground_truth_map = ground_truth
    runner.subchapter_definitions = runner._load_subchapter_definitions()
    return runner


def _plan(runner, section):
    result = asyncio.run(runner._process_single_subchapter(section, runner.subchapter_definitions[section]))
    return result[section]["rows"]


def test_every_audit_type_renders_its_prompt_from_the_shared_template():
    for audit_type, section in Chapter4Runner.BAUSTEIN_SECTIONS.items():
        runner = _runner(audit_type, [])
        definition = runner.subchapter_definitions[section]
        prompt = definition["prompt"]
        assert definition["audit_type"] in prompt
        assert definition["selection_rules"] in prompt
        assert "{ground_truth_map_json}" in prompt  # filled per run
        assert "{audit_type}" not in prompt and "{selection_rules}" not in prompt


def test_valid_rows_pass_without_a_retry():
    rows = [_row("ISMS.1 Sicherheitsmanagement", "Informationsverbund"), _row("SYS.1.1 Allgemeiner Server", "SRV-01")]
    runner = _runner("1. Überwachungsaudit", [{"rows": rows}])

    assert _plan(runner, "auswahlBausteine1Ueberwachungsaudit") == rows
    assert len(runner.ai_client.calls) == 1
    assert "SRV-01" in runner.ai_client.calls[0]["prompt"]  # the map is in the prompt


def test_invalid_row_triggers_one_retry_naming_the_violation():
    bad = [_row("SYS.1.1 Allgemeiner Server", "NET-01")]
    good = [_row("SYS.1.1 Allgemeiner Server", "SRV-01")]
    runner = _runner("Zertifizierungsaudit", [{"rows": bad}, {"rows": good}])

    assert _plan(runner, "auswahlBausteineErstRezertifizierung") == good
    assert len(runner.ai_client.calls) == 2
    retry = runner.ai_client.calls[1]
    assert "(retry)" in retry["log"]
    assert "SYS.1.1" in retry["prompt"] and "NET-01" in retry["prompt"]


def test_rows_still_invalid_after_the_retry_are_dropped():
    keep = _row("ISMS.1 Sicherheitsmanagement", "Informationsverbund")
    unmapped = _row("APP.3.1 Webanwendungen", "SRV-01")  # Baustein absent from the mapping
    runner = _runner("2. Überwachungsaudit", [{"rows": [keep, unmapped]}, {"rows": [keep, unmapped]}])

    assert _plan(runner, "auswahlBausteine2Ueberwachungsaudit") == [keep]


def test_process_layer_baustein_must_point_at_the_informationsverbund():
    runner = _runner("Zertifizierungsaudit", [])
    violations = runner._plan_violations([
        _row("ORP.4 Identitäts- und Berechtigungsmanagement", "SRV-01"),
        _row("CON.3 Datensicherungskonzept", "Informationsverbund"),
    ])
    assert [index for index, _ in violations] == [0]


def test_certification_sample_is_cut_to_six_with_isms1_kept():
    rows = [_row(f"SYS.1.1 Allgemeiner Server", "SRV-01") for _ in range(8)]
    rows.insert(4, _row("ISMS.1 Sicherheitsmanagement", "Informationsverbund"))
    runner = _runner("Zertifizierungsaudit", [{"rows": rows}])

    plan = _plan(runner, "auswahlBausteineErstRezertifizierung")
    assert len(plan) == 6
    assert plan[0]["Baustein"].startswith("ISMS.1")
    assert plan[1:] == rows[:4] + rows[5:6]


def test_surveillance_sample_is_not_cut():
    rows = [_row("ISMS.1 Sicherheitsmanagement", "Informationsverbund")] + [_row("SYS.1.1 Allgemeiner Server", "SRV-01")] * 7
    runner = _runner("1. Überwachungsaudit", [{"rows": rows}])
    assert len(_plan(runner, "auswahlBausteine1Ueberwachungsaudit")) == 8


def test_certification_rule_in_the_prompt_says_exactly_six():
    runner = _runner("Zertifizierungsaudit", [])
    prompt = runner.subchapter_definitions["auswahlBausteineErstRezertifizierung"]["prompt"]
    assert "exactly 6" in prompt and "at least 6" not in prompt


def test_without_a_mapping_the_plan_is_taken_as_is():
    rows = [_row("SYS.1.1 Allgemeiner Server", "whatever")]
    runner = _runner("Zertifizierungsaudit", [{"rows": rows}], ground_truth={})
    assert _plan(runner, "auswahlBausteineErstRezertifizierung") == rows
    assert len(runner.ai_client.calls) == 1
