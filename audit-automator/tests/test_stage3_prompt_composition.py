"""Tests that every question-bearing Chapter-3 task actually shows its questions.

The schemas demand one answer per template question, so a prompt that omits the
questions makes the model produce positionally meaningless booleans that the report
generator then maps 1:1 onto the template — a silent, every-run defect. A custom
prompt must therefore EXTEND the generic question block, never replace it.

Importing the stage module needs google-genai (via AiClient); skip if absent.
"""
import json

import pytest

pytest.importorskip("google.genai")

from src.audit.stages.stage_3_dokumentenpruefung import Chapter3Runner
from src.constants import PROMPT_CONFIG_PATH

TEMPLATE_PATH = "assets/json/master_report_template.json"


def _runner(prompt_config=None):
    """A Chapter3Runner with only the attributes _create_task_from_section reads."""
    runner = Chapter3Runner.__new__(Chapter3Runner)
    if prompt_config is None:
        with open(PROMPT_CONFIG_PATH, encoding="utf-8") as f:
            prompt_config = json.load(f)
    runner.prompt_config = prompt_config
    return runner


def _section(*question_texts):
    return {"content": [{"type": "question", "questionText": q} for q in question_texts]}


def _rendered(task):
    return task["prompt"].format(questions=task.get("questions_formatted", ""))


def test_custom_prompt_is_extended_with_the_questions_block():
    config = {"stages": {"Chapter-3": {
        "generic_question": {"prompt": "The questions to answer are:\n{questions}"},
        "someKey": {"schema_path": "s.json", "prompt": "Extra context sentence."},
    }}}
    task = _runner(config)._create_task_from_section("someKey", _section("Frage A?", "Frage B?"))

    rendered = _rendered(task)
    assert "Extra context sentence." in rendered
    assert "1. Frage A?" in rendered and "2. Frage B?" in rendered


def test_custom_prompt_owning_the_placeholder_is_left_alone():
    config = {"stages": {"Chapter-3": {
        "generic_question": {"prompt": "The questions to answer are:\n{questions}"},
        "someKey": {"schema_path": "s.json", "prompt": "Custom with slot:\n{questions}\nend."},
    }}}
    task = _runner(config)._create_task_from_section("someKey", _section("Frage A?"))

    rendered = _rendered(task)
    assert rendered.count("Frage A?") == 1
    assert rendered.endswith("end.")


def test_task_without_questions_keeps_its_custom_prompt():
    config = {"stages": {"Chapter-3": {
        "generic_question": {"prompt": "The questions to answer are:\n{questions}"},
        "someKey": {"schema_path": "s.json", "prompt": "Standalone instruction."},
    }}}
    task = _runner(config)._create_task_from_section("someKey", _section())
    assert task["prompt"] == "Standalone instruction."


def test_shipped_config_shows_every_question_of_every_ai_subchapter():
    """Regression guard against a real prompt losing its questions again (3.1 did)."""
    runner = _runner()
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        template = json.load(f)
    sections = template["bsiAuditReport"]["dokumentenpruefung"]

    checked = 0
    for key, data in sections.items():
        if not isinstance(data, dict) or "content" not in data:
            continue
        questions = [i["questionText"] for i in data["content"] if i.get("type") == "question"]
        if not questions:
            continue
        task = runner._create_task_from_section(key, data)
        if task is None or task["type"] != "ai_driven":
            continue
        rendered = _rendered(task)
        for question in questions:
            assert question in rendered, f"Chapter-3 '{key}' never shows the model: {question}"
        checked += 1

    assert checked > 0, "no question-bearing AI subchapters were checked"
