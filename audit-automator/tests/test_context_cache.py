"""Tests for explicit context caching of the shared document sets.

The properties that matter, because caching sits on the hot path of every stage:
  * one cache per (model, document set) even when dozens of subchapters ask at once
    — the 28-way Chapter-3 gather would otherwise upload the same PDFs 28 times;
  * a set that cannot be cached (below Vertex's 2048-token minimum, unsupported
    model) degrades to direct attachment instead of failing the call;
  * caches created by a run are deleted at the end, since storage is billed.

The SDK client is stubbed; nothing here touches Vertex. Coroutines are driven via
asyncio.run() to avoid a pytest-asyncio dependency.
"""
import asyncio
import types as pytypes

import pytest

pytest.importorskip("google.genai")

from src.clients.context_cache import ContextCacheManager

MODEL = "gemini-3.1-pro"
DOCS = ["gs://b/source/strukturanalyse.pdf", "gs://b/source/netzplan.pdf"]


class _FakeCaches:
    """Records create/delete calls and hands back incrementing resource names."""

    def __init__(self, fail_with=None, delay=0):
        self.created, self.deleted = [], []
        self._fail_with = fail_with
        self._delay = delay

    async def create(self, *, model, config):
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._fail_with:
            raise self._fail_with
        self.created.append({"model": model, "config": config})
        return pytypes.SimpleNamespace(
            name=f"caches/{len(self.created)}",
            usage_metadata=pytypes.SimpleNamespace(total_token_count=4096),
        )

    async def delete(self, *, name):
        self.deleted.append(name)


def _manager(fail_with=None, delay=0, enabled=True):
    caches = _FakeCaches(fail_with=fail_with, delay=delay)
    client = pytypes.SimpleNamespace(aio=pytypes.SimpleNamespace(caches=caches))
    manager = ContextCacheManager(client, "Du bist ein BSI-Auditor.", ttl_seconds=9000, enabled=enabled)
    return manager, caches


def test_same_document_set_is_cached_once_and_reused():
    manager, caches = _manager()

    async def scenario():
        first = await manager.get_or_create(MODEL, DOCS)
        second = await manager.get_or_create(MODEL, list(reversed(DOCS)))  # order must not matter
        return first, second

    first, second = asyncio.run(scenario())
    assert first == second == "caches/1"
    assert len(caches.created) == 1


def test_concurrent_callers_share_one_cache():
    """Chapter-3 gathers 28 subchapters that all want the same PDFs; without a per-key
    lock each would upload them again."""
    manager, caches = _manager(delay=0.01)

    async def scenario():
        return await asyncio.gather(*(manager.get_or_create(MODEL, DOCS) for _ in range(28)))

    names = asyncio.run(scenario())
    assert set(names) == {"caches/1"}
    assert len(caches.created) == 1


def test_different_models_get_their_own_cache():
    """A cache is bound to the model that created it, so the checker model needs its own."""
    manager, caches = _manager()

    async def scenario():
        return (
            await manager.get_or_create(MODEL, DOCS),
            await manager.get_or_create("gemini-3.7-flash", DOCS),
        )

    first, second = asyncio.run(scenario())
    assert first != second
    assert len(caches.created) == 2


def test_the_cache_carries_the_system_instruction_and_the_documents():
    manager, caches = _manager()
    asyncio.run(manager.get_or_create(MODEL, DOCS))

    config = caches.created[0]["config"]
    assert config.system_instruction == "Du bist ein BSI-Auditor."
    assert config.ttl == "9000s"
    uris = [part.file_data.file_uri for part in config.contents[0].parts]
    assert sorted(uris) == sorted(DOCS)


def test_an_uncacheable_set_falls_back_to_direct_attachment():
    """Below Vertex's 2048-token minimum the create call fails; the caller must still
    be able to make its request."""
    manager, caches = _manager(fail_with=RuntimeError("cached content is too small"))
    assert asyncio.run(manager.get_or_create(MODEL, DOCS)) is None


def test_a_failed_creation_is_not_retried_for_every_caller():
    manager, caches = _manager(fail_with=RuntimeError("too small"))

    async def scenario():
        return await asyncio.gather(*(manager.get_or_create(MODEL, DOCS) for _ in range(10)))

    assert asyncio.run(scenario()) == [None] * 10
    assert caches.created == []  # and the failure was only attempted once


def test_no_documents_or_disabled_means_no_cache():
    manager, caches = _manager()
    assert asyncio.run(manager.get_or_create(MODEL, None)) is None
    assert asyncio.run(manager.get_or_create(MODEL, [])) is None

    disabled, disabled_caches = _manager(enabled=False)
    assert asyncio.run(disabled.get_or_create(MODEL, DOCS)) is None
    assert disabled_caches.created == []


def test_invalidated_cache_is_not_handed_out_again():
    manager, caches = _manager()

    async def scenario():
        name = await manager.get_or_create(MODEL, DOCS)
        await manager.invalidate(name)
        return await manager.get_or_create(MODEL, DOCS)

    assert asyncio.run(scenario()) is None


def test_release_all_deletes_every_cache_the_run_created():
    manager, caches = _manager()

    async def scenario():
        await manager.get_or_create(MODEL, DOCS)
        await manager.get_or_create("gemini-3.7-flash", DOCS)
        await manager.release_all()

    asyncio.run(scenario())
    assert sorted(caches.deleted) == ["caches/1", "caches/2"]


def test_release_all_survives_a_delete_failure():
    """Cleanup runs after the audit output is written; it must never mask that."""
    manager, caches = _manager()

    async def failing_delete(*, name):
        raise RuntimeError("permission denied")

    async def scenario():
        await manager.get_or_create(MODEL, DOCS)
        caches.delete = failing_delete
        await manager.release_all()

    asyncio.run(scenario())  # must not raise
