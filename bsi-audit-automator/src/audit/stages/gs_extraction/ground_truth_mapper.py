# bsi-audit-automator/src/audit/stages/gs_extraction/ground_truth_mapper.py
import logging
import json
import os
from typing import Dict, Any, List

from src.clients.ai_client import AiClient
from src.clients.rag_client import RagClient
from src.clients.gcs_client import GcsClient
from src.constants import GROUND_TRUTH_MAP_PATH


class GroundTruthMapper:
    """
    Responsible for creating the authoritative system structure map by extracting
    Zielobjekte and Baustein-to-Zielobjekt mappings from customer documents.
    """
    
    GROUND_TRUTH_MODEL = os.getenv("GS_GROUND_TRUTH_MODEL", "gemini-2.5-pro")
    PROMPT_CONFIG_PATH = "assets/json/prompt_config.json"

    def __init__(self, ai_client: AiClient, rag_client: RagClient, gcs_client: GcsClient):
        self.ai_client = ai_client
        self.rag_client = rag_client
        self.gcs_client = gcs_client
        self.prompt_config = self._load_asset_json(self.PROMPT_CONFIG_PATH)

    def _load_asset_json(self, path: str) -> dict:
        """Load JSON configuration from assets."""
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _structure_mappings(self, flat_mappings: List[Dict[str, str]]) -> Dict[str, List[str]]:
        """Convert flat mapping list from AI into structured dict of Baustein ID to Zielobjekt Kürzel list."""
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

    async def create_system_structure_map(self, force_overwrite: bool) -> Dict[str, Any]:
        """
        Create the authoritative system structure map by extracting Zielobjekte (from A.1)
        and Baustein-to-Zielobjekt mappings (from A.3).
        This map serves as "Ground Truth".
        
        Args:
            force_overwrite: If True, regenerate even if map already exists
            
        Returns:
            Dict containing zielobjekte list and baustein_to_zielobjekt_mapping
        """
        if not force_overwrite and self.gcs_client.blob_exists(GROUND_TRUTH_MAP_PATH):
            logging.info(f"System structure map already exists. Loading from '{GROUND_TRUTH_MAP_PATH}'.")
            try:
                system_map = await self.gcs_client.read_json_async(GROUND_TRUTH_MAP_PATH)
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
                prompt=z_task_config["prompt"], 
                json_schema=self._load_asset_json(z_task_config["schema_path"]), 
                gcs_uris=z_uris, 
                request_context_log="GT: extract_zielobjekte",
                model_override=self.GROUND_TRUTH_MODEL
            )

            # Extract Mappings from Modellierung (A.3)
            m_task_config = gt_config["extract_baustein_mappings"]
            m_uris = self.rag_client.get_gcs_uris_for_categories(["Modellierung"])
            mappings_result = await self.ai_client.generate_json_response(
                prompt=m_task_config["prompt"], 
                json_schema=self._load_asset_json(m_task_config["schema_path"]), 
                gcs_uris=m_uris, 
                request_context_log="GT: extract_baustein_mappings",
                model_override=self.GROUND_TRUTH_MODEL
            )

            # Construct the system map
            system_map = {
                "zielobjekte": zielobjekte_result.get("zielobjekte", []),
                "baustein_to_zielobjekt_mapping": self._structure_mappings(mappings_result.get("mappings", []))
            }
            
            # Save to GCS
            await self.gcs_client.upload_from_string_async(
                json.dumps(system_map, indent=2, ensure_ascii=False), 
                self.GROUND_TRUTH_MAP_PATH
            )
            logging.info(f"Successfully created and saved system structure map to {self.GROUND_TRUTH_MAP_PATH}.")
            
            return system_map
            
        except Exception as e:
            logging.error(f"Failed to create system structure map: {e}", exc_info=True)
            raise