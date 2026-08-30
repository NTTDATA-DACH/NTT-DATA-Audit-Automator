"""Tests for the controller's shared audit state under parallel stages.

Step 1 of a full run gathers four stages that all read-modify-write the same two
files (all_findings.json and the checker log, the Vier-Augen QS trail). The
guarantees under test:

  * re-running over existing findings replaces only the re-run stage's entries —
    no duplicates, and IDs stay stable across saves;
  * findings of concurrently running stages do not overwrite each other;
  * a checker verdict is attributed to the stage that actually produced it;
  * a skipped stage does not erase its own persisted verdicts.

GCS is an in-memory dict; stage runners are stubs. Importing the controller needs
google-genai (AiClient) and PyMuPDF (document_processor).
"""
import asyncio
import json

import pytest

pytest.importorskip("google.genai")
pytest.importorskip("fitz")

from google.cloud.exceptions import NotFound

from src.audit.controller import AuditController
from src.clients.ai_client import current_stage
from src.constants import ALL_FINDINGS_PATH, CHECKER_LOG_PATH, STAGE_RESULTS_PATH


class _FakeGcsClient:
    def __init__(self, stored=None):
        self.stored = dict(stored or {})

    def blob_exists(self, path):
        return path in self.stored

    def read_json(self, path):
        if path not in self.stored:
            raise NotFound(path)
        return json.loads(self.stored[path])

    def upload_from_string(self, content, destination_blob_name):
        self.stored[destination_blob_name] = content

    def write_json(self, data, destination_blob_name):
        self.upload_from_string(json.dumps(data, indent=2, ensure_ascii=False), destination_blob_name)


class _FakeAiClient:
    def __init__(self):
        self.checker_log = []

    def record(self, task):
        """Append a verdict the way _record_checker_verdict does, stage stamp included."""
        self.checker_log.append({
            "stage": current_stage.get(),
            "task": task,
            "freigabe": True,
            "probleme": [],
            "korrektur_uebernommen": False,
        })


def _controller(gcs, ai=None):
    controller = AuditController.__new__(AuditController)
    AuditController.__init__(controller, config=None, gcs_client=gcs, ai_client=ai or _FakeAiClient(), rag_client=None)
    return controller


def _install_stage(controller, stage_name, result, on_run=None):
    """Register a stub runner for `stage_name` that returns `result`."""
    class _Runner:
        STAGE_NAME = stage_name

        def __init__(self, *args, **kwargs):
            pass

        async def run(self, force_overwrite=False):
            await asyncio.sleep(0)  # yield, so parallel stages really interleave
            if on_run:
                on_run()
            await asyncio.sleep(0)
            return result

    controller.stage_runner_classes[stage_name] = _Runner
    controller.runner_dependencies[stage_name] = ()


def _finding(category, description):
    return {"finding": {"category": category, "description": description}}


def _stored_findings(gcs):
    return json.loads(gcs.stored[ALL_FINDINGS_PATH])


def test_rerunning_a_stage_replaces_its_findings_without_duplicating():
    gcs = _FakeGcsClient()
    controller = _controller(gcs)
    _install_stage(controller, "Chapter-1", _finding("AG", "erste Abweichung"))

    asyncio.run(controller.run_single_stage("Chapter-1", force_overwrite=True))
    first = _stored_findings(gcs)
    asyncio.run(controller.run_single_stage("Chapter-1", force_overwrite=True))
    second = _stored_findings(gcs)

    assert len(first) == 1 and len(second) == 1
    assert first[0]["id"] == second[0]["id"] == "AG-1"


def test_parallel_stages_do_not_lose_or_duplicate_each_others_findings():
    gcs = _FakeGcsClient()
    controller = _controller(gcs)
    for i, stage in enumerate(["Chapter-1", "Chapter-3", "Chapter-7"], start=1):
        _install_stage(controller, stage, _finding("AG", f"Abweichung {i}"))

    async def scenario():
        await asyncio.gather(*(
            controller.run_single_stage(stage, force_overwrite=True)
            for stage in ["Chapter-1", "Chapter-3", "Chapter-7"]
        ))

    asyncio.run(scenario())

    stored = _stored_findings(gcs)
    assert {f["source_chapter"] for f in stored} == {"1", "3", "7"}
    assert len(stored) == 3
    assert len({f["id"] for f in stored}) == 3  # every finding got its own ID


def test_parallel_rerun_over_existing_findings_replaces_instead_of_duplicating():
    """The original defect: each parallel stage filtered out only ITS OWN old findings
    from a shared list, so the last one to filter still carried the others' old entries
    and they were written back alongside the fresh ones."""
    gcs = _FakeGcsClient({ALL_FINDINGS_PATH: json.dumps([
        {"id": "AG-1", "category": "AG", "description": "alt aus 1", "source_chapter": "1"},
        {"id": "AG-2", "category": "AG", "description": "alt aus 3", "source_chapter": "3"},
    ])})
    controller = _controller(gcs)
    _install_stage(controller, "Chapter-1", _finding("AG", "neu aus 1"))
    _install_stage(controller, "Chapter-3", _finding("AG", "neu aus 3"))

    async def scenario():
        await asyncio.gather(*(
            controller.run_single_stage(stage, force_overwrite=True) for stage in ["Chapter-1", "Chapter-3"]
        ))

    asyncio.run(scenario())

    stored = _stored_findings(gcs)
    assert [f["description"] for f in sorted(stored, key=lambda f: f["source_chapter"])] == ["neu aus 1", "neu aus 3"]
    assert len({f["id"] for f in stored}) == 2


def test_ids_survive_a_rerun_of_a_single_stage():
    gcs = _FakeGcsClient()
    controller = _controller(gcs)
    _install_stage(controller, "Chapter-1", _finding("AG", "eins"))
    _install_stage(controller, "Chapter-3", _finding("AG", "drei"))

    asyncio.run(controller.run_single_stage("Chapter-1", force_overwrite=True))
    asyncio.run(controller.run_single_stage("Chapter-3", force_overwrite=True))
    before = {f["source_chapter"]: f["id"] for f in _stored_findings(gcs)}

    asyncio.run(controller.run_single_stage("Chapter-3", force_overwrite=True))
    after = {f["source_chapter"]: f["id"] for f in _stored_findings(gcs)}

    assert before["1"] == after["1"], "re-running Chapter-3 renumbered Chapter-1's finding"
    assert len(after) == 2


def test_checker_verdicts_are_attributed_to_the_stage_that_produced_them():
    gcs = _FakeGcsClient()
    ai = _FakeAiClient()
    controller = _controller(gcs, ai)
    for stage in ["Chapter-1", "Chapter-3"]:
        _install_stage(controller, stage, {}, on_run=lambda s=stage: ai.record(f"{s}: task"))

    async def scenario():
        await asyncio.gather(*(
            controller.run_single_stage(stage, force_overwrite=True) for stage in ["Chapter-1", "Chapter-3"]
        ))

    asyncio.run(scenario())

    log = json.loads(gcs.stored[CHECKER_LOG_PATH])
    assert {(e["stage"], e["task"]) for e in log} == {
        ("Chapter-1", "Chapter-1: task"),
        ("Chapter-3", "Chapter-3: task"),
    }
    assert ai.checker_log == []  # everything harvested


def test_a_skipped_stage_keeps_its_persisted_verdicts():
    gcs = _FakeGcsClient()
    ai = _FakeAiClient()
    controller = _controller(gcs, ai)
    _install_stage(controller, "Chapter-1", {"x": 1}, on_run=lambda: ai.record("Chapter-1: task"))

    asyncio.run(controller.run_single_stage("Chapter-1", force_overwrite=True))
    assert len(json.loads(gcs.stored[CHECKER_LOG_PATH])) == 1

    # Second run finds the stage result on disk and skips generation entirely.
    gcs.stored[STAGE_RESULTS_PATH.format(stage_name="Chapter-1")] = json.dumps({"x": 1})
    asyncio.run(controller.run_single_stage("Chapter-1"))

    log = json.loads(gcs.stored[CHECKER_LOG_PATH])
    assert [e["task"] for e in log] == ["Chapter-1: task"], "the no-op re-run stripped the QS trail"


def test_a_finding_without_a_usable_category_does_not_produce_a_none_id():
    gcs = _FakeGcsClient()
    controller = _controller(gcs)
    _install_stage(controller, "Chapter-1", {"finding": {"description": "Kategorie fehlt"}})

    asyncio.run(controller.run_single_stage("Chapter-1", force_overwrite=True))

    stored = _stored_findings(gcs)
    assert stored[0]["id"] == "AG-1"
    assert stored[0]["category"] == "AG"


def test_a_failing_stage_reraises_its_own_error_even_if_saving_fails():
    class _BrokenGcs(_FakeGcsClient):
        def upload_from_string(self, content, destination_blob_name):
            raise RuntimeError("GCS is down")

    controller = _controller(_BrokenGcs())

    class _Runner:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, force_overwrite=False):
            raise ValueError("the real stage error")

    controller.stage_runner_classes["Chapter-1"] = _Runner
    controller.runner_dependencies["Chapter-1"] = ()

    with pytest.raises(ValueError, match="the real stage error"):
        asyncio.run(controller.run_single_stage("Chapter-1", force_overwrite=True))
