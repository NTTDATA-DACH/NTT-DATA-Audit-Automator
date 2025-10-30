# bsi-audit-automator/src/audit/stages/gs_extraction/cache_manager.py
import logging
import json
from typing import Dict, Any, Optional

from src.clients.gcs_client import GcsClient
from src.constants import INDIVIDUAL_RESULTS_PREFIX


class CacheManager:
    """Handles caching operations for AI refinement results."""

    def __init__(self, gcs_client: GcsClient):
        self.gcs_client = gcs_client

    async def get_cached_result(self, kuerzel: str) -> Optional[Dict[str, Any]]:
        """Check if we have a cached result for this kürzel."""
        cache_path = f"{INDIVIDUAL_RESULTS_PREFIX}{kuerzel}_result.json"
        if self.gcs_client.blob_exists(cache_path):
            try:
                cached_result = await self.gcs_client.read_json_async(cache_path)
                logging.info(f"Using cached result for Zielobjekt '{kuerzel}'")
                return cached_result
            except Exception as e:
                logging.warning(f"Failed to read cached result for '{kuerzel}': {e}")
        return None

    async def save_result_to_cache(self, kuerzel: str, result_data: Dict[str, Any]):
        """Save individual result to cache."""
        cache_path = f"{INDIVIDUAL_RESULTS_PREFIX}{kuerzel}_result.json"
        try:
            await self.gcs_client.upload_from_string_async(
                json.dumps(result_data, indent=2, ensure_ascii=False), cache_path
            )
            logging.debug(f"Cached result for Zielobjekt '{kuerzel}' to {cache_path}")
        except Exception as e:
            logging.error(f"Failed to cache result for '{kuerzel}': {e}")