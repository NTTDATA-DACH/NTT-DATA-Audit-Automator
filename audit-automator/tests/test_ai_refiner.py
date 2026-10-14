"""Tests for the AI refiner's work split: cache resume → batch tier → standard calls.

Offline: the AI client and the GCS-backed cache are fakes, so the test checks the
orchestration only — which chunks go to the batch, which fall back to standard calls,
and that every Zielobjekt ends up cached exactly once. Importing the refiner needs
google-genai + jsonschema (via AiClient); skip cleanly if absent.
"""
import asyncio
import json

import pytest

pytest.importorskip("google.genai")
pytest.importorskip("fitz")  # the gs_extraction package imports PyMuPDF at package level

from src.audit.stages.gs_extraction.ai_refiner import AiRefiner
from src.audit.stages.gs_extraction.chunk_processor import ChunkProcessor

SCHEMA = {"type": "object", "properties": {"anforderungen": {"type": "array"}}}
TEMPLATE = "Extract:\n{zielobjekt_blocks_json}"


def _blocks(n):
    return [{"blockId": i, "textBlock": {"text": f"Anforderung {i}"}} for i in range(n)]


class FakeAiClient:
    def __init__(self, batch_results):
        self.batch_results = batch_results
        self.batch_items = None
        self.standard_calls = []

    async def generate_json_batch(self, items, json_schema, thinking_level=None, request_context_log=""):
        self.batch_items = dict(items)
        return {k: v for k, v in self.batch_results.items() if k in items}

    async def generate_json_response(self, prompt, json_schema, gcs_uris=None, request_context_log="",
                                     max_retries=None, thinking_level=None):
        self.standard_calls.append((request_context_log, thinking_level))
        return {"anforderungen": [{"id": "STD", "zielobjekt_kuerzel": "x"}]}


class FakeGcs:
    def __init__(self, existing=None):
        self.blobs = dict(existing or {})

    def blob_exists(self, name):
        return name in self.blobs

    async def read_json_async(self, name):
        return json.loads(self.blobs[name])

    async def upload_from_string_async(self, content, destination_blob_name, content_type="application/json"):
        self.blobs[destination_blob_name] = content

    async def write_json_async(self, data, destination_blob_name):
        self.blobs[destination_blob_name] = json.dumps(data, ensure_ascii=False)


def _refiner(ai, gcs):
    refiner = AiRefiner.__new__(AiRefiner)
    refiner.ai_client = ai
    refiner.gcs_client = gcs
    from src.audit.stages.gs_extraction.cache_manager import CacheManager
    from src.audit.stages.gs_extraction.data_processor import DataProcessor
    refiner.cache_manager = CacheManager(gcs)
    refiner.chunk_processor = ChunkProcessor()
    refiner.data_processor = DataProcessor()
    return refiner


def test_cached_batch_and_standard_paths_are_combined():
    from src.constants import INDIVIDUAL_RESULTS_PREFIX

    gcs = FakeGcs({f"{INDIVIDUAL_RESULTS_PREFIX}CACHED_result.json": '{"anforderungen": [{"id": "C"}]}'})
    # Zielobjekt BIG spans two chunks; the batch delivers one of them and the single-chunk one.
    ai = FakeAiClient({"BIG#0": {"anforderungen": [{"id": "B0"}]}, "ONE#0": {"anforderungen": [{"id": "O"}]}})
    refiner = _refiner(ai, gcs)
    groups = {"CACHED": _blocks(3), "BIG": _blocks(ChunkProcessor.MAX_BLOCKS_PER_CHUNK + 50), "ONE": _blocks(2)}
    names = {"CACHED": "Cached", "BIG": "Big", "ONE": "One"}

    results = asyncio.run(refiner._process_all_groups(groups, names, TEMPLATE, SCHEMA))
    by_kuerzel = {k: (name, res) for k, name, res in results}

    # The cached Zielobjekt never reached the AI client.
    assert by_kuerzel["CACHED"] == ("Cached", {"anforderungen": [{"id": "C"}]})
    assert not any(k.startswith("CACHED") for k in ai.batch_items)
    # The batch saw every open chunk, keyed per chunk.
    assert set(ai.batch_items) == {"BIG#0", "BIG#1", "ONE#0"}
    # Only the undelivered chunk went through standard calls.
    assert len(ai.standard_calls) == 1
    assert "chunk 2/2 for 'BIG'" in ai.standard_calls[0][0]
    assert by_kuerzel["ONE"][1] == {"anforderungen": [{"id": "O"}]}
    ids = sorted(a["id"] for a in by_kuerzel["BIG"][1]["anforderungen"])
    assert ids == ["B0", "STD"]
    # Every newly processed Zielobjekt is cached for the next run; the cached one is untouched.
    assert f"{INDIVIDUAL_RESULTS_PREFIX}BIG_result.json" in gcs.blobs
    assert f"{INDIVIDUAL_RESULTS_PREFIX}ONE_result.json" in gcs.blobs


def test_invalid_batch_result_falls_back_to_standard_call():
    ai = FakeAiClient({"X#0": {"wrong": "shape"}})
    refiner = _refiner(ai, FakeGcs())
    results = asyncio.run(refiner._process_all_groups({"X": _blocks(2)}, {"X": "x"}, TEMPLATE, SCHEMA))
    assert results[0][2]["anforderungen"][0]["id"] == "STD"
    assert len(ai.standard_calls) == 1


def test_batch_prompt_equals_standard_prompt():
    """Both paths must build the same prompt, or the batch result would answer a different question."""
    ai = FakeAiClient({})
    refiner = _refiner(ai, FakeGcs())
    chunk = refiner.chunk_processor.preprocess_blocks_for_ai(_blocks(2))
    expected = refiner._build_chunk_prompt(chunk, 0, 1, TEMPLATE)
    asyncio.run(refiner._run_batch({"X": [_blocks(2)]}, TEMPLATE, SCHEMA))
    assert ai.batch_items == {"X#0": expected}
