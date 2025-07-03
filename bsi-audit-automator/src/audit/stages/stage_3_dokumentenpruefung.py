# src/audit/stages/stage_3_dokumentenpruefung.py
import logging
import json
import asyncio
from typing import Dict, Any

from src.config import AppConfig
from src.clients.ai_client import AiClient
from src.clients.rag_client import RagClient

class Chapter3Runner:
    """
    Handles generating content for Chapter 3 "Dokumentenprüfung".
    It processes each subchapter as a separate, parallel task.
    """
    STAGE_NAME = "Chapter-3"
    MASTER_TEMPLATE_PATH = "assets/schemas/master_report_template.json"
    
    def __init__(self, config: AppConfig, ai_client: AiClient, rag_client: RagClient):
        self.config = config
        self.ai_client = ai_client
        self.rag_client = rag_client # Store the RAG client for future use
        self.subchapter_definitions = self._load_subchapter_definitions()
        logging.info(f"Initialized runner for stage: {self.STAGE_NAME}")

    def _load_asset_text(self, path: str) -> str:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()

    def _load_asset_json(self, path: str) -> dict:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _load_subchapter_definitions(self) -> Dict[str, Any]:
        """Loads definitions for subchapters to be processed from the master template."""
        # This mapping connects the key in the master template to the specific asset files.
        # This makes the runner highly configurable.
        definitions = {
            "aktualitaetDerReferenzdokumente": {
                "key": "3.1",
                "prompt_path": "assets/prompts/stage_3_1_aktualitaet.txt",
                "schema_path": "assets/schemas/stage_3_1_aktualitaet_schema.json"
            },
            "sicherheitsleitlinieUndRichtlinienInA0": {
                "key": "3.2",
                "prompt_path": "assets/prompts/stage_3_2_sicherheitsleitlinie.txt",
                "schema_path": "assets/schemas/stage_3_2_sicherheitsleitlinie_schema.json"
            },
            "definitionDesInformationsverbundes": {
                "key": "3.3.1",
                "prompt_path": "assets/prompts/stage_3_3_1_informationsverbund.txt",
                "schema_path": "assets/schemas/stage_3_3_1_informationsverbund_schema.json"
            },
            # NOTE: We skip subchapters that we don't have prompts for yet.
            # "bereinigterNetzplan": {...},
            # "listeDerGeschaeftsprozesse": {...},
            "ergebnisDerDokumentenpruefung": {
                "key": "3.9",
                "prompt_path": "assets/prompts/stage_3_9_ergebnis.txt",
                "schema_path": "assets/schemas/stage_3_9_ergebnis_schema.json"
            }
        }
        return definitions

    async def _process_single_subchapter(self, name: str, definition: dict) -> Dict[str, Any]:
        """Generates content for a single subchapter."""
        logging.info(f"Starting generation for subchapter: {definition['key']} ({name})")
        
        prompt_template = self._load_asset_text(definition["prompt_path"])
        schema = self._load_asset_json(definition["schema_path"])

        # In a real RAG scenario, context would be fetched and added here.
        # For now, the prompt is static.
        prompt = prompt_template

        try:
            generated_data = await self.ai_client.generate_json_response(prompt, schema)
            logging.info(f"Successfully generated data for subchapter {definition['key']}")
            # We return a dict with the name so we can aggregate results easily
            return {name: generated_data}
        except Exception as e:
            logging.error(f"Failed to generate data for subchapter {definition['key']}: {e}", exc_info=True)
            return {name: None} # Return None on failure to not break the whole stage

    async def run(self) -> dict:
        """
        Executes the generation logic for all of Chapter 3 in parallel.
        
        Returns:
            A dictionary aggregating the results of all subchapter generations.
        """
        logging.info(f"Executing stage: {self.STAGE_NAME}")

        tasks = []
        for name, definition in self.subchapter_definitions.items():
            tasks.append(self._process_single_subchapter(name, definition))
        
        # Run all subchapter generation tasks concurrently
        results_list = await asyncio.gather(*tasks)

        # Aggregate results from the list of dicts into a single dict
        aggregated_results = {}
        for res_dict in results_list:
            aggregated_results.update(res_dict)
            
        logging.info(f"Successfully aggregated results for all of stage {self.STAGE_NAME}")
        return aggregated_results