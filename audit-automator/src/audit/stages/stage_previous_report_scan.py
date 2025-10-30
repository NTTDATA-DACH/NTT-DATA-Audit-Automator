# src/audit/stages/stage_previous_report_scan.py
import logging
import json
import asyncio
from typing import Dict, Any

from src.config import AppConfig
from src.clients.ai_client import AiClient
from src.clients.rag_client import RagClient
from src.constants import PROMPT_CONFIG_PATH

class PreviousReportScanner:
    """
    A dedicated stage to scan a previous audit report and extract key data.
    It runs three extraction tasks in parallel for maximum efficiency.
    """
    STAGE_NAME = "Scan-Report"

    def __init__(self, config: AppConfig, ai_client: AiClient, rag_client: RagClient):
        self.config = config
        self.ai_client = ai_client
        self.rag_client = rag_client
        self.prompt_config = self._load_asset_json(PROMPT_CONFIG_PATH)
        logging.info(f"Initialized runner for stage: {self.STAGE_NAME}")

    def _load_asset_json(self, path: str) -> dict:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    async def _run_extraction_task(self, task_name: str, gcs_uri: str) -> Dict[str, Any]:
        """
        Runs a single AI extraction task for a part of the report.
        
        Args:
            task_name: The key from the prompt_config (e.g., 'extract_chapter_1').
            gcs_uri: The GCS URI of the previous audit report.

        Returns:
            A dictionary containing the extracted data for that task.
        """
        logging.info(f"Starting extraction for task: {task_name}")
        try:
            task_config = self.prompt_config["stages"][self.STAGE_NAME][task_name]
            prompt = task_config["prompt"]
            schema = self._load_asset_json(task_config["schema_path"])
            
            response = await self.ai_client.generate_json_response(
                prompt=prompt,
                json_schema=schema,
                gcs_uris=[gcs_uri],
                request_context_log=f"{self.STAGE_NAME}: {task_name}"
            )
            return response
        except Exception as e:
            logging.error(f"Extraction task '{task_name}' failed: {e}", exc_info=True)
            return {task_name: {"error": str(e)}} # Return error structure

    async def run(self, force_overwrite: bool = False) -> dict:
        """
        Executes the logic for scanning the previous audit report.
        """
        logging.info(f"Executing stage: {self.STAGE_NAME}")
        
        # 1. Find the previous audit report document
        report_uris = self.rag_client.get_gcs_uris_for_categories(["Vorheriger-Auditbericht"])
        if not report_uris:
            logging.warning("No document with category 'Vorheriger-Auditbericht' found. Skipping stage.")
            return {"status": "skipped", "reason": "No previous audit report found."}
        
        # Use the first report found if multiple are classified
        report_uri = report_uris[0]
        logging.info(f"Found previous audit report to scan: {report_uri}")

        # 2. Define and run all extraction tasks in parallel
        extraction_tasks = ["extract_chapter_1", "extract_chapter_4", "extract_chapter_7"]
        coroutines = [self._run_extraction_task(task_name, report_uri) for task_name in extraction_tasks]
        
        results_list = await asyncio.gather(*coroutines)

        # 3. Aggregate results into a single dictionary
        final_result = {}
        for result in results_list:
            final_result.update(result)
            
        logging.info(f"Successfully completed all extractions for stage {self.STAGE_NAME}")
        return final_result