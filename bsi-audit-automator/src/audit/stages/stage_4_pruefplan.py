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
    It processes each subchapter as a separate, parallel planning task based on the central prompt config.
    """
    STAGE_NAME = "Chapter-4"
    PROMPT_CONFIG_PATH = "assets/json/prompt_config.json"


    def __init__(self, config: AppConfig, ai_client: AiClient):
        self.config = config
        self.ai_client = ai_client
        self.prompt_config = self._load_asset_json(self.PROMPT_CONFIG_PATH)
        self.subchapter_definitions = self._load_subchapter_definitions()
        logging.info(f"Initialized runner for stage: {self.STAGE_NAME}")

    def _load_asset_json(self, path: str) -> dict:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _load_subchapter_definitions(self) -> Dict[str, Any]:
        """
        Loads definitions for all Chapter 4 subchapters from the central prompt config.
        The logic for which Baustein selection to run is now based on the AUDIT_TYPE.
        """
        logging.info(f"Loading Chapter 4 definitions for audit type: {self.config.audit_type}")
        definitions = {}
        ch4_config = self.prompt_config["stages"]["Chapter-4"]

        # Deterministic part
        definitions["auswahlStandorte"] = {
            "key": "4.1.4",
            "type": "deterministic",
            "table": {
                "rows": [{"Standort": "Hauptstandort", "Erst- bzw. Rezertifizierung": "Ja", "1. Überwachungsaudit": "Ja", "2. Überwachungsaudit": "Ja", "Begründung für die Auswahl": "Zentraler Standort mit kritischer Infrastruktur."}]
            }
        }
        
        # Load AI-driven definitions based on config and audit type
        if self.config.audit_type == "Zertifizierungsaudit":
            definitions["auswahlBausteineErstRezertifizierung"] = ch4_config["auswahlBausteineErstRezertifizierung"]
        elif self.config.audit_type == "Überwachungsaudit":
            definitions["auswahlBausteine1Ueberwachungsaudit"] = ch4_config["auswahlBausteine1Ueberwachungsaudit"]
        else:
            logging.warning(f"Unknown audit type '{self.config.audit_type}'. No Baustein selection definitions loaded.")
            
        definitions["auswahlMassnahmenAusRisikoanalyse"] = ch4_config["auswahlMassnahmenAusRisikoanalyse"]

        # Mark the type for processing
        for key in definitions:
            if "prompt" in definitions[key]:
                definitions[key]["type"] = "ai_driven"

        return definitions

    async def _process_single_subchapter(self, name: str, definition: dict) -> Dict[str, Any]:
        """Generates planning content for a single subchapter, supporting AI and deterministic modes."""
        logging.info(f"Starting planning for subchapter: {definition.get('key', name)} ({name})")
        
        if definition.get("type") == "deterministic":
            logging.info(f"Processing '{name}' deterministically.")
            return {name: {"table": definition["table"]}}

        # AI-driven
        prompt_template = definition["prompt"]
        schema = self._load_asset_json(definition["schema_path"])
        
        try:
            generated_data = await self.ai_client.generate_json_response(
                prompt=prompt_template,
                json_schema=schema,
                request_context_log=f"Chapter-4: {name}"
            )
            logging.info(f"Successfully generated plan for subchapter {definition.get('key', name)}")
            # The AI response is the table content itself, but the final report expects it nested.
            return {name: {"table": generated_data}}
        except Exception as e:
            logging.error(f"Failed to generate plan for subchapter {definition.get('key', name)}: {e}", exc_info=True)
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