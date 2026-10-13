# src/assets_loader.py
"""Loading of the bundled JSON assets (prompt config, schemas, report template).

The assets ship inside the image and never change while the process runs, so every
file is read once and served from an LRU cache afterwards. A single loader also
means one place decides encoding and error handling — seven near-identical
`_load_asset_json` methods used to make that a per-module accident.
"""
import json
from functools import lru_cache
from typing import Any, Dict


@lru_cache(maxsize=None)
def _load_cached(path: str) -> str:
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def load_asset_json(path: str) -> Dict[str, Any]:
    """Loads a bundled JSON asset.

    The parsed result is deliberately NOT cached: callers pass schemas straight into
    the SDK, which mutates them (it strips `$schema`), and prompt configs are indexed
    into. Only the file read is cached, which is what actually costs.
    """
    return json.loads(_load_cached(path))
