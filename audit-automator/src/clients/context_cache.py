# src/clients/context_cache.py
"""Explicit context caching for the document sets that Vertex AI calls share.

The audit sends the same customer PDFs over and over: `Strukturanalyse` alone is the
context for 10 Chapter-3 subchapters, `Schutzbedarfsfeststellung` for 8, and every
checked answer sends its documents a second time for the checker's second opinion.
Re-uploading and re-tokenising those PDFs per call is both the largest avoidable cost
in a run and the slowest part of each request.

A context cache pins one (model, document set) pair server-side. Later calls reference
it by name and send only their own prompt: the cached tokens bill at a fraction of
fresh input tokens and are not re-processed, so the same run gets cheaper and faster.

Caching is strictly an optimisation. Every failure path — a document set below the
2048-token minimum, an unsupported model, a cache that expired mid-run — falls back to
attaching the documents to the request exactly as before.
"""
import asyncio
import hashlib
import logging
from typing import Dict, List, Optional, Tuple

from google.genai import types

# Vertex requires at least this many tokens before a context cache may be created.
# We cannot count tokens up front without an extra round trip, so a set that is too
# small simply fails to cache and is remembered as uncacheable.
MIN_CACHEABLE_TOKENS = 2048

CacheKey = Tuple[str, Tuple[str, ...]]


class ContextCacheManager:
    """Creates and reuses Vertex context caches, keyed by (model, document set)."""

    def __init__(self, client, system_instruction: str, ttl_seconds: int, enabled: bool = True):
        self.client = client
        self.system_instruction = system_instruction
        self.ttl_seconds = ttl_seconds
        self.enabled = enabled
        # None as a value means "this set was tried and cannot be cached" — remembering
        # that is what stops 28 concurrent subchapters from each retrying the failure.
        self._caches: Dict[CacheKey, Optional[str]] = {}
        self._locks: Dict[CacheKey, asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()
        self._hits = 0

    @staticmethod
    def _key(model: str, gcs_uris: List[str]) -> CacheKey:
        # Sorted, so two callers listing the same documents in a different order share
        # one cache. The model is part of the key: a cache is bound to the model that
        # created it and cannot be used by another.
        return model, tuple(sorted(gcs_uris))

    async def get_or_create(self, model: str, gcs_uris: Optional[List[str]]) -> Optional[str]:
        """Returns the resource name of a cache holding `gcs_uris`, or None.

        None means the caller should attach the documents to the request itself.
        """
        if not self.enabled or not gcs_uris:
            return None

        key = self._key(model, gcs_uris)
        async with self._registry_lock:
            if key in self._caches:
                cached_name = self._caches[key]
                if cached_name:
                    self._hits += 1
                return cached_name
            lock = self._locks.setdefault(key, asyncio.Lock())

        # Only one task per key builds the cache; the stages gather dozens of calls that
        # all want the same documents, and each creation uploads the whole set.
        async with lock:
            if key in self._caches:
                return self._caches[key]
            cache_name = await self._create(model, list(key[1]))
            self._caches[key] = cache_name
            return cache_name

    async def _create(self, model: str, gcs_uris: List[str]) -> Optional[str]:
        """Creates one cache; returns None if this set cannot be cached."""
        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_uri(file_uri=uri, mime_type="application/pdf") for uri in gcs_uris],
            )
        ]
        digest = hashlib.sha256("|".join(gcs_uris).encode("utf-8")).hexdigest()[:12]
        try:
            cached = await self.client.aio.caches.create(
                model=model,
                config=types.CreateCachedContentConfig(
                    contents=contents,
                    system_instruction=self.system_instruction,
                    ttl=f"{self.ttl_seconds}s",
                    display_name=f"bsi-audit-{digest}",
                ),
            )
        except Exception as e:  # noqa: BLE001 — caching is optional, never fatal
            logging.info(
                f"Not caching {len(gcs_uris)} document(s) ({type(e).__name__}: {e}). "
                f"They are attached per request instead. Sets below "
                f"{MIN_CACHEABLE_TOKENS} tokens cannot be cached."
            )
            return None

        token_count = getattr(getattr(cached, "usage_metadata", None), "total_token_count", None)
        logging.info(
            f"Cached {len(gcs_uris)} document(s) as '{cached.name}'"
            + (f" ({token_count} tokens)" if token_count else "")
            + f", TTL {self.ttl_seconds}s."
        )
        return cached.name

    async def invalidate(self, cache_name: str) -> None:
        """Forgets a cache that the API rejected, so callers stop referencing it."""
        async with self._registry_lock:
            for key, name in list(self._caches.items()):
                if name == cache_name:
                    self._caches[key] = None
                    logging.warning(f"Context cache '{cache_name}' is no longer usable; falling back to direct attachment.")

    async def release_all(self) -> None:
        """Deletes every cache this run created.

        Caches bill for as long as they are stored, so a run must not leave its own
        behind. Deletion failures are logged, never raised: by the time this runs the
        audit output is already written and a leftover cache expires on its own TTL.
        """
        async with self._registry_lock:
            names = [name for name in self._caches.values() if name]
            self._caches.clear()
            self._locks.clear()

        if not names:
            return
        for name in names:
            try:
                await self.client.aio.caches.delete(name=name)
            except Exception as e:  # noqa: BLE001
                logging.warning(f"Could not delete context cache '{name}': {e}. It expires on its own TTL.")
        logging.info(f"Released {len(names)} context cache(s); they were reused {self._hits} time(s) this run.")
