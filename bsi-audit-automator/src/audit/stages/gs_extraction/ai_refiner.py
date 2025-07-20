# bsi-audit-automator/src/audit/stages/gs_extraction/ai_refiner.py
import logging
import json
import asyncio
import os
from typing import Dict, Any, List, Tuple, Optional

from src.clients.ai_client import AiClient
from src.clients.gcs_client import GcsClient
from src.constants import GROUPED_BLOCKS_PATH, EXTRACTED_CHECK_DATA_PATH, CHUNK_PROCESSING_MODEL, GROUND_TRUTH_MODEL

from .cache_manager import CacheManager
from .chunk_processor import ChunkProcessor
from .data_processor import DataProcessor


class AiRefiner:
    """
    Orchestrates the AI refinement process for grouped blocks to extract structured security requirements.
    Delegates specific responsibilities to specialized components.
    """
    
    PROMPT_CONFIG_PATH = "assets/json/prompt_config.json"

    def __init__(self, ai_client: AiClient, gcs_client: GcsClient):
        self.ai_client = ai_client
        self.gcs_client = gcs_client
        self.cache_manager = CacheManager(gcs_client)
        self.chunk_processor = ChunkProcessor()
        self.data_processor = DataProcessor()
        self.prompt_config = self._load_asset_json(self.PROMPT_CONFIG_PATH)

    def _load_asset_json(self, path: str) -> dict:
        """Load JSON configuration from assets."""
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

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
        schema = self._load_asset_json(refine_config["schema_path"])
        
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
            results = await self._process_all_groups(valid_groups, zielobjekt_map, prompt_template, schema)
            
            # Assemble final results
            final_output = self.data_processor.assemble_final_results(results)
        
        # Save consolidated results
        await self.gcs_client.upload_from_string_async(
            json.dumps(final_output, indent=2, ensure_ascii=False), 
            EXTRACTED_CHECK_DATA_PATH
        )
        logging.info(f"Saved final refined check data with {len(final_output['anforderungen'])} requirements")

    async def _process_all_groups(self, valid_groups: Dict[str, List[Dict]], zielobjekt_map: Dict[str, str], 
                                 prompt_template: str, schema: Dict[str, Any]) -> List[Tuple[str, str, Optional[Dict[str, Any]]]]:
        """Process all valid groups concurrently."""
        tasks = [
            self._process_group_with_caching(kuerzel, blocks, zielobjekt_map, prompt_template, schema) 
            for kuerzel, blocks in valid_groups.items()
        ]
        return await asyncio.gather(*tasks)

    async def _process_group_with_caching(self, kuerzel: str, blocks: List[Dict], zielobjekt_map: Dict[str, str], 
                                         prompt_template: str, schema: Dict[str, Any]) -> Tuple[str, str, Optional[Dict[str, Any]]]:
        """Process a single group with caching support."""
        name = zielobjekt_map.get(kuerzel, "Unbekannt")
        
        # Check for cached result first
        cached_result = await self.cache_manager.get_cached_result(kuerzel)
        if cached_result is not None:
            return kuerzel, name, cached_result
        
        try:
            # Process with chunking if needed
            chunks = self.chunk_processor.chunk_blocks(blocks)
            
            if len(chunks) == 1:
                # Single chunk - process normally
                result = await self._process_blocks_chunk(kuerzel, chunks[0], 0, 1, prompt_template, schema)
            else:
                # Multiple chunks - process each and merge results
                logging.info(f"Processing {len(chunks)} chunks for Zielobjekt '{kuerzel}'")
                chunk_tasks = [
                    self._process_blocks_chunk(kuerzel, chunk, idx, len(chunks), prompt_template, schema) 
                    for idx, chunk in enumerate(chunks)
                ]
                chunk_results = await asyncio.gather(*chunk_tasks)
                
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

    async def _process_blocks_chunk(self, kuerzel: str, chunk: List[Dict], chunk_idx: int, total_chunks: int,
                                   prompt_template: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a single chunk of blocks for a kürzel.
        Simplified logic: 2 attempts with flash model, then 2 attempts with ground truth model, then fail.
        """
        # Preprocess blocks to prevent JSON issues
        clean_chunk = self.chunk_processor.preprocess_blocks_for_ai(chunk)
        
        # Calculate rough content size for logging
        total_chars = sum(len(str(block)) for block in clean_chunk)
        
        # Add chunk context to prompt if multiple chunks
        chunk_context = ""
        if total_chunks > 1:
            chunk_context = f"\n\nNote: This is chunk {chunk_idx + 1} of {total_chunks} for this Zielobjekt. Chunks have 10% overlap to maintain context continuity. Focus on extracting requirements from these specific blocks, avoiding duplication of requirements found in overlapping sections."
        
        prompt = prompt_template.format(zielobjekt_blocks_json=json.dumps(clean_chunk, indent=2)) + chunk_context
        
        # Try with flash model first (2 attempts)
        logging.info(f"Processing chunk {chunk_idx + 1}/{total_chunks} for '{kuerzel}': {len(clean_chunk)} blocks, ~{total_chars} chars")
        
        for attempt in range(2):
            try:
                logging.info(f"Attempt {attempt + 1}/2 with {CHUNK_PROCESSING_MODEL}")
                result = await self.ai_client.generate_json_response(
                    prompt=prompt,
                    json_schema=schema,
                    request_context_log=f"RefineGroup: {kuerzel} (chunk {chunk_idx + 1}/{total_chunks})",
                    model_override=CHUNK_PROCESSING_MODEL,
                    max_retries=1  # Single internal retry per attempt
                )
                
                if result and "anforderungen" in result:
                    logging.info(f"Successfully processed chunk with {CHUNK_PROCESSING_MODEL} on attempt {attempt + 1}")
                    return result
                    
            except Exception as e:
                logging.warning(f"{CHUNK_PROCESSING_MODEL} attempt {attempt + 1} failed for '{kuerzel}' chunk {chunk_idx + 1}: {e}")
                if attempt == 1:  # Last attempt with flash model
                    logging.info(f"Both {CHUNK_PROCESSING_MODEL} attempts failed. Switching to {GROUND_TRUTH_MODEL}...")
        
        # Try with ground truth model (2 attempts)
        for attempt in range(2):
            try:
                logging.info(f"Attempt {attempt + 1}/2 with {GROUND_TRUTH_MODEL}")
                result = await self.ai_client.generate_json_response(
                    prompt=prompt,
                    json_schema=schema,
                    request_context_log=f"RefineGroup: {kuerzel} (chunk {chunk_idx + 1}/{total_chunks})",
                    model_override=GROUND_TRUTH_MODEL,
                    max_retries=1  # Single internal retry per attempt
                )
                
                if result and "anforderungen" in result:
                    logging.info(f"Successfully processed chunk with {GROUND_TRUTH_MODEL} on attempt {attempt + 1}")
                    return result
                    
            except Exception as e:
                logging.error(f"{GROUND_TRUTH_MODEL} attempt {attempt + 1} failed for '{kuerzel}' chunk {chunk_idx + 1}: {e}")
        
        # All attempts failed
        logging.error(f"All 4 attempts (2x{CHUNK_PROCESSING_MODEL} + 2x{GROUND_TRUTH_MODEL}) failed for '{kuerzel}' chunk {chunk_idx + 1}. Returning empty result.")
        return {"anforderungen": []}