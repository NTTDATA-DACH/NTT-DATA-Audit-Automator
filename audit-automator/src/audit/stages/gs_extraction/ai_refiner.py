# bsi-audit-automator/src/audit/stages/gs_extraction/ai_refiner.py
import asyncio
import logging
import json
import os
from typing import Dict, Any, List, Tuple, Optional

from src.assets_loader import load_asset_json
from src.clients.ai_client import AiClient
from src.clients.gcs_client import GcsClient
from src.audit.async_utils import gather_resilient
from src.constants import (
    GROUPED_BLOCKS_PATH,
    EXTRACTED_CHECK_DATA_PATH,
    CHUNK_THINKING_LEVEL,
    THINKING_LEVEL,
    PROMPT_CONFIG_PATH,
)

from .cache_manager import CacheManager
from .chunk_processor import ChunkProcessor
from .data_processor import DataProcessor


class AiRefiner:
    """
    Orchestrates the AI refinement process for grouped blocks to extract structured security requirements.
    Delegates specific responsibilities to specialized components.

    Order of work per run: Zielobjekte with a cached result are skipped, every open chunk is
    sent through the batch tier in one job, and whatever the batch did not deliver in time
    runs through standard calls (2 attempts at the chunk thinking level, then 2 at the full
    document thinking level: same model, more thinking).
    """

    def __init__(self, ai_client: AiClient, gcs_client: GcsClient):
        self.ai_client = ai_client
        self.gcs_client = gcs_client
        self.cache_manager = CacheManager(gcs_client)
        self.chunk_processor = ChunkProcessor()
        self.data_processor = DataProcessor()
        self.prompt_config = load_asset_json(PROMPT_CONFIG_PATH)


    async def refine_grouped_blocks_with_ai(self, system_map: Dict[str, Any], force_overwrite: bool):
        """
        Process grouped blocks with AI to extract structured requirements.

        Args:
            system_map: Ground truth map containing zielobjekte information
            force_overwrite: If True, reprocess even if output exists
        """
        if not force_overwrite and self.gcs_client.blob_exists(EXTRACTED_CHECK_DATA_PATH):
            logging.info(f"Final extracted check results file exists. Skipping AI refinement.")
            return

        logging.info("Refining grouped blocks with AI to extract structured requirements...")

        # Load grouped blocks and configuration
        grouped_blocks_data = await self.gcs_client.read_json_async(GROUPED_BLOCKS_PATH)
        groups = grouped_blocks_data.get("zielobjekt_grouped_blocks", {})

        refine_config = self.prompt_config["stages"]["Chapter-3"]["refine_layout_parser_group"]
        prompt_template = refine_config["prompt"]
        schema = load_asset_json(refine_config["schema_path"])

        zielobjekt_map = {z['kuerzel']: z['name'] for z in system_map.get("zielobjekte", [])}

        # Filter valid groups and apply test mode limiting
        valid_groups = {k: v for k, v in groups.items() if k != "_UNGROUPED_" and v}

        if not valid_groups:
            logging.warning("No valid Zielobjekt groups found for processing")
            final_output = {"anforderungen": []}
        else:
            # Apply test mode limiting
            if os.getenv("TEST", "false").lower() == "true":
                limited_groups = dict(list(valid_groups.items())[:3])
                logging.info(f"Test mode: Processing only {len(limited_groups)} of {len(valid_groups)} groups")
                valid_groups = limited_groups

            logging.info(f"Processing {len(valid_groups)} Zielobjekt groups...")

            # Process all groups
            results = await self._process_all_groups(valid_groups, zielobjekt_map, prompt_template, schema, force_overwrite)

            # A partial extraction must never be persisted: it becomes the pinned final
            # result and every later stage would audit a document with requirements
            # silently missing. Successful groups stay cached, so a re-run is cheap.
            extracted_kuerzel = {kuerzel for kuerzel, _name, data in results if data is not None}
            failed_kuerzel = sorted(set(valid_groups) - extracted_kuerzel)
            if failed_kuerzel:
                raise RuntimeError(
                    f"AI refinement failed for {len(failed_kuerzel)} of {len(valid_groups)} Zielobjekte: "
                    f"{failed_kuerzel}. Refusing to save an incomplete Grundschutz-Check extraction."
                )

            # Assemble final results
            final_output = self.data_processor.assemble_final_results(results)

        # Save consolidated results
        await self.gcs_client.write_json_async(final_output, EXTRACTED_CHECK_DATA_PATH)
        logging.info(f"Saved final refined check data with {len(final_output['anforderungen'])} requirements")

    @staticmethod
    def _chunk_key(kuerzel: str, chunk_idx: int) -> str:
        return f"{kuerzel}#{chunk_idx}"

    async def _process_all_groups(self, valid_groups: Dict[str, List[Dict]], zielobjekt_map: Dict[str, str],
                                 prompt_template: str, schema: Dict[str, Any],
                                 force_overwrite: bool = False) -> List[Tuple[str, str, Optional[Dict[str, Any]]]]:
        """Resume from the per-Zielobjekt cache, batch the open chunks, finish the rest with standard calls.

        --force must reach this layer too: the cache is keyed by Kürzel alone, so without it a
        replaced source document would be answered from the previous document's extraction
        forever. The caller checks the returned groups against valid_groups and fails the stage
        on any missing one.
        """
        async def lookup(kuerzel: str) -> Tuple[str, Optional[Dict[str, Any]]]:
            if force_overwrite:
                return kuerzel, None
            try:
                return kuerzel, await self.cache_manager.get_cached_result(kuerzel)
            except Exception as e:  # noqa: BLE001 — a failed lookup means "not cached", never a lost run
                logging.warning(f"Cache lookup for Zielobjekt '{kuerzel}' failed ({e}); processing it afresh.")
                return kuerzel, None

        done: List[Tuple[str, str, Optional[Dict[str, Any]]]] = []
        pending: Dict[str, List[List[Dict]]] = {}
        for kuerzel, cached_result in await asyncio.gather(*(lookup(k) for k in valid_groups)):
            if cached_result is not None:
                done.append((kuerzel, zielobjekt_map.get(kuerzel, "Unbekannt"), cached_result))
            else:
                pending[kuerzel] = self.chunk_processor.chunk_blocks(valid_groups[kuerzel])
        logging.info(f"Resume: {len(done)} Zielobjekt(e) from cache, {len(pending)} pending.")
        if not pending:
            return done

        batch_results = await self._run_batch(pending, prompt_template, schema)

        tasks = [
            self._process_group(kuerzel, chunks, zielobjekt_map, prompt_template, schema, batch_results)
            for kuerzel, chunks in pending.items()
        ]
        # Resilient: a single failed Zielobjekt group must not discard the rest.
        done.extend(await gather_resilient(*tasks, context="ai_refiner: process_all_groups"))
        return done

    async def _run_batch(self, pending: Dict[str, List[List[Dict]]], prompt_template: str,
                         schema: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Send every open chunk through the batch tier; returns chunk key -> valid result."""
        items: Dict[str, str] = {}
        for kuerzel, chunks in pending.items():
            for idx, chunk in enumerate(chunks):
                clean_chunk = self.chunk_processor.preprocess_blocks_for_ai(chunk)
                items[self._chunk_key(kuerzel, idx)] = self._build_chunk_prompt(clean_chunk, idx, len(chunks), prompt_template)

        results = await self.ai_client.generate_json_batch(
            items=items,
            json_schema=schema,
            thinking_level=CHUNK_THINKING_LEVEL,
            request_context_log="RefineGroup batch",
        )
        valid = {key: result for key, result in results.items() if self._is_valid_result(result)}
        logging.info(
            f"Batch tier: {len(valid)} of {len(items)} chunk(s) delivered a valid result; "
            f"{len(items) - len(valid)} run as standard calls."
        )
        return valid

    async def _process_group(self, kuerzel: str, chunks: List[List[Dict]], zielobjekt_map: Dict[str, str],
                             prompt_template: str, schema: Dict[str, Any],
                             batch_results: Dict[str, Dict[str, Any]]) -> Tuple[str, str, Optional[Dict[str, Any]]]:
        """Process one Zielobjekt from its chunks and cache the merged result."""
        name = zielobjekt_map.get(kuerzel, "Unbekannt")
        try:
            if len(chunks) == 1:
                # Single chunk - process normally
                result = await self._process_single_chunk(
                    kuerzel, chunks[0], 0, 1, prompt_template, schema,
                    precomputed=batch_results.get(self._chunk_key(kuerzel, 0)),
                )
            else:
                # Multiple chunks - process each and merge results
                logging.info(f"Processing {len(chunks)} chunks for Zielobjekt '{kuerzel}'")
                chunk_tasks = [
                    self._process_single_chunk(
                        kuerzel, chunk, idx, len(chunks), prompt_template, schema,
                        precomputed=batch_results.get(self._chunk_key(kuerzel, idx)),
                    )
                    for idx, chunk in enumerate(chunks)
                ]
                # Resilient so completed chunks are logged, but a shortfall still fails
                # the group below — a partial merge would be cached as the whole truth.
                chunk_results = await gather_resilient(*chunk_tasks, context=f"ai_refiner: chunks for '{kuerzel}'")
                if len(chunk_results) < len(chunks):
                    raise RuntimeError(
                        f"Only {len(chunk_results)} of {len(chunks)} chunks could be extracted for '{kuerzel}'"
                    )

                # Merge all anforderungen from chunks
                all_anforderungen = []
                for chunk_result in chunk_results:
                    if chunk_result and "anforderungen" in chunk_result:
                        all_anforderungen.extend(chunk_result["anforderungen"])

                result = {"anforderungen": all_anforderungen}
                logging.info(f"Merged {len(all_anforderungen)} requirements from {len(chunks)} chunks for '{kuerzel}'")

            # Cache the result
            if result:
                await self.cache_manager.save_result_to_cache(kuerzel, result)

            return kuerzel, name, result

        except Exception as e:
            logging.error(f"Complete processing failed for Zielobjekt '{kuerzel}': {e}")
            return kuerzel, name, None

    async def _process_single_chunk(self, kuerzel: str, chunk: List[Dict], chunk_idx: int, total_chunks: int,
                                   prompt_template: str, schema: Dict[str, Any],
                                   precomputed: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Process a single chunk: a batch result if there is one, else the 2+2 attempt pattern.

        Raises:
            RuntimeError: if all four attempts fail. An empty result would be
            indistinguishable from a chunk that genuinely holds no requirements and
            would be cached as the final answer for this Zielobjekt.
        """
        if precomputed is not None:
            logging.info(f"Using batch result for chunk {chunk_idx + 1}/{total_chunks} for '{kuerzel}' ({len(chunk)} blocks)")
            return precomputed

        # Preprocess blocks
        clean_chunk = self.chunk_processor.preprocess_blocks_for_ai(chunk)

        # Build prompt
        prompt = self._build_chunk_prompt(clean_chunk, chunk_idx, total_chunks, prompt_template)

        # Log chunk info
        total_chars = sum(len(str(block)) for block in clean_chunk)
        chunk_info = f"chunk {chunk_idx + 1}/{total_chunks} for '{kuerzel}' ({len(clean_chunk)} blocks, ~{total_chars:,} chars)"
        logging.info(f"Processing {chunk_info}")

        # Two attempts at the bulk thinking level, then two at the full document level. Same
        # model: on gemini-4-argon the escalation is more thinking, not a second model.
        bulk_result = await self._try_level_with_retries(
            thinking_level=CHUNK_THINKING_LEVEL,
            prompt=prompt,
            schema=schema,
            chunk_info=chunk_info,
            attempts=2,
        )

        if bulk_result:
            return bulk_result

        logging.info(f"⚡ Thinking level '{CHUNK_THINKING_LEVEL}' exhausted for {chunk_info}. Escalating to 🎯 '{THINKING_LEVEL}'...")

        full_result = await self._try_level_with_retries(
            thinking_level=THINKING_LEVEL,
            prompt=prompt,
            schema=schema,
            chunk_info=chunk_info,
            attempts=2,
        )

        if full_result:
            return full_result

        # All attempts failed
        logging.error(f"❌ All 4 attempts failed for {chunk_info}.")
        raise RuntimeError(f"All 4 extraction attempts failed for {chunk_info}")

    def _build_chunk_prompt(self, clean_chunk: List[Dict], chunk_idx: int, total_chunks: int, prompt_template: str) -> str:
        """Build the prompt for a chunk."""
        chunk_context = ""
        if total_chunks > 1:
            chunk_context = (
                f"\n\nNote: This is chunk {chunk_idx + 1} of {total_chunks} for this Zielobjekt. "
                f"Chunks have 10% overlap to maintain context continuity. Focus on extracting requirements "
                f"from these specific blocks, avoiding duplication of requirements found in overlapping sections."
            )

        return prompt_template.format(zielobjekt_blocks_json=json.dumps(clean_chunk, indent=2)) + chunk_context

    async def _try_level_with_retries(self, thinking_level: str, prompt: str, schema: Dict[str, Any],
                                      chunk_info: str, attempts: int) -> Optional[Dict[str, Any]]:
        """
        Try a thinking level with the given number of attempts.

        Returns:
            The result dict if successful, None if all attempts failed.
        """
        label = f"thinking '{thinking_level}'"
        for attempt in range(attempts):
            try:
                logging.info(f"Attempt {attempt + 1}/{attempts} at {label} for {chunk_info}")

                # Call AI without internal retries - we handle retries here
                result = await self._call_ai_model(
                    prompt=prompt,
                    schema=schema,
                    chunk_info=chunk_info,
                    thinking_level=thinking_level,
                )

                # Validate result
                if result and self._is_valid_result(result):
                    logging.info(f"✅ Success at {label} on attempt {attempt + 1}")
                    return result
                else:
                    logging.warning(f"⚠️  {label} returned invalid/empty result on attempt {attempt + 1}")

            except Exception as e:
                error_type = self._classify_error(e)
                if attempt < attempts - 1:  # Not the last attempt
                    logging.warning(f"⚠️  {label} attempt {attempt + 1}/{attempts} failed: {error_type}")
                else:  # Last attempt
                    logging.error(f"❌ {label} final attempt {attempt + 1}/{attempts} failed: {error_type}")

        return None

    def _classify_error(self, error: Exception) -> str:
        """Classify the error into a concise, readable format."""
        error_str = str(error)

        if "Unterminated string" in error_str or "JSONDecodeError" in error_str:
            return "JSON parsing error"
        elif "token" in error_str.lower() or "context length" in error_str.lower():
            return "Token limit exceeded"
        elif "timeout" in error_str.lower():
            return "Request timeout"
        elif "rate limit" in error_str.lower():
            return "Rate limit hit"
        elif "GoogleAPICallError" in error.__class__.__name__:
            return f"API error: {error_str[:100]}..."
        else:
            return f"Unexpected error: {error_str[:100]}..."

    async def _call_ai_model(self, prompt: str, schema: Dict[str, Any],
                           chunk_info: str, thinking_level: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Make a single AI call without retries.
        """
        # max_retries=1 gives one clean attempt; the 2+2 pattern above owns the retries.
        return await self.ai_client.generate_json_response(
            prompt=prompt,
            json_schema=schema,
            request_context_log=f"RefineGroup: {chunk_info}",
            max_retries=1,
            thinking_level=thinking_level,
        )

    def _is_valid_result(self, result: Dict[str, Any]) -> bool:
        """Check if the AI result is valid."""
        return result is not None and "anforderungen" in result and isinstance(result["anforderungen"], list)
