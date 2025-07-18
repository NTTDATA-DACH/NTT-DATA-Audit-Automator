# bsi-audit-automator/src/audit/stages/stage_gs_check_extraction.py
import logging
import json
import asyncio
import fitz  # PyMuPDF
import re
from typing import Dict, Any, List, Tuple
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
            return await self.gcs_client.read_json_async(self.GROUND_TRUTH_MAP_PATH)

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
        if not check_uris: raise FileNotFoundError("Could not find 'Grundschutz-Check' document.")
        
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
            json.dumps(final_layout_json, indent=2, ensure_ascii=False), self.FINAL_MERGED_LAYOUT_PATH
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
    
    def _get_text_from_layout(self, layout: Dict[str, Any], document_text: str) -> str:
        """Helper to extract text from a single layout object using its textAnchor."""
        text = ""
        if layout and layout.get("textAnchor", {}).get("textSegments"):
            for segment in layout["textAnchor"]["textSegments"]:
                start = int(segment.get("startIndex", 0))
                end = int(segment.get("endIndex", 0))
                text += document_text[start:end]
        return text

    def _get_text_from_block_recursive(self, block: Dict[str, Any], document_text: str) -> str:
        """Recursively traverses a block to get all its text content."""
        text_parts = []
        def traverse(element):
            if isinstance(element, dict):
                if 'layout' in element:
                    text_parts.append(self._get_text_from_layout(element['layout'], document_text))
                for value in element.values(): traverse(value)
            elif isinstance(element, list):
                for item in element: traverse(item)
        traverse(block)
        return " ".join(part.strip() for part in text_parts if part.strip())

    async def _group_layout_blocks_by_zielobjekt(self, system_map: Dict[str, Any], force_overwrite: bool):
        """[Step 3] Deterministically groups layout blocks by the Zielobjekt they belong to."""
        if not force_overwrite and self.gcs_client.blob_exists(self.GROUPED_BLOCKS_PATH):
            logging.info(f"Grouped layout blocks file already exists. Skipping grouping.")
            return

        logging.info("Grouping layout blocks by Zielobjekt context...")
        layout_data = await self.gcs_client.read_json_async(self.FINAL_MERGED_LAYOUT_PATH)
        all_blocks = layout_data.get("documentLayout", {}).get("blocks", [])
        full_text = layout_data.get("text", "")

        zielobjekte = system_map.get("zielobjekte", [])
        # Create a lookup of lowercase name/kuerzel to the canonical kuerzel
        zielobjekt_lookup = {z['kuerzel'].lower(): z['kuerzel'] for z in zielobjekte}
        zielobjekt_lookup.update({z['name'].lower(): z['kuerzel'] for z in zielobjekte})

        grouped_blocks = {"_UNGROUPED_": []}
        for zo in zielobjekte: grouped_blocks[zo['kuerzel']] = []
        
        current_zielobjekt_kuerzel = "_UNGROUPED_"
        for block in all_blocks:
            block_text = self._get_text_from_block_recursive(block, full_text).lower()
            if not block_text: continue

            # Find a new context if the block text is a potential heading
            found_kuerzel = None
            for lookup_key, kuerzel_val in zielobjekt_lookup.items():
                # A heading is likely a very close match, not just a substring
                if re.fullmatch(r'\s*' + re.escape(lookup_key) + r'\s*', block_text, re.IGNORECASE):
                    found_kuerzel = kuerzel_val
                    break
            
            if found_kuerzel and current_zielobjekt_kuerzel != found_kuerzel:
                logging.info(f"Switched context to Zielobjekt: '{found_kuerzel}'")
                current_zielobjekt_kuerzel = found_kuerzel
            
            grouped_blocks[current_zielobjekt_kuerzel].append(block)

        await self.gcs_client.upload_from_string_async(
            json.dumps({"zielobjekt_grouped_blocks": grouped_blocks}, indent=2, ensure_ascii=False),
            self.GROUPED_BLOCKS_PATH
        )
        logging.info(f"Saved grouped layout blocks to {self.GROUPED_BLOCKS_PATH}")

    async def _refine_grouped_blocks_with_ai(self, system_map: Dict[str, Any], force_overwrite: bool):
        """[Step 4] Processes each group of blocks with Gemini to extract structured requirements."""
        if not force_overwrite and self.gcs_client.blob_exists(self.FINAL_CHECK_RESULTS_PATH):
            logging.info(f"Final extracted check results file exists. Skipping AI refinement.")
            return

        logging.info("Refining grouped blocks with AI to extract structured requirements...")
        grouped_blocks_data = await self.gcs_client.read_json_async(self.GROUPED_BLOCKS_PATH)
        groups = grouped_blocks_data.get("zielobjekt_grouped_blocks", {})
        
        refine_config = self.prompt_config["stages"]["Chapter-3"]["refine_layout_parser_group"]
        prompt_template = refine_config["prompt"]
        schema = self._load_asset_json(refine_config["schema_path"])
        
        zielobjekt_map = {z['kuerzel']: z['name'] for z in system_map.get("zielobjekte", [])}

        async def generate_and_tag(kuerzel, blocks):
            name = zielobjekt_map.get(kuerzel, "Unbekannt")
            prompt = prompt_template.format(zielobjekt_blocks_json=json.dumps(blocks, indent=2))
            try:
                result = await self.ai_client.generate_json_response(
                    prompt, schema, request_context_log=f"RefineGroup: {kuerzel}"
                )
                return kuerzel, name, result
            except Exception as e:
                logging.error(f"AI refinement failed for Zielobjekt '{kuerzel}': {e}")
                return kuerzel, name, None

        tasks = [generate_and_tag(kuerzel, blocks) for kuerzel, blocks in groups.items() if kuerzel != "_UNGROUPED_" and blocks]
        results = await asyncio.gather(*tasks)

        all_anforderungen = []
        for kuerzel, name, result_data in results:
            if result_data:
                for anforderung in result_data.get("anforderungen", []):
                    anforderung['zielobjekt_kuerzel'] = kuerzel
                    anforderung['zielobjekt_name'] = name
                    all_anforderungen.append(anforderung)
        
        final_output = {"anforderungen": all_anforderungen}
        await self.gcs_client.upload_from_string_async(
            json.dumps(final_output, indent=2, ensure_ascii=False), self.FINAL_CHECK_RESULTS_PATH
        )
        logging.info(f"Saved final refined check data with {len(all_anforderungen)} requirements to {self.FINAL_CHECK_RESULTS_PATH}")

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