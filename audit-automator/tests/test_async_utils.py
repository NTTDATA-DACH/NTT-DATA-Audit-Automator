"""Tests for the shared concurrency helper (MAX-7).

Dependency-light: exercises src.audit.async_utils only (pure asyncio + logging),
so no GCP env or cloud SDKs are required. Async coroutines are driven via
asyncio.run() to avoid a pytest-asyncio dependency.
"""
import asyncio

from src.audit.async_utils import gather_resilient


def test_gather_resilient_returns_only_successes_in_order():
    """A failing task is dropped; the others still return, in original order."""
    async def ok(value):
        return value

    async def boom():
        raise RuntimeError("task failed")

    async def scenario():
        return await gather_resilient(ok(1), boom(), ok(3), context="test")

    result = asyncio.run(scenario())
    assert result == [1, 3]


def test_gather_resilient_all_success():
    async def ok(value):
        return value

    async def scenario():
        return await gather_resilient(ok("a"), ok("b"), context="test")

    assert asyncio.run(scenario()) == ["a", "b"]


def test_gather_resilient_all_fail_returns_empty():
    """All tasks failing must not raise — it returns an empty list."""
    async def boom():
        raise ValueError("nope")

    async def scenario():
        return await gather_resilient(boom(), boom(), context="test")

    assert asyncio.run(scenario()) == []
