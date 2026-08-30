"""Tests that AiClient actually routes requests through the context cache.

The saving only materialises if a cached request stops carrying the PDFs. These
exercise the seam between AiClient and ContextCacheManager with a stubbed Vertex
call, so they catch a regression where the cache is created but never referenced —
which would cost more than not caching at all.
"""
import asyncio
import types as pytypes

import pytest

pytest.importorskip("google.genai")

from google.genai import errors as genai_errors

from src.clients.ai_client import AiClient
from src.clients.context_cache import ContextCacheManager

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
ANSWER = '{"answer": "Ja"}'
DOCS = ["gs://b/source/strukturanalyse.pdf"]


class _FakeCaches:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.created = []

    async def create(self, *, model, config):
        if not self.enabled:
            raise RuntimeError("cached content is too small")
        self.created.append(model)
        return pytypes.SimpleNamespace(name="caches/1", usage_metadata=None)

    async def delete(self, *, name):
        pass


def _ok_response(text=ANSWER):
    candidate = pytypes.SimpleNamespace(finish_reason=pytypes.SimpleNamespace(name="STOP"))
    return pytypes.SimpleNamespace(candidates=[candidate], text=text)


def _client(caches_enabled=True, fail_first_call_with=None):
    """An AiClient whose Vertex call is stubbed and whose calls are recorded."""
    client = AiClient.__new__(AiClient)
    client.system_message = "Du bist ein BSI-Auditor."
    client.config = pytypes.SimpleNamespace(is_test_mode=False)
    client.semaphore = asyncio.Semaphore(4)
    client.calls = []

    fake_caches = _FakeCaches(enabled=caches_enabled)
    pending_failure = [fail_first_call_with]

    async def fake_generate_content(*, model, contents, config):
        client.calls.append({"model": model, "contents": contents, "config": config})
        if pending_failure[0] is not None:
            error, pending_failure[0] = pending_failure[0], None
            raise error
        return _ok_response()

    sdk = pytypes.SimpleNamespace(
        aio=pytypes.SimpleNamespace(
            caches=fake_caches,
            models=pytypes.SimpleNamespace(generate_content=fake_generate_content),
        )
    )
    client.client = sdk
    client.cache_manager = ContextCacheManager(sdk, client.system_message, ttl_seconds=9000, enabled=True)
    client._fake_caches = fake_caches
    return client


def _run(client, **kwargs):
    return asyncio.run(client.generate_json_response(
        prompt="Liegt eine Sicherheitsleitlinie vor?", json_schema=SCHEMA, **kwargs
    ))


def _file_uris(contents):
    """The gs:// URIs actually attached to a request."""
    uris = []
    for item in contents:
        for part in getattr(item, "parts", []) or ([item] if hasattr(item, "file_data") else []):
            file_data = getattr(part, "file_data", None)
            if file_data and file_data.file_uri:
                uris.append(file_data.file_uri)
    return uris


def test_cached_request_sends_the_prompt_only_not_the_documents():
    client = _client()
    assert _run(client, gcs_uris=DOCS) == {"answer": "Ja"}

    call = client.calls[0]
    assert call["config"].cached_content == "caches/1"
    assert _file_uris(call["contents"]) == [], "documents were re-sent despite the cache"
    assert call["config"].system_instruction is None


def test_uncacheable_documents_are_still_attached():
    client = _client(caches_enabled=False)
    assert _run(client, gcs_uris=DOCS) == {"answer": "Ja"}

    call = client.calls[0]
    assert call["config"].cached_content is None
    assert _file_uris(call["contents"]) == DOCS
    assert call["config"].system_instruction == "Du bist ein BSI-Auditor."


def test_a_second_call_with_the_same_documents_reuses_the_cache():
    client = _client()

    async def scenario():
        await client.generate_json_response("erste Frage", SCHEMA, gcs_uris=DOCS)
        await client.generate_json_response("zweite Frage", SCHEMA, gcs_uris=DOCS)

    asyncio.run(scenario())
    assert len(client._fake_caches.created) == 1
    assert all(c["config"].cached_content == "caches/1" for c in client.calls)


def test_a_stale_cache_falls_back_to_attaching_the_documents():
    """A cache can expire between building the request and sending it; that must cost
    one retry, not the call."""
    stale = genai_errors.APIError.__new__(genai_errors.APIError)
    stale.code = 403
    stale.message = "CachedContent not found"
    stale.status = "PERMISSION_DENIED"
    stale.details = {}

    client = _client(fail_first_call_with=stale)
    assert _run(client, gcs_uris=DOCS) == {"answer": "Ja"}

    assert len(client.calls) == 2
    assert client.calls[0]["config"].cached_content == "caches/1"
    # The retry dropped the cache and carried the documents itself.
    assert client.calls[1]["config"].cached_content is None
    assert _file_uris(client.calls[1]["contents"]) == DOCS


def test_requests_without_documents_never_touch_the_cache():
    client = _client()
    assert _run(client) == {"answer": "Ja"}
    assert client._fake_caches.created == []
    assert client.calls[0]["config"].cached_content is None
