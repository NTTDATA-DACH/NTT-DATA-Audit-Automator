"""Tests for RagClient document selection and map hygiene.

Covers the three guarantees stages rely on:
  * an empty category yields NO documents (never the whole corpus) — otherwise every
    caller's "document missing" guard is dead code and the model answers about the
    wrong evidence;
  * filenames the classifier invents never enter the map (they would become dead
    gs:// URIs that fail every retry);
  * a cached map is reconciled with the bucket, so documents uploaded later are
    classified and vanished ones are dropped.

Importing the client needs google-genai (via AiClient); skip cleanly if absent.
Coroutines are driven via asyncio.run() to avoid a pytest-asyncio dependency.
"""
import asyncio
import json
import types

import pytest

pytest.importorskip("google.genai")

from src.clients.rag_client import DOC_MAP_PATH, RagClient


class _FakeGcsClient:
    """Minimal in-memory stand-in for the GCS blobs RagClient touches."""

    def __init__(self, files, stored_map=None):
        self._files = list(files)
        self.stored = {}
        if stored_map is not None:
            self.stored[DOC_MAP_PATH] = json.dumps(stored_map)

    def list_files(self, prefix=None):
        return [types.SimpleNamespace(name=name) for name in self._files]

    def blob_exists(self, path):
        return path in self.stored

    def read_json(self, path):
        return json.loads(self.stored[path])

    def upload_from_string(self, content, destination_blob_name):
        self.stored[destination_blob_name] = content


class _FakeAiClient:
    """Returns a canned classification and records the prompts it was given."""

    def __init__(self, document_map):
        self._document_map = document_map
        self.calls = []

    async def generate_json_response(self, prompt, schema, request_context_log=None):
        self.calls.append(prompt)
        return {"document_map": self._document_map}


def _make_client(gcs_client, ai_client=None):
    config = types.SimpleNamespace(bucket_name="test-bucket", is_test_mode=False)
    return RagClient(config, gcs_client, ai_client or _FakeAiClient([]))


def _create(gcs_client, ai_client=None):
    """Build and initialize a client, driving the async factory synchronously."""
    client = _make_client(gcs_client, ai_client)

    async def scenario():
        await client._initialize()

    asyncio.run(scenario())
    return client


def test_empty_category_returns_no_documents():
    gcs = _FakeGcsClient(
        ["source/a.pdf", "source/b.pdf"],
        stored_map={"document_map": [
            {"filename": "source/a.pdf", "category": "Strukturanalyse"},
            {"filename": "source/b.pdf", "category": "Strukturanalyse"},
        ]},
    )
    client = _create(gcs)

    # The category holds nothing: callers must see that, not the whole corpus.
    assert client.get_gcs_uris_for_categories(["Risikoanalyse"]) == []
    # A populated category still resolves.
    assert client.get_gcs_uris_for_categories(["Strukturanalyse"]) == [
        "gs://test-bucket/source/a.pdf",
        "gs://test-bucket/source/b.pdf",
    ]


def test_no_categories_still_returns_all_documents():
    gcs = _FakeGcsClient(["source/a.pdf"], stored_map={"document_map": []})
    client = _create(gcs)
    assert client.get_gcs_uris_for_categories(None) == ["gs://test-bucket/source/a.pdf"]


def test_hallucinated_filenames_are_dropped_from_the_map():
    gcs = _FakeGcsClient(["source/real.pdf"])
    ai = _FakeAiClient([
        {"filename": "real.pdf", "category": "Strukturanalyse"},
        {"filename": "invented.pdf", "category": "Risikoanalyse"},
    ])
    client = _create(gcs, ai)

    assert client.get_gcs_uris_for_categories(["Strukturanalyse"]) == ["gs://test-bucket/source/real.pdf"]
    assert client.get_gcs_uris_for_categories(["Risikoanalyse"]) == []
    assert json.loads(gcs.stored[DOC_MAP_PATH])["document_map"] == [
        {"filename": "source/real.pdf", "category": "Strukturanalyse"}
    ]


def test_cached_map_is_reconciled_with_the_bucket():
    gcs = _FakeGcsClient(
        ["source/known.pdf", "source/uploaded_later.pdf"],
        stored_map={"document_map": [
            {"filename": "source/known.pdf", "category": "Strukturanalyse"},
            {"filename": "source/deleted.pdf", "category": "Modellierung"},
        ]},
    )
    ai = _FakeAiClient([{"filename": "uploaded_later.pdf", "category": "Risikoanalyse"}])
    client = _create(gcs, ai)

    # The new file was classified, the vanished one dropped.
    assert client.get_gcs_uris_for_categories(["Risikoanalyse"]) == ["gs://test-bucket/source/uploaded_later.pdf"]
    assert client.get_gcs_uris_for_categories(["Modellierung"]) == []
    # Only the unclassified file was sent to the model, not the whole corpus.
    assert len(ai.calls) == 1 and "uploaded_later.pdf" in ai.calls[0] and "known.pdf" not in ai.calls[0]
    # The healed map was written back.
    persisted = {item["filename"] for item in json.loads(gcs.stored[DOC_MAP_PATH])["document_map"]}
    assert persisted == {"source/known.pdf", "source/uploaded_later.pdf"}


def test_unchanged_map_is_not_rewritten_or_reclassified():
    gcs = _FakeGcsClient(
        ["source/known.pdf"],
        stored_map={"document_map": [{"filename": "source/known.pdf", "category": "Strukturanalyse"}]},
    )
    ai = _FakeAiClient([])
    before = gcs.stored[DOC_MAP_PATH]
    _create(gcs, ai)

    assert ai.calls == []
    assert gcs.stored[DOC_MAP_PATH] == before
