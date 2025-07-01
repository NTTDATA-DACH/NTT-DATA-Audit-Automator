# src/audit/controller.py
import logging
import json
import asyncio
from google.cloud.exceptions import NotFound

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.ai_client import AiClient
from src.audit.stages.stage_1_general import Chapter1Runner
from src.audit.stages.stage_3_dokumentenpruefung import Chapter3Runner

class AuditController:
    """Orchestrates the entire staged audit process."""

    def __init__(self, config: AppConfig, gcs_client: GcsClient, ai_client: AiClient):
        self.config = config
        self.gcs_client = gcs_client
        self.ai_client = ai_client
        # Define the sequence of all audit stages and their runners
        self.audit_stages = {
            "Chapter-1": Chapter1Runner(config, ai_client),
            "Chapter-3": Chapter3Runner(config, ai_client),
            # Add other stage runners here as they are developed
        }
        logging.info("Audit Controller initialized.")

    async def run_all_stages(self):
        """Runs all defined audit stages in sequence."""
        logging.info("Starting to run all audit stages.")
        for stage_name in self.audit_stages.keys():
            await self.run_single_stage(stage_name)
        logging.info("All audit stages completed.")

    async def run_single_stage(self, stage_name: str):
        """Runs a single, specified audit stage, managing state."""
        if stage_name not in self.audit_stages:
            logging.error(f"Unknown stage '{stage_name}'. Available: {list(self.audit_stages.keys())}")
            raise ValueError(f"Unknown stage: {stage_name}")

        stage_output_path = f"{self.config.output_prefix}results/{stage_name}.json"
        
        try:
            existing_data = self.gcs_client.read_json(stage_output_path)
            logging.info(f"Stage '{stage_name}' already completed. Skipping generation.")
            return existing_data
        except NotFound:
            logging.info(f"No results for stage '{stage_name}' found. Generating...")
        except Exception as e:
            logging.warning(f"Could not read existing state for stage '{stage_name}': {e}. Proceeding with generation.")

        stage_runner = self.audit_stages[stage_name]
        try:
            result_data = await stage_runner.run()
            self.gcs_client.upload_from_string(
                content=json.dumps(result_data, indent=2, ensure_ascii=False),
                destination_blob_name=stage_output_path
            )
            logging.info(f"Successfully saved results for stage '{stage_name}'.")
            return result_data
        except Exception as e:
            logging.error(f"Stage '{stage_name}' failed: {e}", exc_info=True)
            raise