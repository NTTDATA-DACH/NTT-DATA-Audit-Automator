# src/audit/controller.py
import logging
import json
import asyncio
import uuid
from typing import List, Dict, Any, Optional
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

    def _append_finding(self, finding: Dict[str, Any], source_chapter: str, source_subchapter_key: Optional[str] = None) -> None:
        """Appends a single structured finding to the central list with a unique ID."""
        # Use a short UUID for the ID to prevent collisions in parallel environments.
        finding_id = f"{finding['category']}-{uuid.uuid4().hex[:12]}"
        
        # Construct a more detailed source reference
        source_ref = source_chapter.replace('Chapter-', '')
        if source_subchapter_key:
            source_ref += f" ({source_subchapter_key})"
            
        self.all_findings.append({
            "id": finding_id,
            "category": finding['category'],
            "description": finding['description'],
            "source_chapter": source_ref
        })
        logging.info(f"Collected finding from {source_ref}: {finding['category']}")

    def _extract_findings_recursive(self, data: Any, stage_name: str) -> None:
        """
        Recursively traverses a nested data structure (dicts and lists) to find
        and store all structured `finding` objects. This is more robust than a
        flat search.
        """
        if isinstance(data, dict):
            # Check if the current dictionary is a finding object itself
            if 'finding' in data and isinstance(data['finding'], dict):
                finding = data['finding']
                if finding and finding.get('category') != 'OK':
                    # Try to find a subchapter key for better context
                    subchapter_key = next((k for k, v in data.items() if isinstance(v, dict) and 'finding' in v), None)
                    self._append_finding(finding, stage_name, subchapter_key)
            
            # Recurse into the values of the dictionary
            for key, value in data.items():
                self._extract_findings_recursive(value, stage_name)

        elif isinstance(data, list):
            # Recurse into each item in the list
            for item in data:
                self._extract_findings_recursive(item, stage_name)

    def _extract_and_store_findings(self, stage_name: str, result_data: Dict[str, Any]) -> None:
        """
        Public entry point to parse stage results for findings. It uses a recursive
        helper to ensure all nested findings are discovered.

        Args:
            stage_name: The name of the stage that produced the result (e.g., 'Chapter-3').
            result_data: The JSON-like dictionary returned by the stage runner.
        """
        if not result_data:
            return
        self._extract_findings_recursive(result_data, stage_name)


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

    async def run_all_stages(self, force_overwrite: bool = False) -> None:
        """
        Runs all defined audit stages in parallel, collecting findings after each
        stage. It respects resumability by skipping already completed stages.

        Args:
            force_overwrite: If True, all stages will be re-run even if results exist.
        """
        logging.info("Starting to run all audit stages in parallel...")
        
        # Create a list of tasks to run concurrently.
        tasks = [
            self.run_single_stage(stage_name, force_overwrite=force_overwrite)
            for stage_name in self.stage_runner_classes.keys()
        ]
        
        # Execute all stages concurrently and wait for them to complete.
        await asyncio.gather(*tasks)
        
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