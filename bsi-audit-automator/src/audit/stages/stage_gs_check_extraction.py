# bsi-audit-automator/src/audit/stages/stage_gs_check_extraction.py
import logging
import json
import asyncio
import fitz  # PyMuPDF
import re
import os

from typing import Optional, Dict, Any, List, Tuple
from google.cloud.exceptions import NotFound

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.document_ai_client import DocumentAiClient
from src.clients.ai_client import AiClient
from src.clients.rag_client import RagClient


class GrundschutzCheckExtractionRunner:
    """
    A dedicated, multi-step pre-processing stage that creates an authoritative,
    structured representation of the customer's security requirements from the
    Grundschutz-Check document. It uses a "Ground-Truth-Driven Semantic Chunking"
    strategy for high accuracy.
    """
    STAGE_NAME = "Grundschutz-Check-Extraction"
    PROMPT_CONFIG_PATH = "assets/json/prompt_config.json"
    
    # Paths for intermediate and final artifacts
    GROUND_TRUTH_MAP_PATH = "output/results/intermediate/system_structure_map.json"
    TEMP_PDF_CHUNK_PREFIX = "output/temp_pdf_chunks/"
    DOC_AI_CHUNK_RESULTS_PREFIX = "output/doc_ai_results/"
    FINAL_MERGED_LAYOUT_PATH = "output/results/intermediate/doc_ai_layout_parser_merged.json"
    GROUPED_BLOCKS_PATH = "output/results/intermediate/zielobjekt_grouped_blocks.json"
    FINAL_CHECK_RESULTS_PATH = "output/results/intermediate/extracted_grundschutz_check_merged.json"
    
    PAGE_CHUNK_SIZE = 100

    def __init__(self, config: AppConfig, gcs_client: GcsClient, doc_ai_client: DocumentAiClient, ai_client: AiClient, rag_client: RagClient):
        self.config = config
        self.gcs_client = gcs_client
        self.doc_ai_client = doc_ai_client
        self.ai_client = ai_client
        self.rag_client = rag_client
        self.prompt_config = self._load_asset_json(self.PROMPT_CONFIG_PATH)
        self.block_counter = 1
        logging.info(f"Initialized runner for stage: {self.STAGE_NAME}")

    def _load_asset_json(self, path: str) -> dict:
        with open(path, 'r', encoding='utf-8') as f: return json.load(f)

    def _structure_mappings(self, flat_mappings: List[Dict[str, str]]) -> Dict[str, List[str]]:
        """Converts the flat mapping list from AI into a dict of Baustein ID to a list of Zielobjekt Kürzel."""
        structured = {}
        for mapping in flat_mappings:
            baustein_id = mapping.get("baustein_id")
            kuerzel = mapping.get("zielobjekt_kuerzel")
            if baustein_id and kuerzel:
                if baustein_id not in structured:
                    structured[baustein_id] = []
                if kuerzel not in structured[baustein_id]:
                    structured[baustein_id].append(kuerzel)
        return structured

    async def _create_system_structure_map(self, force_overwrite: bool) -> Dict[str, Any]:
        """
        [Step 1] Creates the authoritative system structure map by extracting Zielobjekte (from A.1)
        and Baustein-to-Zielobjekt mappings (from A.3). This map is the "Ground Truth".
        """
        if not force_overwrite and self.gcs_client.blob_exists(self.GROUND_TRUTH_MAP_PATH):
            logging.info(f"System structure map already exists. Loading from '{self.GROUND_TRUTH_MAP_PATH}'.")
            try:
                system_map = await self.gcs_client.read_json_async(self.GROUND_TRUTH_MAP_PATH)
                if not system_map.get("zielobjekte"):
                    logging.error("Loaded system structure map has empty 'zielobjekte'. Exiting.")
                    raise ValueError("No Zielobjekte found in loaded map. Cannot proceed.")
                return system_map
            except json.JSONDecodeError as e:
                logging.error(f"Invalid JSON in system structure map: {e}")
                raise
        logging.info("Generating new system structure map...")
        gt_config = self.prompt_config["stages"]["Chapter-3-Ground-Truth"]
        
        try:
            # Extract Zielobjekte from Strukturanalyse (A.1)
            z_task_config = gt_config["extract_zielobjekte"]
            z_uris = self.rag_client.get_gcs_uris_for_categories(["Strukturanalyse"])
            zielobjekte_result = await self.ai_client.generate_json_response(
                z_task_config["prompt"], self._load_asset_json(z_task_config["schema_path"]), z_uris, "GT: extract_zielobjekte"
            )

            # Extract Mappings from Modellierung (A.3)
            m_task_config = gt_config["extract_baustein_mappings"]
            m_uris = self.rag_client.get_gcs_uris_for_categories(["Modellierung"])
            mappings_result = await self.ai_client.generate_json_response(
                m_task_config["prompt"], self._load_asset_json(m_task_config["schema_path"]), m_uris, "GT: extract_baustein_mappings"
            )

            system_map = {
                "zielobjekte": zielobjekte_result.get("zielobjekte", []),
                "baustein_to_zielobjekt_mapping": self._structure_mappings(mappings_result.get("mappings", []))
            }
            
            await self.gcs_client.upload_from_string_async(
                json.dumps(system_map, indent=2, ensure_ascii=False), self.GROUND_TRUTH_MAP_PATH
            )
            logging.info(f"Successfully created and saved system structure map to {self.GROUND_TRUTH_MAP_PATH}.")
            return system_map
        except Exception as e:
            logging.error(f"Failed to create system structure map: {e}", exc_info=True)
            raise

    async def _execute_layout_parser_workflow(self, force_overwrite: bool):
        """[Step 2] Runs the full Document AI Layout Parser workflow if the output doesn't exist."""
        if not force_overwrite and self.gcs_client.blob_exists(self.FINAL_MERGED_LAYOUT_PATH):
            logging.info(f"Merged layout file already exists. Skipping Layout Parser workflow.")
            return

        check_uris = self.rag_client.get_gcs_uris_for_categories(["Grundschutz-Check", "test.pdf"])
        if not check_uris: raise FileNotFoundError("Could not find 'Grundschutz-Check' or 'test.pdf' document.")
        
        source_blob_name = check_uris[0].replace(f"gs://{self.config.bucket_name}/", "")
        pdf_bytes = self.gcs_client.download_blob_as_bytes(self.gcs_client.bucket.blob(source_blob_name))
        
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        upload_tasks = []
        for i in range(0, pdf_doc.page_count, self.PAGE_CHUNK_SIZE):
            chunk_doc = fitz.open()
            chunk_doc.insert_pdf(pdf_doc, from_page=i, to_page=min(i + self.PAGE_CHUNK_SIZE, pdf_doc.page_count) - 1)
            destination_blob_name = f"{self.TEMP_PDF_CHUNK_PREFIX}chunk_{i // self.PAGE_CHUNK_SIZE}.pdf"
            upload_tasks.append(self.gcs_client.upload_from_bytes_async(chunk_doc.tobytes(), destination_blob_name))
            chunk_doc.close()
        
        await asyncio.gather(*upload_tasks)
        logging.info(f"Split PDF into {len(upload_tasks)} chunks and uploaded to GCS.")

        processing_tasks = [
            self.doc_ai_client.process_document_chunk_async(
                f"gs://{self.config.bucket_name}/{self.TEMP_PDF_CHUNK_PREFIX}chunk_{i}.pdf", self.DOC_AI_CHUNK_RESULTS_PREFIX
            ) for i in range(len(upload_tasks))
        ]
        await asyncio.gather(*processing_tasks)

        # Merge results - this relies on the now-fixed client logic
        merged_blocks = []
        merged_text = ""
        for i in range(len(upload_tasks)):
            chunk_json_path = f"{self.DOC_AI_CHUNK_RESULTS_PREFIX}chunk_{i}.json"
            chunk_data = await self.gcs_client.read_json_async(chunk_json_path)
            merged_text += chunk_data.get("text", "")
            merged_blocks.extend(chunk_data.get("documentLayout", {}).get("blocks", []))

        # Re-index block IDs globally
        self.block_counter = 1
        self._reindex_and_prune_blocks(merged_blocks)
        
        final_layout_json = {"text": merged_text, "documentLayout": {"blocks": merged_blocks}}
        await self.gcs_client.upload_from_string_async(
            json.dumps(final_layout_json, indent=2, ensure_ascii=False),
            self.FINAL_MERGED_LAYOUT_PATH
        )
        logging.info(f"Successfully merged, re-indexed, and saved final layout to {self.FINAL_MERGED_LAYOUT_PATH}")

    def _reindex_and_prune_blocks(self, blocks: List[Dict[str, Any]]):
        """Recursively re-indexes blockId globally and removes pageSpan."""
        for block in blocks:
            block.pop("pageSpan", None)
            block["blockId"] = str(self.block_counter)
            self.block_counter += 1
            if "blocks" in block.get("textBlock", {}):
                self._reindex_and_prune_blocks(block["textBlock"]["blocks"])
            for row_type in ["headerRows", "bodyRows"]:
                for row in block.get("tableBlock", {}).get(row_type, []):
                    for cell in row.get("cells", []):
                        if "blocks" in cell: self._reindex_and_prune_blocks(cell["blocks"])
    
    async def _group_layout_blocks_by_zielobjekt(self, system_map: Dict[str, Any], force_overwrite: bool):
        """
        [Step 3] Deterministically groups layout blocks by the Zielobjekt they belong to
        using a robust two-phase "find markers, then group" algorithm.
        """
        if not force_overwrite and self.gcs_client.blob_exists(self.GROUPED_BLOCKS_PATH):
            logging.info(f"Grouped layout blocks file already exists. Skipping grouping.")
            return

        logging.info("Grouping layout blocks by Zielobjekt context using marker-based algorithm...")
        layout_data = await self.gcs_client.read_json_async(self.FINAL_MERGED_LAYOUT_PATH)
        all_blocks = layout_data.get("documentLayout", {}).get("blocks", [])

        # ADD THESE MISSING IMPORTS AND VARIABLES:
        import sys
        from collections import defaultdict
        
        # Initialize grouped_blocks
        grouped_blocks = defaultdict(list)
        
        # Flatten all blocks for consistent processing
        def flatten_all_blocks(blocks):
            """Flattens all blocks into a single list with their hierarchical structure removed."""
            flattened = []
            
            def flatten_recursive(block_list):
                for block in block_list:
                    # Add the current block to flattened list
                    flattened.append(block)
                    
                    # Process nested textBlock.blocks
                    if 'textBlock' in block and 'blocks' in block['textBlock']:
                        flatten_recursive(block['textBlock']['blocks'])
                    
                    # Process table blocks
                    if 'tableBlock' in block:
                        for row_type in ['headerRows', 'bodyRows']:
                            for row in block['tableBlock'].get(row_type, []):
                                for cell in row.get('cells', []):
                                    if 'blocks' in cell:
                                        flatten_recursive(cell['blocks'])
            
            flatten_recursive(blocks)
            return flattened
        
        all_flattened_blocks = flatten_all_blocks(all_blocks)
        block_id_to_block_map = {int(b['blockId']): b for b in all_flattened_blocks}

        # --- Phase 1: Find Markers ---
        zielobjekte = system_map.get("zielobjekte", [])
        kuerzel_list = [item['kuerzel'] for item in zielobjekte]
        remaining_kuerzel = kuerzel_list.copy()

        markers = []
                
        # Check each individual block's direct text
        for block in all_flattened_blocks:
            # Get the direct text from this specific block only
            direct_text = ""
            if 'textBlock' in block and 'text' in block['textBlock']:
                direct_text = block['textBlock']['text'].strip()
            
            if direct_text:
                for kuerzel in remaining_kuerzel.copy():
                    if direct_text == kuerzel:
                        block_id = int(block.get('blockId', 0))
                        markers.append({'kuerzel': kuerzel, 'block_id': block_id})
                        remaining_kuerzel.remove(kuerzel)
                        break
        
        print(f"DEBUG: UNFOUND kuerzel ({len(remaining_kuerzel)}): {remaining_kuerzel}")

        if not markers:
            # If no markers are found, all blocks are ungrouped
            logging.warning("No Zielobjekt markers found in document. All blocks will be marked as ungrouped.")
            sys.exit()
        else:
            # --- Phase 2: Sort Markers ---
            markers.sort(key=lambda m: m['block_id'])
            logging.info(f"Found and sorted {len(markers)} Zielobjekt markers.")

            # --- Phase 3: Group Blocks by Range ---
            sorted_block_ids = sorted(block_id_to_block_map.keys())
            
            first_marker_id = markers[0]['block_id']
            ungrouped_ids = [bid for bid in sorted_block_ids if bid < first_marker_id]
            for bid in ungrouped_ids:
                grouped_blocks["_UNGROUPED_"].append(block_id_to_block_map[bid])
            
            for i, marker in enumerate(markers):
                start_id = marker['block_id']
                end_id = markers[i+1]['block_id'] if i + 1 < len(markers) else max(sorted_block_ids) + 1
                
                kuerzel = marker['kuerzel']
                group_ids = [bid for bid in sorted_block_ids if start_id <= bid < end_id]
                for bid in group_ids:
                    grouped_blocks[kuerzel].append(block_id_to_block_map[bid])
                
                logging.info(f"Assigned {len(group_ids)} blocks to '{kuerzel}' (IDs {start_id}-{end_id-1}).")

        await self.gcs_client.upload_from_string_async(
            json.dumps({"zielobjekt_grouped_blocks": dict(grouped_blocks)}, indent=2, ensure_ascii=False),
            self.GROUPED_BLOCKS_PATH
        )
        logging.info(f"Saved grouped layout blocks to {self.GROUPED_BLOCKS_PATH}")

    async def _refine_grouped_blocks_with_ai(self, system_map: Dict[str, Any], force_overwrite: bool):
        """[Step 4] Processes each group of blocks with Gemini to extract structured requirements.
        
        Features:
        - Per-kürzel idempotency with GCS caching
        - Automatic chunking for large block groups
        - Robust error handling and recovery
        """
        if not force_overwrite and self.gcs_client.blob_exists(self.FINAL_CHECK_RESULTS_PATH):
            logging.info(f"Final extracted check results file exists. Skipping AI refinement.")
            return

        logging.info("Refining grouped blocks with AI to extract structured requirements...")
        grouped_blocks_data = await self.gcs_client.read_json_async(self.GROUPED_BLOCKS_PATH)
        groups = grouped_blocks_data.get("zielobjekt_grouped_blocks", {})
        
        refine_config = self.prompt_config["stages"]["Chapter-3"]["refine_layout_parser_group"]
        prompt_template = refine_config["prompt"]
        schema = self._load_asset_json(refine_config["schema_path"])
        
        # Configuration for chunking
        MAX_BLOCKS_PER_CHUNK = 300  # Reduced to prevent token limit issues
        MIN_BLOCKS_PER_CHUNK = 50   # Minimum chunk size before splitting further
        INDIVIDUAL_RESULTS_PREFIX = "output/results/intermediate/gs_extraction_individual_results/"
        
        zielobjekt_map = {z['kuerzel']: z['name'] for z in system_map.get("zielobjekte", [])}

        async def get_cached_result(kuerzel: str) -> Optional[Dict[str, Any]]:
            """Check if we have a cached result for this kürzel."""
            cache_path = f"output/{INDIVIDUAL_RESULTS_PREFIX}{kuerzel}_result.json"
            if not force_overwrite and self.gcs_client.blob_exists(cache_path):
                try:
                    cached_result = await self.gcs_client.read_json_async(cache_path)
                    logging.info(f"Using cached result for Zielobjekt '{kuerzel}'")
                    return cached_result
                except Exception as e:
                    logging.warning(f"Failed to read cached result for '{kuerzel}': {e}")
            return None

        async def save_result_to_cache(kuerzel: str, result_data: Dict[str, Any]):
            """Save individual result to cache."""
            cache_path = f"output/{INDIVIDUAL_RESULTS_PREFIX}{kuerzel}_result.json"
            try:
                await self.gcs_client.upload_from_string_async(
                    json.dumps(result_data, indent=2, ensure_ascii=False), cache_path
                )
                logging.debug(f"Cached result for Zielobjekt '{kuerzel}' to {cache_path}")
            except Exception as e:
                logging.error(f"Failed to cache result for '{kuerzel}': {e}")

        def chunk_blocks(blocks: List[Dict], max_blocks: int) -> List[List[Dict]]:
            """Split blocks into chunks of manageable size with 2% overlap."""
            if len(blocks) <= max_blocks:
                return [blocks]
            
            # Calculate overlap size (2% of max_blocks, minimum 1 block)
            overlap_size = max(1, int(max_blocks * 0.02))
            
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
            
            logging.info(f"Split {len(blocks)} blocks into {len(chunks)} chunks with {overlap_size}-block overlap")
            return chunks

        async def process_blocks_chunk(kuerzel: str, chunk: List[Dict], chunk_idx: int, total_chunks: int) -> Dict[str, Any]:
            """Process a single chunk of blocks for a kürzel."""
            name = zielobjekt_map.get(kuerzel, "Unbekannt")

            # Preprocess blocks to prevent JSON issues
            clean_chunk = preprocess_blocks_for_ai(chunk)

            # Calculate rough content size for logging
            total_chars = sum(len(str(block)) for block in clean_chunk)
            logging.info(f"Processing chunk {chunk_idx + 1}/{total_chunks} for '{kuerzel}': {len(clean_chunk)} blocks, ~{total_chars} chars")

            # Add chunk context to prompt if multiple chunks
            chunk_context = ""
            if total_chunks > 1:
                chunk_context = f"\n\nNote: This is chunk {chunk_idx + 1} of {total_chunks} for this Zielobjekt. Chunks have 2% overlap to maintain context continuity. Focus on extracting requirements from these specific blocks, avoiding duplication of requirements found in overlapping sections."

            prompt = prompt_template.format(zielobjekt_blocks_json=json.dumps(clean_chunk, indent=2)) + chunk_context

            try:
                # Try with normal generation first
                result = await self.ai_client.generate_json_response(
                    prompt, schema, request_context_log=f"RefineGroup: {kuerzel} (chunk {chunk_idx + 1}/{total_chunks})"
                )

                # Validate the result
                if result and "anforderungen" in result:
                    return result
                else:
                    logging.warning(f"Invalid result structure for {kuerzel} chunk {chunk_idx + 1}")
                    return {"anforderungen": []}

            except Exception as e:
                logging.error(f"AI refinement failed for Zielobjekt '{kuerzel}' chunk {chunk_idx + 1}: {e}")

                # If this chunk is too large, try splitting it further
                if len(clean_chunk) > MIN_BLOCKS_PER_CHUNK and "token" in str(e).lower():
                    logging.info(f"Attempting to split large chunk {chunk_idx + 1} for '{kuerzel}'")
                    try:
                        # Split the chunk in half and process each part
                        mid_point = len(clean_chunk) // 2
                        part1 = clean_chunk[:mid_point]
                        part2 = clean_chunk[mid_point:]

                        # Process both parts
                        result1 = await process_blocks_chunk(kuerzel, part1, f"{chunk_idx}a", f"{total_chunks}+")
                        result2 = await process_blocks_chunk(kuerzel, part2, f"{chunk_idx}b", f"{total_chunks}+")

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

        async def generate_and_tag(kuerzel: str, blocks: List[Dict]) -> Tuple[str, str, Optional[Dict[str, Any]]]:
            """Process all blocks for a kürzel, with caching and chunking support."""
            name = zielobjekt_map.get(kuerzel, "Unbekannt")
            
            # Check for cached result first
            cached_result = await get_cached_result(kuerzel)
            if cached_result is not None:
                return kuerzel, name, cached_result
            
            try:
                # Determine if chunking is needed
                chunks = chunk_blocks(blocks, MAX_BLOCKS_PER_CHUNK)
                
                if len(chunks) == 1:
                    # Single chunk - process normally
                    result = await process_blocks_chunk(kuerzel, chunks[0], 0, 1)
                else:
                    # Multiple chunks - process each and merge results
                    logging.info(f"Processing {len(chunks)} chunks for Zielobjekt '{kuerzel}'")
                    chunk_tasks = [
                        process_blocks_chunk(kuerzel, chunk, idx, len(chunks)) 
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
                    await save_result_to_cache(kuerzel, result)
                
                return kuerzel, name, result
                
            except Exception as e:
                logging.error(f"Complete processing failed for Zielobjekt '{kuerzel}': {e}")
                return kuerzel, name, None

        # Filter and process groups
        valid_groups = {k: v for k, v in groups.items() if k != "_UNGROUPED_" and v}
        
        if not valid_groups:
            logging.warning("No valid Zielobjekt groups found for processing")
            final_output = {"anforderungen": []}
        else:
            # Limit in test mode
            if os.getenv("TEST", "false").lower() == "true":
                limited_groups = dict(list(valid_groups.items())[:3])
                logging.info(f"Test mode: Processing only {len(limited_groups)} of {len(valid_groups)} groups")
                valid_groups = limited_groups
            
            logging.info(f"Processing {len(valid_groups)} Zielobjekt groups...")
            
            # Process all groups
            tasks = [generate_and_tag(kuerzel, blocks) for kuerzel, blocks in valid_groups.items()]
            results = await asyncio.gather(*tasks)

            # Assemble final results
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

            final_output = {"anforderungen": all_anforderungen}
            
            logging.info(f"AI refinement completed: {successful_count} successful, {failed_count} failed")
        
        # Save final consolidated results
        await self.gcs_client.upload_from_string_async(
            json.dumps(final_output, indent=2, ensure_ascii=False), self.FINAL_CHECK_RESULTS_PATH
        )
        logging.info(f"Saved final refined check data with {len(final_output['anforderungen'])} requirements to {self.FINAL_CHECK_RESULTS_PATH}")

    async def run(self, force_overwrite: bool = False) -> Dict[str, Any]:
        """Main execution method for the full extraction and refinement pipeline."""
        logging.info(f"Executing stage: {self.STAGE_NAME}")
        
        # This stage produces intermediate files used by other stages, but no direct reportable output.
        # Its "result" is the successful creation of its artifacts on GCS.
        # The flow is idempotent at each step.
        
        # Step 1: Establish Ground Truth
        system_map = await self._create_system_structure_map(force_overwrite)
        
        # Step 2: Get Raw Layout from Document
        await self._execute_layout_parser_workflow(force_overwrite)

        # Step 3: Group Raw Layout by Ground Truth Context
        await self._group_layout_blocks_by_zielobjekt(system_map, force_overwrite)
        
        # Step 4: Use AI to transform grouped raw layout into structured data
        await self._refine_grouped_blocks_with_ai(system_map, force_overwrite)

        return {"status": "success", "message": f"Stage {self.STAGE_NAME} completed successfully."}