# src/audit/stages/stage_gs_check_extraction.py
import logging
import json
import asyncio
from typing import Dict, Any, List, Tuple
from datetime import datetime
import fitz  # PyMuPDF
from google.cloud.exceptions import NotFound
from collections import defaultdict
import re

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.ai_client import AiClient
from src.clients.rag_client import RagClient
from src.audit.stages.control_catalog import ControlCatalog

class GrundschutzCheckExtractionRunner:
    """
    A dedicated stage for the "Ground-Truth-Driven Semantic Chunking" strategy.
    It extracts data from the Grundschutz-Check document, merges and refines it,
    and saves the high-quality result as an intermediate file for Chapter 3 to use.
    """
    STAGE_NAME = "Grundschutz-Check-Extraction"
    PROMPT_CONFIG_PATH = "assets/json/prompt_config.json"
    GROUND_TRUTH_MAP_PATH = "output/results/intermediate/system_structure_map.json"
    INTERMEDIATE_CHECK_RESULTS_PATH = "output/results/intermediate/extracted_grundschutz_check_merged.json"
    RAW_EXTRACTION_PATH_PREFIX = "output/results/intermediate/raw_extraction/"
    CHUNK_SIZES = [19, 21]

    def __init__(self, config: AppConfig, gcs_client: GcsClient, ai_client: AiClient, rag_client: RagClient):
        self.config = config
        self.gcs_client = gcs_client
        self.ai_client = ai_client
        self.rag_client = rag_client
        self.control_catalog = ControlCatalog()
        self.prompt_config = self._load_asset_json(self.PROMPT_CONFIG_PATH)
        logging.info(f"Initialized runner for stage: {self.STAGE_NAME}")

    def _load_asset_json(self, path: str) -> dict:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    async def _build_system_structure_map(self) -> Dict[str, Any]:
        """Orchestrates the creation of the ground truth map based on file existence."""
        try:
            map_data = await self.gcs_client.read_json_async(self.GROUND_TRUTH_MAP_PATH)
            logging.info(f"Using cached ground truth map from: {self.GROUND_TRUTH_MAP_PATH}")
            return map_data
        except NotFound:
            logging.info("Ground truth map not found. Generating new ground truth map.")

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
        baustein_mappings = defaultdict(list)
        for mapping in mappings_res.get("mappings", []):
            baustein_mappings[mapping["baustein_id"]].append(mapping["zielobjekt_kuerzel"])

        DETERMINISTIC_PREFIXES = ("ISMS", "ORP", "CON", "OPS", "DER")
        for layer in self.control_catalog._baustein_map.keys():
             if layer.startswith(DETERMINISTIC_PREFIXES):
                 baustein_mappings[layer] = ["Informationsverbund"]
        
        if "Informationsverbund" not in [z['kuerzel'] for z in zielobjekte_list]:
            zielobjekte_list.append({"kuerzel": "Informationsverbund", "name": "Gesamter Informationsverbund"})
            
        final_map = {"zielobjekte": zielobjekte_list, "baustein_to_zielobjekt_mapping": baustein_mappings}
        await self.gcs_client.upload_from_string_async(json.dumps(final_map, indent=2, ensure_ascii=False), self.GROUND_TRUTH_MAP_PATH)
        logging.info(f"Successfully created and saved ground truth map to {self.GROUND_TRUTH_MAP_PATH}")
        return final_map

    async def _run_extraction_pass(self, doc: fitz.Document, chunk_size: int, prompt_template: str, schema: Dict[str, Any], zielobjekte_list: List[Dict]) -> List[Dict[str, Any]]:
        """
        Runs a single data extraction pass, returning raw JSON results from the AI.
        As a side-effect, it saves each raw result to GCS for caching.
        """
        logging.info(f"Starting extraction pass with chunk size: {chunk_size} pages.")
        total_pages = len(doc)
        if total_pages == 0: return []

        zielobjekte_list_json = json.dumps(zielobjekte_list, indent=2, ensure_ascii=False)
        prompt_with_context = prompt_template.format(zielobjekte_list_json=zielobjekte_list_json)

        tasks, temp_blob_names, chunk_details = [], [], []
        for i in range(0, total_pages, chunk_size):
            chunk_doc = fitz.open()
            chunk_doc.insert_pdf(doc, from_page=i, to_page=min(i + chunk_size - 1, total_pages - 1))
            
            chunk_blob_name = f"output/results/intermediate/temp_chunk_{chunk_size}_{i}.pdf"
            await self.gcs_client.upload_from_string_async(chunk_doc.write(), chunk_blob_name, content_type="application/pdf")
            temp_blob_names.append(chunk_blob_name)
            
            chunk_details.append({"pass_size": chunk_size, "page_start": i})

            task = self.ai_client.generate_json_response(
                prompt=prompt_with_context, json_schema=schema, gcs_uris=[f"gs://{self.config.bucket_name}/{chunk_blob_name}"],
                request_context_log=f"GS-Check-Extraction (ChunkSize: {chunk_size}, Pages: {i}-{i+chunk_size-1})"
            )
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Save raw results to GCS for caching and cleanup temp PDFs
        save_tasks = []
        for i, res in enumerate(results):
            if isinstance(res, dict):
                details = chunk_details[i]
                raw_blob_name = f"{self.RAW_EXTRACTION_PATH_PREFIX}pass_{details['pass_size']}_pages_{details['page_start']}.json"
                save_tasks.append(self.gcs_client.upload_from_string_async(json.dumps(res, indent=2, ensure_ascii=False), raw_blob_name))
        await asyncio.gather(*save_tasks)
        
        for blob_name in temp_blob_names:
            try: self.gcs_client.bucket.blob(blob_name).delete()
            except Exception as e: logging.warning(f"Could not delete temp blob {blob_name}: {e}")

        return [res for res in results if isinstance(res, dict)]

    def _merge_and_refine_results(self, all_anforderungen: List, all_headings: List, ground_truth_map: Dict) -> List[Dict[str, Any]]:
        """Merges and refines extracted data into a clean, de-duplicated list."""
        zielobjekte_map = {z['kuerzel']: z['name'] for z in ground_truth_map.get('zielobjekte', [])}
        
        unique_headings = {f"{h.get('kuerzel', '')}-{h.get('pagenumber', 0)}": h for h in all_headings if h.get('kuerzel')}.values()
        
        if self.config.is_test_mode:
            found_kuerzel = sorted(list(set(h.get('kuerzel') for h in unique_headings if h.get('kuerzel'))))
            logging.info(f"[TEST MODE] Found unique Zielobjekt Kürzel in document: {found_kuerzel}")

        sorted_headings = sorted(list(unique_headings), key=lambda x: x.get('pagenumber', 0))

        # --- Two-Tier Assignment Logic ---
        for anforderung in all_anforderungen:
            # Tier 1: Check if the AI made a direct, same-page assignment.
            if anforderung.get('zielobjekt_kuerzel'):
                anforderung['zielobjekt_name'] = zielobjekte_map.get(anforderung['zielobjekt_kuerzel'], "Name not in map")
                continue

            # Tier 2: If no direct assignment, use the deterministic fallback logic.
            else:
                page = anforderung.get('pagenumber', 0)
                assigned_kuerzel = "Unassigned"
                for heading in reversed(sorted_headings):
                    if page > heading.get('pagenumber', 0):
                        assigned_kuerzel = heading['kuerzel']
                        break
                anforderung['zielobjekt_kuerzel'] = assigned_kuerzel
        
        grouped_anforderungen = defaultdict(list)
        for a in all_anforderungen:
            if not a.get('id'): continue
            key = (a['zielobjekt_kuerzel'], a['id'])
            grouped_anforderungen[key].append(a)
        
        final_list = []
        STATUS_PRIORITY = {'Nein': 4, 'teilweise': 3, 'Ja': 2, 'entbehrlich': 1, 'N/A': 0}

        for (kuerzel, anforderung_id), items in grouped_anforderungen.items():
            best_item = {}
            best_item['id'] = anforderung_id
            best_item['zielobjekt_kuerzel'] = kuerzel
            best_item['zielobjekt_name'] = zielobjekte_map.get(kuerzel, "Unbekanntes Zielobjekt")
            
            best_item['titel'] = max(items, key=lambda x: len(x.get('titel', ''))).get('titel', '')
            
            all_erlaeuterungen = " ".join([i.get('umsetzungserlaeuterung', '') for i in items])
            unique_sentences = list(dict.fromkeys(re.split(r'(?<=[.!?])\s+', all_erlaeuterungen)))
            best_item['umsetzungserlaeuterung'] = " ".join(filter(None, unique_sentences)).strip()

            best_status = max(items, key=lambda x: STATUS_PRIORITY.get(x.get('umsetzungsstatus'), 0)).get('umsetzungsstatus')
            best_item['umsetzungsstatus'] = best_status

            latest_date = datetime(1970, 1, 1)
            for item in items:
                date_str = item.get("datumLetztePruefung")
                try:
                    parsed_date = datetime.fromisoformat(str(date_str).split("T")[0]) if date_str and '-' in str(date_str) else datetime.strptime(str(date_str), "%d.%m.%Y")
                    if parsed_date > latest_date: latest_date = parsed_date
                except (ValueError, TypeError): continue
            best_item['datumLetztePruefung'] = latest_date.strftime("%Y-%m-%d") if latest_date.year > 1970 else "1970-01-01"

            final_list.append(best_item)
            
        DETERMINISTIC_PREFIXES = ("ISMS.", "ORP.", "CON.", "OPS.", "DER.")
        for item in final_list:
            if item.get('id', '').startswith(DETERMINISTIC_PREFIXES):
                item['zielobjekt_kuerzel'] = "Informationsverbund"
                item['zielobjekt_name'] = "Gesamter Informationsverbund"

        logging.info(f"Merge & Refine complete. Final list has {len(final_list)} unique requirements.")
        return final_list

    async def _get_all_pass_results(self, ground_truth_map: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Orchestrates getting the raw extraction results, either from cache or by
        running the AI extraction passes, based on file existence.
        """
        raw_blobs = self.gcs_client.list_files(prefix=self.RAW_EXTRACTION_PATH_PREFIX)
        if raw_blobs:
            logging.info(f"Found {len(raw_blobs)} cached raw extraction files. Loading from cache.")
            load_tasks = [self.gcs_client.read_json_async(blob.name) for blob in raw_blobs]
            results = await asyncio.gather(*load_tasks, return_exceptions=True)
            return [res for res in results if isinstance(res, dict)]
        
        logging.info("Running AI extraction for Grundschutz-Check. No cached files found.")
        uris = self.rag_client.get_gcs_uris_for_categories(["Grundschutz-Check"])
        if not uris:
            raise FileNotFoundError("Could not find document with category 'Grundschutz-Check'. This is required.")

        blob_name = uris[0].replace(f"gs://{self.config.bucket_name}/", "")
        pdf_bytes = self.gcs_client.download_blob_as_bytes(self.gcs_client.bucket.blob(blob_name))
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        config = self.prompt_config["stages"]["Chapter-3"]["detailsZumItGrundschutzCheck_extraction"]
        prompt = config["prompt"]
        schema = self._load_asset_json(config["schema_path"])

        pass_tasks = [
            self._run_extraction_pass(doc, cs, prompt, schema, ground_truth_map.get('zielobjekte', []))
            for cs in self.CHUNK_SIZES
        ]
        list_of_lists = await asyncio.gather(*pass_tasks)
        
        return [item for sublist in list_of_lists for item in sublist]

    async def run(self, force_overwrite: bool = False) -> Dict[str, Any]:
        """
        Main execution method for the stage. It orchestrates the entire workflow
        based on file existence, ignoring the --force flag passed by the controller.
        The controller is responsible for deciding IF this stage runs, this method
        decides HOW it runs (i.e., using cache if available).
        """
        logging.info(f"Executing stage: {self.STAGE_NAME}. Caching is managed by manual file deletion, ignoring the --force flag.")
        
        ground_truth_map = await self._build_system_structure_map()
        
        # Get raw results from all passes (either from cache or by running the AI)
        all_pass_results = await self._get_all_pass_results(ground_truth_map)
        
        # Process the raw results into clean lists
        all_anforderungen = [item for res in all_pass_results for item in res.get("anforderungen", [])]
        all_headings = [item for res in all_pass_results for item in res.get("chapter_headings", [])]

        # Merge, refine, and save the final data
        final_anforderungen = self._merge_and_refine_results(all_anforderungen, all_headings, ground_truth_map)
        
        await self.gcs_client.upload_from_string_async(
            json.dumps({"anforderungen": final_anforderungen}, indent=2, ensure_ascii=False),
            self.INTERMEDIATE_CHECK_RESULTS_PATH
        )
        logging.info(f"Successfully created and saved refined Grundschutz-Check data to {self.INTERMEDIATE_CHECK_RESULTS_PATH}")
        
        return {"status": "success", "message": f"Generated intermediate files: {self.GROUND_TRUTH_MAP_PATH} and {self.INTERMEDIATE_CHECK_RESULTS_PATH}"}