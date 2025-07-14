# src/audit/stages/stage_1_general.py
import logging
import json
import asyncio
from typing import Dict, Any

from src.config import AppConfig
from src.clients.ai_client import AiClient
from src.clients.rag_client import RagClient

class Chapter1Runner:
    """Handles generating content for Chapter 1, with most sections being manual placeholders."""
    STAGE_NAME = "Chapter-1"
    PROMPT_CONFIG_PATH = "assets/json/prompt_config.json"

    def __init__(self, config: AppConfig, ai_client: AiClient, rag_client: RagClient):
        self.config = config
        self.ai_client = ai_client
        self.rag_client = rag_client
        self.prompt_config = self._load_asset_json(self.PROMPT_CONFIG_PATH)
        logging.info(f"Initialized runner for stage: {self.STAGE_NAME}")

    def _load_asset_json(self, path: str) -> dict:
        with open(path, 'r', encoding='utf-8') as f: return json.load(f)

    async def _process_informationsverbund(self) -> Dict[str, Any]:
        """Handles 1.4 Informationsverbund using a filtered document query."""
        logging.info("Processing 1.4 Informationsverbund...")
        
        stage_config = self.prompt_config["stages"]["Chapter-1"]["informationsverbund"]
        prompt_template = stage_config["prompt"]
        schema = self._load_asset_json(stage_config["schema_path"])
        
        source_categories = ['Informationsverbund', 'Strukturanalyse']
        gcs_uris = self.rag_client.get_gcs_uris_for_categories(source_categories)
        
        if not gcs_uris:
            logging.warning(f"No documents found for categories {source_categories}. Generating deterministic response.")
            return {
                "kurzbezeichnung": "Nicht ermittelt",
                "kurzbeschreibung": "Der Geltungsbereich des Informationsverbunds konnte aus den bereitgestellten Dokumenten nicht eindeutig ermittelt werden. Dies muss manuell geklärt und dokumentiert werden.",
                "finding": {
                    "category": "AS",
                    "description": "Die Abgrenzung des Geltungsbereichs ist unklar, da keine Dokumente der Kategorien 'Informationsverbund' oder 'Strukturanalyse' gefunden wurden. Dies ist eine schwerwiegende Abweichung."
                }
            }
            
        return await self.ai_client.generate_json_response(
            prompt=prompt_template,
            json_schema=schema,
            gcs_uris=gcs_uris,
            request_context_log="Chapter-1: informationsverbund"
        )

    async def run(self, force_overwrite: bool = False) -> dict:
        """Executes the generation logic for Chapter 1."""
        logging.info(f"Executing stage: {self.STAGE_NAME}")
        
        informationsverbund_result = await self._process_informationsverbund()

        final_result = {
            "informationsverbund": informationsverbund_result,
            "audittyp": {
                "content": self.config.audit_type
            }
        }

        logging.info(f"Successfully generated data for stage {self.STAGE_NAME}")
        return final_result