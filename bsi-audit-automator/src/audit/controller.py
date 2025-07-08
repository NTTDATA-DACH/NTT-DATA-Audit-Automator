# src/audit/controller.py
import logging
import json
import asyncio
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict
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
        # Chapter 4 no longer needs rag_client
        self.runner_dependencies = {
            "Chapter-1": (self.config, self.ai_client, self.rag_client),
            "Chapter-3": (self.config, self.ai_client, self.rag_client),
            "Chapter-4": (self.config, self.ai_client),
            "Chapter-5": (self.config, self.gcs_client, self.ai_client),
            "Chapter-7": (self.config, self.gcs_client),
        }
        logging.info("Audit Controller initialized with lazy stage loading and findings collector.")

    def _collect_finding(self, finding: Dict[str, Any], stage_name: str) -> None:
        """Collects a raw finding object, adding source context before appending."""
        source_ref = stage_name.replace('Chapter-', '')
        self.all_findings.append({
            # ID is NOT assigned here. It will be assigned sequentially at the end.
            "category": finding['category'],
            "description": finding['description'],
            "source_chapter": source_ref
        })
        logging.info(f"Collected finding from {stage_name}: {finding['category']}")

    def _extract_findings_recursive(self, data: Any) -> List[Dict[str, Any]]:
        """
        Recursively traverses a data structure to find all structured `finding` objects.
        Returns a flat list of all findings discovered.
        """
        found = []
        if isinstance(data, dict):
            if 'finding' in data and isinstance(data['finding'], dict):
                finding_obj = data['finding']
                if finding_obj and finding_obj.get('category') != 'OK':
                    found.append(finding_obj)
            
            for value in data.values():
                found.extend(self._extract_findings_recursive(value))
        
        elif isinstance(data, list):
            for item in data:
                found.extend(self._extract_findings_recursive(item))
        
        return found

    def _extract_and_store_findings(self, stage_name: str, result_data: Dict[str, Any]) -> None:
        """
        Parses stage results, finds all structured `finding` objects recursively,
        and adds them to the central collection.
        """
        if not result_data:
            return

        discovered_findings = self._extract_findings_recursive(result_data)
        for finding in discovered_findings:
            self._collect_finding(finding, stage_name)

    def _save_all_findings(self) -> None:
        """
        Saves the centrally collected list of all non-'OK' findings. It assigns
        a sequential, report-friendly ID to each finding just before saving.
        """
        if not self.all_findings:
            logging.info("No findings were collected during the audit. Skipping save.")
            return

        # Assign sequential, category-based IDs now that all findings are collected.
        findings_with_ids = []
        counters = defaultdict(int)
        for finding in self.all_findings:
            category = finding['category']
            counters[category] += 1
            finding_id = f"{category}-{counters[category]}"
            
            # Create a new dict with the ID and the rest of the finding data
            findings_with_ids.append({"id": finding_id, **finding})

        findings_path = f"{self.config.output_prefix}results/all_findings.json"
        self.gcs_client.upload_from_string(
            content=json.dumps(findings_with_ids, indent=2, ensure_ascii=False),
            destination_blob_name=findings_path
        )
        logging.info(f"Successfully saved {len(findings_with_ids)} findings with sequential IDs to {findings_path}")

    async def run_all_stages(self, force_overwrite: bool = False) -> None:
        """
        Runs all defined audit stages using a dependency-aware parallel execution flow.
        Independent stages are run concurrently, and dependent stages run sequentially after.
        """
        # Define stages that can run in parallel (no inter-dependencies)
        parallel_stages = ["Chapter-1", "Chapter-3", "Chapter-4"]
        # Define stages that must run sequentially after the parallel group
        sequential_stages = ["Chapter-5", "Chapter-7"]

        logging.info(f"Starting parallel execution for independent stages: {parallel_stages}")
        parallel_tasks = [
            self.run_single_stage(stage_name, force_overwrite=force_overwrite)
            for stage_name in parallel_stages
        ]
        await asyncio.gather(*parallel_tasks)
        logging.info("Completed parallel execution of independent stages.")

        logging.info(f"Starting sequential execution for dependent stages: {sequential_stages}")
        for stage_name in sequential_stages:
            await self.run_single_stage(stage_name, force_overwrite=force_overwrite)
        logging.info("Completed sequential execution of dependent stages.")
        
        self._save_all_findings()
        logging.info("All audit stages completed.")

    async def run_single_stage(self, stage_name: str, force_overwrite: bool = False) -> Dict[str, Any]:
        """
        Runs a single, specified audit stage and collects findings from its result.
        """
        if stage_name not in self.stage_runner_classes:
            logging.error(f"Unknown stage '{stage_name}'. Available: {list(self.stage_runner_classes.keys())}")
            raise ValueError(f"Unknown stage: {stage_name}")

        stage_output_path = f"{self.config.output_prefix}results/{stage_name}.json"
        
        # For full runs, we check for existing results unless `force` is specified.
        # For single runs (`--run-stage`), force_overwrite is True by default.
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