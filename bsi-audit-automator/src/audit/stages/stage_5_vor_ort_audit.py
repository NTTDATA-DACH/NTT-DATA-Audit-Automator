# src/audit/stages/stage_5_vor_ort_audit.py
import logging
import json
import asyncio
from typing import Dict, Any, List

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.ai_client import AiClient
from src.audit.stages.control_catalog import ControlCatalog

class Chapter5Runner:
    """
    Handles generating content for Chapter 5 "Vor-Ort-Audit".
    Processes each subchapter as a separate, parallel task.
    """
    STAGE_NAME = "Chapter-5"

    def __init__(self, config: AppConfig, gcs_client: GcsClient, ai_client: AiClient):
        self.config = config
        self.gcs_client = gcs_client
        self.ai_client = ai_client
        self.control_catalog = ControlCatalog() # Initialize the BSI catalog helper
        self.subchapter_definitions = self._load_subchapter_definitions()
        logging.info(f"Initialized runner for stage: {self.STAGE_NAME}")

    def _load_asset_text(self, path: str) -> str:
        with open(path, 'r', encoding='utf-8') as f: return f.read()

    def _load_asset_json(self, path: str) -> dict:
        with open(path, 'r', encoding='utf-8') as f: return json.load(f)

    def _load_subchapter_definitions(self) -> Dict[str, Any]:
        return {
            "wirksamkeitDesSicherheitsmanagementsystems": {
                "key": "5.1",
                "prompt_path": "assets/prompts/stage_5_1_wirksamkeit.txt",
                "schema_path": "assets/schemas/stage_5_1_wirksamkeit_schema.json"
            },
            "verifikationDesITGrundschutzChecks": { # This is for 5.5.2
                "key": "5.5.2",
                "prompt_path": "assets/prompts/stage_5_5_2_einzelergebnisse.txt",
                "schema_path": "assets/schemas/stage_5_5_2_einzelergebnisse_schema.json"
            },
            # NOTE: Skipping 5.5.3 and 5.6.1 as per instructions
            # NOTE: Skipping 5.6.2 (risk measures) for now as it needs a separate implementation
        }

    async def _process_control_verification(self, chapter_4_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handles the special RAG logic for 5.5.2."""
        definition = self.subchapter_definitions["verifikationDesITGrundschutzChecks"]
        name = "verifikationDesITGrundschutzChecks"
        logging.info(f"Starting special control verification for subchapter: {definition['key']} ({name})")

        # 1. Get selected Bausteine from Chapter 4 results
        selected_bausteine = chapter_4_data.get("auswahlBausteineErstRezertifizierung", {}).get("rows", [])
        if not selected_bausteine:
            logging.warning("No Bausteine found in Chapter 4 results. Skipping 5.5.2.")
            return {name: {"bausteinPruefungen": []}}

        # 2. Get all controls for these Bausteine from our catalog
        all_controls_to_verify = []
        for baustein in selected_bausteine:
            # Assuming the 'Baustein' field contains the ID, e.g., "ISMS.1 Sicherheitsmanagement"
            baustein_id = baustein.get("Baustein").split(" ")[0] # Extract "ISMS.1"
            controls = self.control_catalog.get_controls_for_baustein_id(baustein_id)
            for control in controls:
                all_controls_to_verify.append({
                    "id": control.get("id"),
                    "title": control.get("title"),
                    "baustein": baustein.get("Baustein"),
                    "zielobjekt": baustein.get("Zielobjekt")
                })
        
        # 3. Create the prompt
        prompt_template = self._load_asset_text(definition["prompt_path"])
        schema = self._load_asset_json(definition["schema_path"])
        
        # Format the list of controls for the prompt
        controls_text = "\n".join([f"- {c['id']} {c['title']} (Zielobjekt: {c['zielobjekt']})" for c in all_controls_to_verify])
        prompt = prompt_template.format(controls_to_verify=controls_text)

        try:
            generated_data = await self.ai_client.generate_json_response(prompt, schema)
            logging.info(f"Successfully generated verification data for subchapter {definition['key']}")
            return {name: generated_data}
        except Exception as e:
            logging.error(f"Failed to generate verification data for subchapter {definition['key']}: {e}", exc_info=True)
            return {name: None}

    async def _process_generic_subchapter(self, name: str, definition: dict) -> Dict[str, Any]:
        """Generates content for a standard subchapter."""
        logging.info(f"Starting generation for subchapter: {definition['key']} ({name})")
        prompt = self._load_asset_text(definition["prompt_path"])
        schema = self._load_asset_json(definition["schema_path"])
        try:
            generated_data = await self.ai_client.generate_json_response(prompt, schema)
            logging.info(f"Successfully generated data for subchapter {definition['key']}")
            return {name: generated_data}
        except Exception as e:
            logging.error(f"Failed to generate data for subchapter {definition['key']}: {e}", exc_info=True)
            return {name: None}

    async def run(self) -> dict:
        """Executes the generation logic for all of Chapter 5."""
        logging.info(f"Executing stage: {self.STAGE_NAME}")
        
        # Load Chapter 4 results as they are a dependency
        try:
            ch4_results_path = f"{self.config.output_prefix}results/Chapter-4.json"
            chapter_4_data = self.gcs_client.read_json(ch4_results_path)
            logging.info("Successfully loaded dependency: Chapter 4 results.")
        except Exception as e:
            logging.error(f"Could not load Chapter 4 results, which are required for Chapter 5. Aborting stage. Error: {e}")
            raise
            
        tasks = []
        for name, definition in self.subchapter_definitions.items():
            if name == "verifikationDesITGrundschutzChecks":
                tasks.append(self._process_control_verification(chapter_4_data))
            else:
                tasks.append(self._process_generic_subchapter(name, definition))
        
        results_list = await asyncio.gather(*tasks)
        aggregated_results = {}
        for res_dict in results_list:
            aggregated_results.update(res_dict)
            
        logging.info(f"Successfully aggregated results for all of stage {self.STAGE_NAME}")
        return aggregated_results