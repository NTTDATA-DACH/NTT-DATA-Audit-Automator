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
        """Loads definitions for subchapters to be processed."""
        # This mapping connects the key in the master template to the specific asset files.
        definitions = {
            "auswahlBausteineErstRezertifizierung": {
                "key": "4.1.1",
                "prompt_path": "assets/prompts/stage_4_1_1_auswahl_bausteine_erst.txt",
                "schema_path": "assets/schemas/stage_4_1_1_auswahl_bausteine_erst_schema.json"
            },
            # Add other subchapters like 4.1.2, 4.1.3 etc. here when prompts are ready
        }
        return definitions

    async def _process_single_subchapter(self, name: str, definition: dict) -> Dict[str, Any]:
        """Generates planning content for a single subchapter."""
        logging.info(f"Starting planning for subchapter: {definition['key']} ({name})")
        
        prompt_template = self._load_asset_text(definition["prompt_path"])
        schema = self._load_asset_json(definition["schema_path"])
        
        prompt = prompt_template.format(customer_id=self.config.customer_id)

        try:
            generated_data = await self.ai_client.generate_json_response(prompt, schema)
            logging.info(f"Successfully generated plan for subchapter {definition['key']}")
            return {name: generated_data}
        except Exception as e:
            logging.error(f"Failed to generate plan for subchapter {definition['key']}: {e}", exc_info=True)
            return {name: None}

    async def run(self) -> dict:
        """
        Executes the planning logic for all of Chapter 4 in parallel.
        
        Returns:
            A dictionary aggregating the results of all subchapter plans.
        """
        logging.info(f"Executing stage: {self.STAGE_NAME}")

        tasks = []
        for name, definition in self.subchapter_definitions.items():
            tasks.append(self._process_single_subchapter(name, definition))
        
        results_list = await asyncio.gather(*tasks)

        aggregated_results = {}
        for res_dict in results_list:
            aggregated_results.update(res_dict)
            
        logging.info(f"Successfully aggregated planning results for stage {self.STAGE_NAME}")
        return aggregated_results