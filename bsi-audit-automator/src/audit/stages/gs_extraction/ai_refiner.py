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
                result = await self._process_single_chunk(kuerzel, chunks[0], 0, 1, prompt_template, schema)
            else:
                # Multiple chunks - process each and merge results
                logging.info(f"Processing {len(chunks)} chunks for Zielobjekt '{kuerzel}'")
                chunk_tasks = [
                    self._process_single_chunk(kuerzel, chunk, idx, len(chunks), prompt_template, schema) 
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

    async def _process_single_chunk(self, kuerzel: str, chunk: List[Dict], chunk_idx: int, total_chunks: int,
                                   prompt_template: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a single chunk with the 2+2 attempt pattern.
        """
        # Preprocess blocks
        clean_chunk = self.chunk_processor.preprocess_blocks_for_ai(chunk)
        
        # Build prompt
        prompt = self._build_chunk_prompt(clean_chunk, chunk_idx, total_chunks, prompt_template)
        
        # Log chunk info
        total_chars = sum(len(str(block)) for block in clean_chunk)
        chunk_info = f"chunk {chunk_idx + 1}/{total_chunks} for '{kuerzel}' ({len(clean_chunk)} blocks, ~{total_chars:,} chars)"
        logging.info(f"Processing {chunk_info}")
        
        # Try with flash model (2 attempts)
        flash_result = await self._try_model_with_retries(
            model_name=CHUNK_PROCESSING_MODEL,
            model_display_name="flash-lite",
            prompt=prompt,
            schema=schema,
            chunk_info=chunk_info,
            attempts=2
        )
        
        if flash_result:
            return flash_result
        
        # Flash failed, try with ground truth model (2 attempts)
        logging.info(f"⚡ Flash model exhausted for {chunk_info}. Switching to 🎯 ground truth model...")
        
        gt_result = await self._try_model_with_retries(
            model_name=GROUND_TRUTH_MODEL,
            model_display_name="ground-truth",
            prompt=prompt,
            schema=schema,
            chunk_info=chunk_info,
            attempts=2
        )
        
        if gt_result:
            return gt_result
        
        # All attempts failed
        logging.error(f"❌ All 4 attempts failed for {chunk_info}. Returning empty result.")
        return {"anforderungen": []}

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

    async def _try_model_with_retries(self, model_name: str, model_display_name: str, prompt: str, 
                                     schema: Dict[str, Any], chunk_info: str, attempts: int) -> Optional[Dict[str, Any]]:
        """
        Try a specific model with the given number of attempts.
        
        Returns:
            The result dict if successful, None if all attempts failed.
        """
        for attempt in range(attempts):
            try:
                model_icon = "⚡" if "flash" in model_display_name else "🎯"
                logging.info(f"{model_icon} Attempt {attempt + 1}/{attempts} with {model_display_name} for {chunk_info}")
                
                # Call AI without internal retries - we handle retries here
                result = await self._call_ai_model(
                    model_name=model_name,
                    prompt=prompt,
                    schema=schema,
                    chunk_info=chunk_info
                )
                
                # Validate result
                if result and self._is_valid_result(result):
                    logging.info(f"✅ Success with {model_display_name} on attempt {attempt + 1}")
                    return result
                else:
                    logging.warning(f"⚠️  {model_display_name} returned invalid/empty result on attempt {attempt + 1}")
                    
            except Exception as e:
                error_type = self._classify_error(e)
                if attempt < attempts - 1:  # Not the last attempt
                    logging.warning(f"⚠️  {model_display_name} attempt {attempt + 1}/{attempts} failed: {error_type}")
                else:  # Last attempt
                    logging.error(f"❌ {model_display_name} final attempt {attempt + 1}/{attempts} failed: {error_type}")
        
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

    async def _call_ai_model(self, model_name: str, prompt: str, schema: Dict[str, Any], 
                           chunk_info: str) -> Optional[Dict[str, Any]]:
        """
        Make a single AI call without retries.
        """
        try:
            # We'll use the standard generate_json_response but with max_retries=1
            # This gives us one clean attempt without the internal retry logic
            result = await self.ai_client.generate_json_response(
                prompt=prompt,
                json_schema=schema,
                request_context_log=f"RefineGroup: {chunk_info}",
                model_override=model_name,
                max_retries=1  # Disable internal retries
            )
            return result
        except Exception as e:
            # Re-raise to be handled by the retry logic
            raise

    def _is_valid_result(self, result: Dict[str, Any]) -> bool:
        """Check if the AI result is valid."""
        return result is not None and "anforderungen" in result and isinstance(result["anforderungen"], list)