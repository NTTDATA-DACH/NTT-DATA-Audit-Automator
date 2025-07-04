# src/audit/controller.py
import logging
import json
import asyncio
from typing import List, Dict, Any
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
        self.all_findings: List[Dict[str, Any]] = []

        self.stage_runner_classes = {
            "Chapter-1": Chapter1Runner,
            "Chapter-3": Chapter3Runner,
            "Chapter-4": Chapter4Runner,
            "Chapter-5": Chapter5Runner,
            "Chapter-7": Chapter7Runner,
        }
        self.runner_dependencies = {
            "Chapter-1": (self.config, self.ai_client, self.rag_client),
            "Chapter-3": (self.config, self.ai_client, self.rag_client),
            "Chapter-4": (self.config, self.ai_client),
            "Chapter-5": (self.config, self.gcs_client, self.ai_client),
            "Chapter-7": (self.config, self.gcs_client), # AI Client no longer needed here
        }
        logging.info("Audit Controller initialized with lazy stage loading and findings collector.")

    def _extract_and_store_findings(self, stage_name: str, result_data: Dict[str, Any]) -> None:
        """
        Parses stage results, finds structured `finding` objects, and appends
        any deviations or recommendations to the central findings list.

        Args:
            stage_name: The name of the stage that produced the result (e.g., 'Chapter-3').
            result_data: The JSON-like dictionary returned by the stage runner.
        """
        if not result_data:
            return

        for subchapter_key, subchapter_data in result_data.items():
            if isinstance(subchapter_data, dict) and 'finding' in subchapter_data:
                finding = subchapter_data['finding']
                if finding and finding.get('category') != 'OK':
                    self.all_findings.append({
                        "id": f"{finding['category']}-{len(self.all_findings) + 1}",
                        "category": finding['category'],
                        "description": finding['description'],
                        "source_chapter": stage_name.replace('Chapter-', '') + f" ({subchapter_key})"
                    })
                    logging.info(f"Collected finding from {stage_name}/{subchapter_key}: {finding['category']}")

    def _save_all_findings(self) -> None:
        """
        Saves the centrally collected list of all non-'OK' findings to a 
        dedicated JSON file in GCS for final report assembly.
        """
        if not self.all_findings:
            logging.info("No findings were collected during the audit. Skipping save.")
            return

        findings_path = f"{self.config.output_prefix}results/all_findings.json"
        self.gcs_client.upload_from_string(
            content=json.dumps(self.all_findings, indent=2, ensure_ascii=False),
            destination_blob_name=findings_path
        )
        logging.info(f"Successfully saved {len(self.all_findings)} findings to {findings_path}")

    async def run_all_stages(self) -> None:
        """
        Runs all defined audit stages in sequence, collecting findings after each
        stage. It respects resumability by skipping already completed stages.
        """
        logging.info("Starting to run all audit stages.")
        for stage_name in self.stage_runner_classes.keys():
            await self.run_single_stage(stage_name, force_overwrite=False)
        
        self._save_all_findings()
        logging.info("All audit stages completed.")

    async def run_single_stage(self, stage_name: str, force_overwrite: bool = False) -> Dict[str, Any]:
        """
        Runs a single, specified audit stage and collects findings from its result.

        Args:
            stage_name: The name of the stage to run (e.g., 'Chapter-1').
            force_overwrite: If True, the stage will run even if a result file
                             already exists. If False, it will skip.

        Returns:
            The result data dictionary from the completed stage.
        """
        if stage_name not in self.stage_runner_classes:
            logging.error(f"Unknown stage '{stage_name}'. Available: {list(self.stage_runner_classes.keys())}")
            raise ValueError(f"Unknown stage: {stage_name}")

        stage_output_path = f"{self.config.output_prefix}results/{stage_name}.json"
        
        if not force_overwrite:
            try:
                existing_data = self.gcs_client.read_json(stage_output_path)
                logging.info(f"Stage '{stage_name}' already completed. Skipping generation.")
                self._extract_and_store_findings(stage_name, existing_data)
                return existing_data
            except NotFound:
                logging.info(f"No results for stage '{stage_name}' found. Generating...")
            except Exception as e:
                logging.warning(f"Could not read existing state for stage '{stage_name}': {e}. Proceeding.")
        else:
            logging.info(f"Force overwrite enabled for stage '{stage_name}'. Running generation.")

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
            self._extract_and_store_findings(stage_name, result_data)
            return result_data
        except Exception as e:
            logging.error(f"Stage '{stage_name}' failed: {e}", exc_info=True)
            raise