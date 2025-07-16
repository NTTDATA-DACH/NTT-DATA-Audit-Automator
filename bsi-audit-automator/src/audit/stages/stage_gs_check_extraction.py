# bsi-audit-automator/src/audit/stages/stage_gs_check_extraction.py
import logging
import json
import asyncio
from typing import Dict, Any
from collections import defaultdict

from google.cloud.exceptions import NotFound

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.document_ai_client import DocumentAiClient
from src.clients.ai_client import AiClient
from src.clients.rag_client import RagClient


class GrundschutzCheckExtractionRunner:
    """
    A dedicated stage for the "Ground-Truth-Driven Semantic Chunking" strategy.
    It orchestrates a two-stage process:
    1.  Uses Document AI to perform high-fidelity form parsing on the Grundschutz-Check PDF.
    2.  Uses Gemini to refine and structure the JSON output from Document AI.
    The stage is idempotent and saves intermediate results for debugging and cost-efficiency.
    """
    STAGE_NAME = "Grundschutz-Check-Extraction"
    PROMPT_CONFIG_PATH = "assets/json/prompt_config.json"
    GROUND_TRUTH_MAP_PATH = "output/results/intermediate/system_structure_map.json"
    DOC_AI_RAW_OUTPUT_PATH = "output/results/intermediate/doc_ai_raw_output.json"
    FINAL_MERGED_OUTPUT_PATH = "output/results/intermediate/extracted_grundschutz_check_merged.json"

    def __init__(self, config: AppConfig, gcs_client: GcsClient, doc_ai_client: DocumentAiClient, ai_client: AiClient, rag_client: RagClient):
        self.config = config
        self.gcs_client = gcs_client
        self.doc_ai_client = doc_ai_client
        self.ai_client = ai_client
        self.rag_client = rag_client
        self.prompt_config = self._load_asset_json(self.PROMPT_CONFIG_PATH)
        logging.info(f"Initialized runner for stage: {self.STAGE_NAME}")

    def _load_asset_json(self, path: str) -> dict:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    async def _build_system_structure_map(self, force_remap: bool) -> Dict[str, Any]:
        """Orchestrates the creation of the ground truth map."""
        if force_remap:
            logging.info("Force remapping enabled. Generating new ground truth map.")
        else:
            try:
                map_data = await self.gcs_client.read_json_async(self.GROUND_TRUTH_MAP_PATH)
                logging.info(f"Using cached ground truth map from: {self.GROUND_TRUTH_MAP_PATH}")
                return map_data
            except NotFound:
                logging.info("Ground truth map not found. Generating...")

        # --- Generation logic only runs if force_remap is true or file not found ---
        zielobjekte_uris = self.rag_client.get_gcs_uris_for_categories(["Strukturanalyse"])
        zielobjekte_config = self.prompt_config["stages"]["Chapter-3-Ground-Truth"]["extract_zielobjekte"]
        zielobjekte_res = await self.ai_client.generate_json_response(
            prompt=zielobjekte_config["prompt"],
            json_schema=self._load_asset_json(zielobjekte_config["schema_path"]),
            gcs_uris=zielobjekte_uris,
            request_context_log="GT: Extract Zielobjekte"
        )
        zielobjekte_list = zielobjekte_res.get("zielobjekte", [])

        modellierung_uris = self.rag_client.get_gcs_uris_for_categories(["Modellierung"])
        mappings_config = self.prompt_config["stages"]["Chapter-3-Ground-Truth"]["extract_baustein_mappings"]
        mappings_res = await self.ai_client.generate_json_response(
            prompt=mappings_config["prompt"],
            json_schema=self._load_asset_json(mappings_config["schema_path"]),
            gcs_uris=modellierung_uris,
            request_context_log="GT: Extract Baustein Mappings"
        )
        
        # --- IMPROVEMENT: Correctly group the mappings into a dictionary ---
        baustein_mappings = defaultdict(list)
        for mapping in mappings_res.get("mappings", []):
            baustein_id = mapping.get("baustein_id")
            zielobjekt_kuerzel = mapping.get("zielobjekt_kuerzel")
            if baustein_id and zielobjekt_kuerzel:
                baustein_mappings[baustein_id].append(zielobjekt_kuerzel)
        
        final_map = {
            "zielobjekte": zielobjekte_list,
            "baustein_to_zielobjekt_mapping": baustein_mappings
        }
        
        await self.gcs_client.upload_from_string_async(
            json.dumps(final_map, indent=2, ensure_ascii=False), 
            self.GROUND_TRUTH_MAP_PATH
        )
        logging.info(f"Successfully created and saved ground truth map to {self.GROUND_TRUTH_MAP_PATH}")
        return final_map

    async def run(self, force_overwrite: bool = False) -> Dict[str, Any]:
        """
        Main execution method for the stage. It creates the ground-truth map and
        the refined Grundschutz-Check data, saving them to GCS.
        """
        logging.info(f"Executing stage: {self.STAGE_NAME}")
        
        # --- IDEMPOTENCY CHECK 1: CHECK FOR FINAL OUTPUT ---
        if not force_overwrite and self.gcs_client.blob_exists(self.FINAL_MERGED_OUTPUT_PATH):
            logging.info(f"Final merged output file already exists at '{self.FINAL_MERGED_OUTPUT_PATH}'. Skipping entire stage.")
            return {"status": "skipped", "reason": "Final output file already exists."}

        # Build the ground truth map first, it's a dependency for the Gemini prompt
        ground_truth_map = await self._build_system_structure_map(force_remap=force_overwrite)
        
        doc_ai_output = None
        # --- IDEMPOTENCY CHECK 2: CHECK FOR INTERMEDIATE DOC AI OUTPUT ---
        if not force_overwrite and self.gcs_client.blob_exists(self.DOC_AI_RAW_OUTPUT_PATH):
            logging.info(f"Found existing Document AI output at '{self.DOC_AI_RAW_OUTPUT_PATH}'. Skipping Document AI step.")
            doc_ai_output = await self.gcs_client.read_json_async(self.DOC_AI_RAW_OUTPUT_PATH)
        else:
            logging.info("Intermediate Document AI output not found or --force used. Running Document AI processing.")
            # STEP 1: Run Document AI Processing
            check_uris = self.rag_client.get_gcs_uris_for_categories(["Grundschutz-Check", "test.pdf"])
            if not check_uris:
                raise FileNotFoundError("Could not find document with category 'Grundschutz-Check' or 'test.pdf'. This is required.")
            
            # If both real and test docs are present, prefer the test one.
            doc_uri_to_process = next((uri for uri in check_uris if 'test.pdf' in uri), check_uris[0])

            doc_ai_output = await self.doc_ai_client.process_document_async(doc_uri_to_process)
        
        if not doc_ai_output:
            raise RuntimeError("Document AI processing failed to produce an output.")

        # STEP 2: Run Gemini for Refinement
        logging.info("Starting Gemini refinement of Document AI output.")
        config = self.prompt_config["stages"]["Chapter-3"]["detailsZumItGrundschutzCheck_extraction"]
        prompt_template = config["prompt"]
        schema = self._load_asset_json(config["schema_path"])

        # Prepare the context for the refiner prompt
        prompt = prompt_template.format(
            ground_truth_map_json=json.dumps(ground_truth_map, indent=2, ensure_ascii=False),
            document_ai_json=json.dumps(doc_ai_output, indent=2, ensure_ascii=False)
        )
        
        # The prompt is now self-contained; no gcs_uris are needed for the Gemini call
        refined_data = await self.ai_client.generate_json_response(
            prompt,
            schema,
            gcs_uris=[], # IMPORTANT: No files attached here
            request_context_log="GS-Check-Refinement"
        )
        
        # STEP 3: Save the final output
        await self.gcs_client.upload_from_string_async(
            json.dumps(refined_data, indent=2, ensure_ascii=False),
            self.FINAL_MERGED_OUTPUT_PATH
        )
        logging.info(f"Successfully created and saved refined Grundschutz-Check data to {self.FINAL_MERGED_OUTPUT_PATH}")
        
        return {"status": "success", "message": f"Successfully generated intermediate files."}