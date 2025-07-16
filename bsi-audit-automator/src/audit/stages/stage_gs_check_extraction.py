# bsi-audit-automator/src/audit/stages/stage_gs_check_extraction.py
import logging
import json
import asyncio
from typing import Dict, Any, List
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
    It orchestrates a multi-step process:
    1.  Builds an authoritative "ground truth" map of the system structure.
    2.  Uses Document AI to perform high-fidelity form parsing on the Grundschutz-Check PDF.
    3.  Chunks the large Document AI JSON output to avoid token limits.
    4.  Uses Gemini in parallel to refine and structure each chunk.
    5.  Merges the refined chunks into a single, coherent output file.
    The stage is idempotent and saves intermediate results for debugging and cost-efficiency.
    """
    STAGE_NAME = "Grundschutz-Check-Extraction"
    PROMPT_CONFIG_PATH = "assets/json/prompt_config.json"
    GROUND_TRUTH_MAP_PATH = "output/results/intermediate/system_structure_map.json"
    
    # Intermediate file for raw, unchunked Document AI output
    DOC_AI_RAW_OUTPUT_PATH = "output/results/intermediate/doc_ai_raw_output.json"
    FINAL_MERGED_OUTPUT_PATH = "output/results/intermediate/extracted_grundschutz_check_merged.json"

    # Constants for chunking
    CHUNK_TARGET_CHAR_SIZE = 45000  # Target character size per chunk (approx. < 50k tokens)
    CHUNK_OVERLAP_ENTITY_COUNT = 10 # Number of entities to overlap between chunks

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

    def _chunk_doc_ai_entities(self, entities: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """
        Splits the list of Document AI entities into smaller chunks based on character count,
        with a defined overlap to maintain context between chunks.
        """
        if not entities:
            return []
            
        chunks = []
        current_chunk = []
        current_char_count = 0
        
        for i, entity in enumerate(entities):
            entity_str = json.dumps(entity)
            entity_char_count = len(entity_str)

            if current_char_count + entity_char_count > self.CHUNK_TARGET_CHAR_SIZE and current_chunk:
                chunks.append(current_chunk)
                # Create overlap by taking the last few items from the previous chunk
                overlap_start_index = max(0, len(current_chunk) - self.CHUNK_OVERLAP_ENTITY_COUNT)
                current_chunk = current_chunk[overlap_start_index:]
                current_char_count = len(json.dumps(current_chunk))
            
            current_chunk.append(entity)
            current_char_count += entity_char_count

        if current_chunk:
            chunks.append(current_chunk)
            
        logging.info(f"Split {len(entities)} Document AI entities into {len(chunks)} chunks for processing.")
        return chunks

    async def _refine_single_chunk(self, entity_chunk: List[Dict[str, Any]], ground_truth_map: Dict[str, Any], chunk_index: int) -> Dict[str, Any]:
        """
        Sends a single chunk of Document AI entities to Gemini for refinement.
        """
        config = self.prompt_config["stages"]["Chapter-3"]["detailsZumItGrundschutzCheck_extraction"]
        prompt_template = config["prompt"]
        schema = self._load_asset_json(config["schema_path"])

        # Create a partial Document AI output structure for this chunk
        chunk_doc_ai_output = {"entities": entity_chunk}

        prompt = prompt_template.format(
            ground_truth_map_json=json.dumps(ground_truth_map, indent=2, ensure_ascii=False),
            document_ai_json=json.dumps(chunk_doc_ai_output, indent=2, ensure_ascii=False)
        )
        
        try:
            refined_data = await self.ai_client.generate_json_response(
                prompt,
                schema,
                gcs_uris=[],
                request_context_log=f"GS-Check-Refinement-Chunk-{chunk_index + 1}"
            )
            return refined_data
        except Exception as e:
            logging.error(f"Failed to refine chunk {chunk_index + 1}: {e}", exc_info=True)
            return {"anforderungen": []} # Return empty structure on failure

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
            logging.info(f"Found existing Document AI output at '{self.DOC_AI_RAW_OUTPUT_PATH}'. Skipping Document AI processing.")
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

        # --- NEW: Chunking and Parallel Refinement Logic ---
        all_entities = doc_ai_output.get("entities", [])
        entity_chunks = self._chunk_doc_ai_entities(all_entities)

        if not entity_chunks:
            logging.warning("No entities found in Document AI output to process.")
            return {"status": "failed", "message": "No entities in Doc AI output."}

        refinement_tasks = [
            self._refine_single_chunk(chunk, ground_truth_map, i)
            for i, chunk in enumerate(entity_chunks)
        ]
        
        refined_results = await asyncio.gather(*refinement_tasks)
        
        # Merge the results from all chunks
        merged_anforderungen = []
        for result in refined_results:
            merged_anforderungen.extend(result.get("anforderungen", []))
        
        # --- Deduplicate based on a composite key of requirement ID and zielobjekt kuerzel ---
        final_anforderungen_map = {}
        for anforderung in merged_anforderungen:
            key = (anforderung.get("id"), anforderung.get("zielobjekt_kuerzel"))
            if key[0] and key[1]: # Only add if the key is complete
                 final_anforderungen_map[key] = anforderung # Overwrite duplicates, last one wins

        final_data = {"anforderungen": list(final_anforderungen_map.values())}
        logging.info(f"Merged and deduplicated refined chunks, resulting in {len(final_data['anforderungen'])} unique requirements.")
        
        # Save the final merged and refined output
        await self.gcs_client.upload_from_string_async(
            json.dumps(final_data, indent=2, ensure_ascii=False),
            self.FINAL_MERGED_OUTPUT_PATH
        )
        logging.info(f"Successfully created and saved refined Grundschutz-Check data to {self.FINAL_MERGED_OUTPUT_PATH}")
        
        return {"status": "success", "message": f"Successfully generated intermediate files."}