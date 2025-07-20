# bsi-audit-automator/src/audit/stages/gs_extraction/ai_refiner.py
import logging
import json
import asyncio
import os
from typing import Dict, Any, List, Tuple, Optional

from src.clients.ai_client import AiClient
from src.clients.gcs_client import GcsClient
from src.constants import GROUPED_BLOCKS_PATH, EXTRACTED_CHECK_DATA_PATH, INDIVIDUAL_RESULTS_PREFIX, CHUNK_PROCESSING_MODEL, GROUND_TRUTH_MODEL
from jsonschema import validate, ValidationError

class AiRefiner:
    """
    Processes grouped blocks with AI to extract structured security requirements.
    Handles chunking, caching, error recovery, and result consolidation.
    """
    
    PROMPT_CONFIG_PATH = "assets/json/prompt_config.json"
    
    # Chunking configuration
    MAX_BLOCKS_PER_CHUNK = 200
    MIN_BLOCKS_PER_CHUNK = 50

    def __init__(self, ai_client: AiClient, gcs_client: GcsClient):
        self.ai_client = ai_client
        self.gcs_client = gcs_client
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
            final_output = self._assemble_final_results(results)
        
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
        cached_result = await self._get_cached_result(kuerzel)
        if cached_result is not None:
            return kuerzel, name, cached_result
        
        try:
            # Process with chunking if needed
            chunks = self._chunk_blocks(blocks, self.MAX_BLOCKS_PER_CHUNK)
            
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
                await self._save_result_to_cache(kuerzel, result)
            
            return kuerzel, name, result
            
        except Exception as e:
            logging.error(f"Complete processing failed for Zielobjekt '{kuerzel}': {e}")
            return kuerzel, name, None

    async def _get_cached_result(self, kuerzel: str) -> Optional[Dict[str, Any]]:
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

    async def _save_result_to_cache(self, kuerzel: str, result_data: Dict[str, Any]):
        """Save individual result to cache."""
        cache_path = f"{INDIVIDUAL_RESULTS_PREFIX}{kuerzel}_result.json"
        try:
            await self.gcs_client.upload_from_string_async(
                json.dumps(result_data, indent=2, ensure_ascii=False), cache_path
            )
            logging.debug(f"Cached result for Zielobjekt '{kuerzel}' to {cache_path}")
        except Exception as e:
            logging.error(f"Failed to cache result for '{kuerzel}': {e}")

    def _chunk_blocks(self, blocks: List[Dict], max_blocks: int) -> List[List[Dict]]:
        """Split blocks into chunks of manageable size with 8% overlap."""
        if len(blocks) <= max_blocks:
            return [blocks]

        # Calculate overlap size (10% of max_blocks, minimum 10 blocks, maximum 20 blocks)
        overlap_size = max(10, min(20, int(max_blocks * 0.10)))
        
        chunks = []
        i = 0
        while i < len(blocks):
            # Calculate chunk boundaries
            start_idx = max(0, i - (overlap_size if i > 0 else 0))
            end_idx = min(len(blocks), i + max_blocks)
            
            # Extract chunk with overlap
            chunk = blocks[start_idx:end_idx]
            chunks.append(chunk)
            
            # Move to next chunk position (accounting for overlap)
            i += max_blocks - overlap_size
            
            # Break if we've covered all blocks
            if end_idx >= len(blocks):
                break
        
        logging.info(f"Split {len(blocks)} blocks into {len(chunks)} chunks with {overlap_size}-block overlap ({overlap_size/max_blocks*100:.1f}%)")
        return chunks

    def _preprocess_blocks_for_ai(self, blocks: List[Dict]) -> List[Dict]:
        """Preprocess blocks to avoid JSON generation issues."""
        processed_blocks = []
        
        for block in blocks:
            # Create a clean copy of the block
            clean_block = block.copy()
            
            # Clean text content to prevent JSON issues
            if 'textBlock' in clean_block and 'text' in clean_block['textBlock']:
                text = clean_block['textBlock']['text']
                # Remove or escape problematic characters
                text = text.replace('\r\n', ' ').replace('\n', ' ').replace('\r', ' ')
                text = text.replace('"', '\\"').replace('\t', ' ')
                # Limit extremely long text blocks that might cause issues
                if len(text) > 2000:
                    text = text[:1800] + "... [truncated]"
                clean_block['textBlock']['text'] = text
            
            processed_blocks.append(clean_block)
        
        return processed_blocks

    async def _process_blocks_chunk(self, kuerzel: str, chunk: List[Dict], chunk_idx: int, total_chunks: int,
                                   prompt_template: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single chunk of blocks for a kürzel."""
        # Preprocess blocks to prevent JSON issues
        clean_chunk = self._preprocess_blocks_for_ai(chunk)
        
        # Calculate rough content size for logging
        total_chars = sum(len(str(block)) for block in clean_chunk)
        
        # Add chunk context to prompt if multiple chunks
        chunk_context = ""
        if total_chunks > 1:
            chunk_context = f"\n\nNote: This is chunk {chunk_idx + 1} of {total_chunks} for this Zielobjekt. Chunks have 8% overlap to maintain context continuity. Focus on extracting requirements from these specific blocks, avoiding duplication of requirements found in overlapping sections."
        
        prompt = prompt_template.format(zielobjekt_blocks_json=json.dumps(clean_chunk, indent=2)) + chunk_context
        
        # Try flash-lite first, then fallback to ground truth model on JSON errors
        models_to_try = [
            (CHUNK_PROCESSING_MODEL, "flash-lite"),
            (GROUND_TRUTH_MODEL, "ground-truth fallback")
        ]
        
        for model_name, model_desc in models_to_try:
            logging.info(f"Processing chunk {chunk_idx + 1}/{total_chunks} for '{kuerzel}': {len(clean_chunk)} blocks, ~{total_chars} chars (using {model_desc})")
            
            try:
                result = await self.ai_client.generate_json_response(
                    prompt=prompt,
                    json_schema=schema,
                    request_context_log=f"RefineGroup: {kuerzel} (chunk {chunk_idx + 1}/{total_chunks})",
                    model_override=model_name
                )
                
                # Validate the result against the schema
                if result:
                    try:
                        validate(instance=result, schema=schema)
                        if "anforderungen" in result:
                            if model_name != CHUNK_PROCESSING_MODEL:
                                logging.info(f"Successfully processed chunk with {model_desc} after flash-lite failed")
                            return result
                        else:
                            logging.warning(f"{model_desc} returned valid JSON but missing 'anforderungen' key for {kuerzel} chunk {chunk_idx + 1}")
                    except ValidationError as ve:
                        logging.warning(f"{model_desc} returned JSON that failed schema validation for {kuerzel} chunk {chunk_idx + 1}: {ve.message}")
                        # Continue to next model or splitting logic
                else:
                    logging.warning(f"{model_desc} returned empty result for {kuerzel} chunk {chunk_idx + 1}")
                    
            except Exception as e:
                error_msg = str(e).lower()
                is_json_error = "unterminated string" in error_msg or "json" in error_msg
                
                if is_json_error and model_name == CHUNK_PROCESSING_MODEL:
                    logging.warning(f"Flash-lite failed with JSON error for '{kuerzel}' chunk {chunk_idx + 1}: {e}. Trying ground truth model...")
                    continue  # Try next model
                elif model_name == GROUND_TRUTH_MODEL:
                    logging.error(f"Ground truth model also failed for '{kuerzel}' chunk {chunk_idx + 1}: {e}")
                    break  # Both models failed, try chunk splitting
                else:
                    logging.error(f"{model_desc} failed for '{kuerzel}' chunk {chunk_idx + 1}: {e}")
                    break
        
        # If both models failed, try splitting the chunk as last resort
        if len(clean_chunk) > self.MIN_BLOCKS_PER_CHUNK:
            logging.info(f"Both models failed, attempting to split chunk {chunk_idx + 1} for '{kuerzel}'")
            try:
                mid_point = len(clean_chunk) // 2
                part1 = clean_chunk[:mid_point]
                part2 = clean_chunk[mid_point:]
                
                # Process both parts recursively
                result1 = await self._process_blocks_chunk(kuerzel, part1, f"{chunk_idx}a", f"{total_chunks}+", prompt_template, schema)
                result2 = await self._process_blocks_chunk(kuerzel, part2, f"{chunk_idx}b", f"{total_chunks}+", prompt_template, schema)
                
                # Combine results
                combined_anforderungen = []
                if result1 and "anforderungen" in result1:
                    combined_anforderungen.extend(result1["anforderungen"])
                if result2 and "anforderungen" in result2:
                    combined_anforderungen.extend(result2["anforderungen"])
                
                return {"anforderungen": combined_anforderungen}
                
            except Exception as split_error:
                logging.error(f"Chunk splitting also failed for '{kuerzel}': {split_error}")
        
            # If this chunk is too large, try splitting it further
            if len(clean_chunk) > self.MIN_BLOCKS_PER_CHUNK and "token" in str(e).lower():
                logging.info(f"Attempting to split large chunk {chunk_idx + 1} for '{kuerzel}'")
                try:
                    # Split the chunk in half and process each part
                    mid_point = len(clean_chunk) // 2
                    part1 = clean_chunk[:mid_point]
                    part2 = clean_chunk[mid_point:]
                    
                    # Process both parts recursively
                    result1 = await self._process_blocks_chunk(kuerzel, part1, f"{chunk_idx}a", f"{total_chunks}+", prompt_template, schema)
                    result2 = await self._process_blocks_chunk(kuerzel, part2, f"{chunk_idx}b", f"{total_chunks}+", prompt_template, schema)
                    
                    # Combine results
                    combined_anforderungen = []
                    if result1 and "anforderungen" in result1:
                        combined_anforderungen.extend(result1["anforderungen"])
                    if result2 and "anforderungen" in result2:
                        combined_anforderungen.extend(result2["anforderungen"])
                    
                    return {"anforderungen": combined_anforderungen}
                    
                except Exception as split_error:
                    logging.error(f"Chunk splitting also failed for '{kuerzel}': {split_error}")
            
            return {"anforderungen": []}

    def _deduplicate_requirements(self, all_anforderungen: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Deduplicates requirements based on (id, zielobjekt_kuerzel) composite key.
        For duplicates, selects the version with the highest quality score.
        
        Args:
            all_anforderungen: List of all extracted requirements (may contain duplicates)
            
        Returns:
            List of deduplicated requirements with highest quality versions retained
        """
        from collections import defaultdict
        
        # Group requirements by composite key
        requirement_groups = defaultdict(list)
        for req in all_anforderungen:
            req_id = req.get('id')
            zielobjekt_kuerzel = req.get('zielobjekt_kuerzel')
            
            # Skip requirements missing critical identifiers
            if not req_id or not zielobjekt_kuerzel:
                logging.warning(f"Skipping requirement with missing ID or Zielobjekt: {req}")
                continue
                
            composite_key = (req_id, zielobjekt_kuerzel)
            requirement_groups[composite_key].append(req)
        
        deduplicated = []
        duplicate_count = 0
        
        for composite_key, req_list in requirement_groups.items():
            if len(req_list) == 1:
                # No duplicates for this key
                deduplicated.append(req_list[0])
            else:
                # Multiple versions found - select best quality
                duplicate_count += len(req_list) - 1
                best_req = self._select_best_requirement_version(req_list)
                deduplicated.append(best_req)
                
                req_id, zielobjekt_kuerzel = composite_key
                logging.info(f"Resolved {len(req_list)} duplicates for requirement '{req_id}' on '{zielobjekt_kuerzel}' - kept version with quality score {best_req.get('_quality_score', 'N/A')}")
        
        logging.info(f"Deduplication complete: {duplicate_count} duplicates removed, {len(deduplicated)} unique requirements retained")
        return deduplicated

    def _calculate_quality_score(self, requirement: Dict[str, Any]) -> float:
        """
        Calculates a quality score for a requirement based on completeness and content quality.
        Higher scores indicate better extractions.
        
        Args:
            requirement: The requirement dictionary to score
            
        Returns:
            Quality score (0.0 to 1.0)
        """
        score = 0.0
        
        # Check for presence and quality of key fields
        umsetzungserlaeuterung = requirement.get('umsetzungserlaeuterung', '').strip()
        if umsetzungserlaeuterung and len(umsetzungserlaeuterung) > 10:
            if 'keine spezifische angabe' not in umsetzungserlaeuterung.lower():
                score += 0.4  # Good explanation content
            else:
                score += 0.1  # Generic/fallback explanation
        
        # Valid status increases score
        status = requirement.get('umsetzungsstatus', '').strip().lower()
        if status in ['ja', 'nein', 'teilweise', 'entbehrlich']:
            score += 0.3
        
        # Recent check date increases score
        date_str = requirement.get('datumLetztePruefung', '1970-01-01')
        if date_str != '1970-01-01':
            try:
                from datetime import datetime
                if '.' in date_str:
                    check_date = datetime.strptime(date_str, "%d.%m.%Y")
                else:
                    check_date = datetime.strptime(date_str, "%Y-%m-%d")
                
                # More recent dates get higher scores (within last 2 years = full points)
                days_old = (datetime.now() - check_date).days
                if days_old <= 730:  # 2 years
                    score += 0.2
                elif days_old <= 1460:  # 4 years
                    score += 0.1
            except ValueError:
                pass  # Invalid date format
        
        # Title presence
        if requirement.get('titel', '').strip():
            score += 0.1
        
        return min(score, 1.0)  # Cap at 1.0

    def _select_best_requirement_version(self, requirement_versions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Selects the best version from a list of duplicate requirements.
        
        Args:
            requirement_versions: List of requirement dictionaries (duplicates)
            
        Returns:
            The requirement version with the highest quality score
        """
        if len(requirement_versions) == 1:
            return requirement_versions[0]
        
        # Calculate quality scores for all versions
        scored_versions = []
        for req in requirement_versions:
            quality_score = self._calculate_quality_score(req)
            req['_quality_score'] = quality_score  # Store for logging
            scored_versions.append((quality_score, req))
        
        # Sort by quality score (highest first) and return best version
        scored_versions.sort(key=lambda x: x[0], reverse=True)
        best_req = scored_versions[0][1]
        
        # Clean up temporary quality score field
        for _, req in scored_versions:
            req.pop('_quality_score', None)
        
        return best_req

    def _assemble_final_results(self, results: List[Tuple[str, str, Optional[Dict[str, Any]]]]) -> Dict[str, List[Dict]]:
        """
        Assemble final results from all processed groups with robust deduplication.
        """
        all_anforderungen = []
        successful_count = 0
        failed_count = 0
        
        for kuerzel, name, result_data in results:
            if result_data and "anforderungen" in result_data:
                for anforderung in result_data["anforderungen"]:
                    anforderung['zielobjekt_kuerzel'] = kuerzel
                    anforderung['zielobjekt_name'] = name
                    all_anforderungen.append(anforderung)
                successful_count += 1
            else:
                failed_count += 1
                logging.warning(f"No valid requirements extracted for Zielobjekt '{kuerzel}'")

        # Apply deduplication
        logging.info(f"Pre-deduplication: {len(all_anforderungen)} total requirements")
        deduplicated_anforderungen = self._deduplicate_requirements(all_anforderungen)
        logging.info(f"Post-deduplication: {len(deduplicated_anforderungen)} unique requirements")

        logging.info(f"AI refinement completed: {successful_count} successful, {failed_count} failed")
        return {"anforderungen": deduplicated_anforderungen}