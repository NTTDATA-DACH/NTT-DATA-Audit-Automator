"""Tests for the extraction cache and partial-result guarantees of AiRefiner.

Three properties the audit trail depends on:
  * a failed group is never cached — a cached empty/partial result would be served
    on every later run, including --force, and the requirements would be missing
    from chapters 3 and 5 forever;
  * an incomplete extraction is never saved as the final result;
  * --force bypasses the per-Zielobjekt cache.

The AI call and GCS are stubbed; only the orchestration is under test. Importing
the package needs google-genai (AiClient) and PyMuPDF (document_processor).
"""
import asyncio
import json

import pytest

pytest.importorskip("google.genai")
pytest.importorskip("fitz")

from src.audit.stages.gs_extraction.ai_refiner import AiRefiner
from src.constants import EXTRACTED_CHECK_DATA_PATH, GROUPED_BLOCKS_PATH, INDIVIDUAL_RESULTS_PREFIX

SYSTEM_MAP = {"zielobjekte": [
    {"kuerzel": "SVR-01", "name": "Web Server"},
    {"kuerzel": "APP-02", "name": "CRM"},
]}


class _FakeGcsClient:
    def __init__(self, groups, cached=None):
        self.stored = {GROUPED_BLOCKS_PATH: {"zielobjekt_grouped_blocks": groups}}
        self.stored.update(cached or {})

    def blob_exists(self, path):
        return path in self.stored

    async def read_json_async(self, path):
        return self.stored[path]

    async def upload_from_string_async(self, content, destination_blob_name):
        self.stored[destination_blob_name] = json.loads(content)


def _refiner(gcs, per_kuerzel_results):
    """A refiner whose per-chunk AI call replays `per_kuerzel_results` by Kürzel.

    A value that is an Exception is raised on every attempt for that Zielobjekt.
    """
    refiner = AiRefiner.__new__(AiRefiner)
    AiRefiner.__init__(refiner, ai_client=None, gcs_client=gcs)
    refiner.ai_calls = []

    async def fake_call(model_name, prompt, schema, chunk_info):
        kuerzel = chunk_info.split("'")[1]
        refiner.ai_calls.append(kuerzel)
        outcome = per_kuerzel_results[kuerzel]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    refiner._call_ai_model = fake_call
    return refiner


def _run(refiner, force_overwrite=False):
    return asyncio.run(refiner.refine_grouped_blocks_with_ai(SYSTEM_MAP, force_overwrite))


def _one_requirement(req_id, status="Ja"):
    return {"anforderungen": [{"id": req_id, "umsetzungsstatus": status}]}


def test_successful_groups_are_cached_and_assembled():
    gcs = _FakeGcsClient({"SVR-01": [{"blockId": "1"}], "APP-02": [{"blockId": "2"}]})
    refiner = _refiner(gcs, {"SVR-01": _one_requirement("ORP.1.A1"), "APP-02": _one_requirement("ORP.1.A2")})
    _run(refiner)

    saved = gcs.stored[EXTRACTED_CHECK_DATA_PATH]["anforderungen"]
    assert {a["zielobjekt_kuerzel"] for a in saved} == {"SVR-01", "APP-02"}
    assert f"{INDIVIDUAL_RESULTS_PREFIX}SVR-01_result.json" in gcs.stored


def test_a_failed_group_is_neither_cached_nor_saved():
    gcs = _FakeGcsClient({"SVR-01": [{"blockId": "1"}], "APP-02": [{"blockId": "2"}]})
    refiner = _refiner(gcs, {"SVR-01": _one_requirement("ORP.1.A1"), "APP-02": RuntimeError("model down")})

    with pytest.raises(RuntimeError, match="APP-02"):
        _run(refiner)

    assert EXTRACTED_CHECK_DATA_PATH not in gcs.stored
    assert f"{INDIVIDUAL_RESULTS_PREFIX}APP-02_result.json" not in gcs.stored
    # The group that did succeed stays cached, so the re-run is cheap.
    assert f"{INDIVIDUAL_RESULTS_PREFIX}SVR-01_result.json" in gcs.stored


def test_cached_group_is_reused_without_an_ai_call():
    gcs = _FakeGcsClient(
        {"SVR-01": [{"blockId": "1"}]},
        cached={f"{INDIVIDUAL_RESULTS_PREFIX}SVR-01_result.json": _one_requirement("CACHED.A1")},
    )
    refiner = _refiner(gcs, {"SVR-01": _one_requirement("FRESH.A1")})
    _run(refiner)

    assert refiner.ai_calls == []
    assert gcs.stored[EXTRACTED_CHECK_DATA_PATH]["anforderungen"][0]["id"] == "CACHED.A1"


def test_force_overwrite_bypasses_the_cache():
    gcs = _FakeGcsClient(
        {"SVR-01": [{"blockId": "1"}]},
        cached={f"{INDIVIDUAL_RESULTS_PREFIX}SVR-01_result.json": _one_requirement("CACHED.A1")},
    )
    refiner = _refiner(gcs, {"SVR-01": _one_requirement("FRESH.A1")})
    _run(refiner, force_overwrite=True)

    assert refiner.ai_calls == ["SVR-01"]
    assert gcs.stored[EXTRACTED_CHECK_DATA_PATH]["anforderungen"][0]["id"] == "FRESH.A1"
