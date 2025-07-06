# src/audit/stages/stage_4_pruefplan.py
import logging
import json
import asyncio
from typing import Dict, Any

from src.config import AppConfig
from src.clients.ai_client import AiClient

class Chapter4Runner:
    """
    Handles generating the audit plan for Chapter 4 "Erstellung eines Prüfplans".
    It processes each subchapter as a separate, parallel planning task.
    """
    STAGE_NAME = "Chapter-4"

    def __init__(self, config: AppConfig, ai_client: AiClient):
        self.config = config
        self.ai_client = ai_client
        self.subchapter_definitions = self._load_subchapter_definitions()
        logging.info(f"Initialized runner for stage: {self.STAGE_NAME}")

    def _load_asset_text(self, path: str) -> str:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()

    def _load_asset_json(self, path: str) -> dict:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _load_subchapter_definitions(self) -> Dict[str, Any]:
        """
        Loads definitions for all Chapter 4 subchapters. The logic for which
        Baustein selection to run is now based on the AUDIT_TYPE.
        """
        logging.info(f"Loading Chapter 4 definitions for audit type: {self.config.audit_type}")
        
        definitions = {
            # Placeholder for 4.1.4, currently deterministic
            "auswahlStandorte": {
                "key": "4.1.4",
                "type": "deterministic",
                "table": {
                    "rows": [{"Standort": "Hauptstandort", "Erst- bzw. Rezertifizierung": "Ja", "1. Überwachungsaudit": "Ja", "2. Überwachungsaudit": "Ja", "Begründung für die Auswahl": "Zentraler Standort mit kritischer Infrastruktur."}]
                }
            },
            # This is now a fully functional AI-driven task.
            "auswahlMassnahmenAusRisikoanalyse": {
                "key": "4.1.5",
                "type": "ai_driven",
                "prompt_path": "assets/prompts/stage_4_1_5_auswahl_massnahmen_risiko.txt",
                "schema_path": "assets/schemas/stage_4_1_5_auswahl_massnahmen_risiko_schema.json"
            }
        }

        if self.config.audit_type == "Zertifizierungsaudit":
            definitions["auswahlBausteineErstRezertifizierung"] = {
                "key": "4.1.1",
                "type": "ai_driven",
                "prompt_path": "assets/prompts/stage_4_1_1_auswahl_bausteine_erst.txt",
                "schema_path": "assets/schemas/stage_4_1_1_auswahl_bausteine_erst_schema.json"
            }
        elif self.config.audit_type == "Überwachungsaudit":
            # In a real scenario, we might have different logic for 1st and 2nd surveillance audit
            definitions["auswahlBausteine1Ueberwachungsaudit"] = {
                "key": "4.1.2",
                "type": "ai_driven",
                "prompt_path": "assets/prompts/stage_4_1_2_auswahl_bausteine_ueberwachung.txt",
                "schema_path": "assets/schemas/stage_4_1_2_auswahl_bausteine_ueberwachung_schema.json"
            }
        else:
            logging.warning(f"Unknown audit type '{self.config.audit_type}'. No Baustein selection definitions loaded.")
            
        return definitions

    async def _process_single_subchapter(self, name: str, definition: dict) -> Dict[str, Any]:
        """Generates planning content for a single subchapter, supporting AI and deterministic modes."""
        logging.info(f"Starting planning for subchapter: {definition['key']} ({name})")
        
        if definition.get("type") == "deterministic":
            logging.info(f"Processing '{name}' deterministically.")
            # For deterministic sections, just return the predefined table rows.
            return {name: {"table": definition["table"]}}

        # Default to AI-driven
        prompt_template = self._load_asset_text(definition["prompt_path"])
        schema = self._load_asset_json(definition["schema_path"])
        
        try:
            generated_rows = await self.ai_client.generate_json_response(prompt_template, schema)
            logging.info(f"Successfully generated plan for subchapter {definition['key']}")
            return {name: {"table": generated_rows}}
        except Exception as e:
            logging.error(f"Failed to generate plan for subchapter {definition['key']}: {e}", exc_info=True)
            return {name: {"table": {"rows": []}}} # Return empty structure on failure

    async def run(self) -> dict:
        """
        Executes the planning logic for all of Chapter 4 in parallel.
        """
        logging.info(f"Executing stage: {self.STAGE_NAME}")

        if not self.subchapter_definitions:
            logging.warning(f"No subchapter definitions found. Skipping Chapter 4.")
            return {}

        tasks = [self._process_single_subchapter(name, definition) for name, definition in self.subchapter_definitions.items()]
        results_list = await asyncio.gather(*tasks)

        aggregated_results = {}
        for res_dict in results_list:
            aggregated_results.update(res_dict)
            
        logging.info(f"Successfully aggregated planning results for stage {self.STAGE_NAME}")
        return aggregated_results