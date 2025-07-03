# src/audit/controller.py
import logging
import json
import asyncio
from google.cloud.exceptions import NotFound

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.ai_client import AiClient
from src.clients.rag_client import RagClient
from src.audit.stages.stage_1_general import Chapter1Runner
from src.audit.stages.stage_3_dokumentenpruefung import Chapter3Runner
from src.audit.stages.stage_4_pruefplan import Chapter4Runner
from src.audit.stages.stage_5_vor_ort_audit import Chapter5Runner
from src.audit.stages.stage_7_anhang import Chapter7Runner

class AuditController:
    """Orchestrates the entire staged audit process with lazy initialization of runners."""

    def __init__(self, config: AppConfig, gcs_client: GcsClient, ai_client: AiClient, rag_client: RagClient):
        self.config = config
        self.gcs_client = gcs_client
        self.ai_client = ai_client
        self.rag_client = rag_client
        
        # This map now stores the class definitions, not instances.
        self.stage_runner_classes = {
            "Chapter-1": Chapter1Runner,
            "Chapter-3": Chapter3Runner,
            "Chapter-4": Chapter4Runner,
            "Chapter-5": Chapter5Runner,
            "Chapter-7": Chapter7Runner,
        }
        # A map of arguments needed by each runner's constructor.
        self.runner_dependencies = {
            "Chapter-1": (self.config, self.ai_client, self.rag_client),
            "Chapter-3": (self.config, self.ai_client, self.rag_client),
            "Chapter-4": (self.config, self.ai_client),
            "Chapter-5": (self.config, self.gcs_client, self.ai_client),
            "Chapter-7": (self.config, self.gcs_client, self.ai_client),
        }
        logging.info("Audit Controller initialized with lazy stage loading.")

    async def run_all_stages(self):
        """Runs all defined audit stages in sequence, with resumability."""
        logging.info("Starting to run all audit stages.")
        for stage_name in self.stage_runner_classes.keys():
            # For a full run, we want to skip completed stages, so force_overwrite is False.
            await self.run_single_stage(stage_name, force_overwrite=False)
        logging.info("All audit stages completed.")

    async def run_single_stage(self, stage_name: str, force_overwrite: bool = False):
        """
        Runs a single, specified audit stage.

        Args:
            stage_name: The name of the stage to run (e.g., "Chapter-1").
            force_overwrite: If True, the stage will run even if a result file exists.
                             If False, it will skip if a result file is found.
        """
        if stage_name not in self.stage_runner_classes:
            logging.error(f"Unknown stage '{stage_name}'. Available: {list(self.stage_runner_classes.keys())}")
            raise ValueError(f"Unknown stage: {stage_name}")

        stage_output_path = f"{self.config.output_prefix}results/{stage_name}.json"
        
        # The check for existing results is now conditional.
        if not force_overwrite:
            try:
                existing_data = self.gcs_client.read_json(stage_output_path)
                logging.info(f"Stage '{stage_name}' already completed. Skipping generation.")
                return existing_data
            except NotFound:
                logging.info(f"No results for stage '{stage_name}' found. Generating...")
            except Exception as e:
                logging.warning(f"Could not read existing state for stage '{stage_name}': {e}. Proceeding with generation.")
        else:
            logging.info(f"Force overwrite enabled for stage '{stage_name}'. Running generation regardless of existing files.")

        # LAZY INITIALIZATION: Create the runner instance just-in-time.
        runner_class = self.stage_runner_classes[stage_name]
        dependencies = self.runner_dependencies[stage_name]
        stage_runner = runner_class(*dependencies)
        logging.info(f"Initialized runner for stage: {stage_name}")

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