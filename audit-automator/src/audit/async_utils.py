# src/audit/async_utils.py
"""Shared concurrency helpers.

MAX-7: the project standardizes on two explicit `asyncio.gather` policies so the
choice is never accidental:

* `gather_resilient(...)` — for independent per-item batches (AI calls, per-document
  extraction). Runs every task to completion (`return_exceptions=True`), logs each
  failure, and returns only the successful results. One bad item never cancels its
  siblings or discards their already-completed (often expensive) work.

* a bare `asyncio.gather(...)` — kept deliberately for all-or-nothing pipeline steps
  where a missing item would corrupt the merged output (e.g. PDF chunk upload /
  Document AI per-chunk) or where downstream stages depend on completion. Those
  sites carry a comment explaining the fail-fast choice.
"""
import asyncio
import logging
from typing import Any, Awaitable, List


async def gather_resilient(*awaitables: Awaitable[Any], context: str) -> List[Any]:
    """Run awaitables concurrently without cancel-all-on-first-failure.

    Args:
        *awaitables: The coroutines/awaitables to run concurrently.
        context: A short label identifying the batch, used in failure logs.

    Returns:
        The successful results, in the original task order. Failed tasks are
        logged against `context` and omitted (never raised), so a single failure
        does not abort the batch or throw away completed work.
    """
    results = await asyncio.gather(*awaitables, return_exceptions=True)
    successful: List[Any] = []
    for idx, result in enumerate(results):
        if isinstance(result, Exception):
            logging.error(
                f"[{context}] Concurrent task {idx + 1}/{len(results)} failed: {result}",
                exc_info=result,
            )
        else:
            successful.append(result)
    return successful
