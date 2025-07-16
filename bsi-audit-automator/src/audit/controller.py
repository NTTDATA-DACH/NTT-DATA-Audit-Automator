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
from src.clients.document_ai_client import DocumentAiClient
from src.clients.rag_client import RagClient
from src.audit.stages.stage_previous_report_scan import PreviousReportScanner
from src.audit.stages.stage_1_general import Chapter1Runner
from src.audit.stages.stage_3_dokumentenpruefung import Chapter3Runner
from src.audit.stages.stage_4_pruefplan import Chapter4Runner
from src.audit.stages.stage_5_vor_ort_audit import Chapter5Runner
from src.audit.stages.stage_7_anhang import Chapter7Runner
from src.audit.stages.stage_gs_check_extraction import GrundschutzCheckExtractionRunner

class AuditController:
    """Orchestrates the entire staged audit process with lazy initialization of runners."""

    def __init__(self, config: AppConfig, gcs_client: GcsClient, ai_client: AiClient, rag_client: RagClient):
        self.config = config
        self.gcs_client = gcs_client
        self.ai_client = ai_client
        self.rag_client = rag_client
        self.all_findings: List[Dict[str, Any]] = []
        self.finding_counters = defaultdict(int)

        self.stage_runner_classes = {
            "Scan-Report": PreviousReportScanner,
            "Grundschutz-Check-Extraction": GrundschutzCheckExtractionRunner,
            "Chapter-1": Chapter1Runner,
            "Chapter-3": Chapter3Runner,
            "Chapter-4": Chapter4Runner,
            "Chapter-5": Chapter5Runner,
            "Chapter-7": Chapter7Runner,
        }
        # This defines the exact order of dependencies for each runner's constructor.
        self.runner_dependencies = {
            "Scan-Report": (self.config, self.ai_client, self.rag_client),
            "Grundschutz-Check-Extraction": (self.config, self.gcs_client, None, self.ai_client, self.rag_client), # Placeholder for doc_ai_client
            "Chapter-1": (self.config, self.ai_client, self.rag_client),
            "Chapter-3": (self.config, self.gcs_client, self.ai_client, self.rag_client),
            "Chapter-4": (self.config, self.gcs_client, self.ai_client),
            "Chapter-5": (self.config, self.gcs_client, self.ai_client),
            "Chapter-7": (self.config, self.gcs_client),
        }
        logging.info("Audit Controller initialized with lazy stage loading and findings collector.")

    def _parse_finding_id(self, finding_id: str) -> Tuple[Optional[str], int]:
        """Parses a finding ID like 'AG-12' into its category 'AG' and number 12."""
        if not finding_id or '-' not in finding_id:
            return None, 0
        parts = finding_id.split('-')
        category = parts[0]
        try:
            num = int(parts[-1])
            return category, num
        except (ValueError, IndexError):
            return None, 0

    def _process_previous_findings(self, previous_findings: List[Dict[str, Any]]):
        """Processes findings from a previous report scan, preserving their IDs and updating counters."""
        logging.info(f"Processing {len(previous_findings)} findings from previous audit report.")
        for finding in previous_findings:
            finding_id = finding.get("nummer")
            if not finding_id:
                continue

            category, num = self._parse_finding_id(finding_id)
            if category and num > 0:
                # Update the counter to the highest number seen for this category
                self.finding_counters[category] = max(self.finding_counters[category], num)

            # Add the finding to the central list with its ID and details preserved
            self.all_findings.append({
                "id": finding_id,
                "category": finding.get("category"),
                "description": finding.get("beschreibung", "No description provided."),
                "source_chapter": f"Previous Audit ({finding.get('quelle', 'N/A')})",
                "status": finding.get("status"),
                "behebungsfrist": finding.get("behebungsfrist")
            })

    def _process_new_finding(self, finding: Dict[str, Any], stage_name: str):
        """Processes a newly generated finding, adding it to the central list to await ID assignment."""
        source_ref = stage_name.replace('Chapter-', '')
        # Add to central list without an ID, which will be assigned at the end.
        self.all_findings.append({
            "category": finding.get("category"),
            "description": finding.get("description"),
            "source_chapter": source_ref
        })
        logging.info(f"Collected new finding from {stage_name}: {finding.get('category')}")

    def _extract_findings_recursive(self, data: Any) -> List[Dict[str, Any]]:
        """
        Recursively traverses a data structure to find all structured `finding` objects.
        Returns a flat list of all findings discovered. This method does NOT handle
        the `all_findings` key from Scan-Report, as that is handled separately.
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

        # Special handling for Scan-Report which has a flat list of previous findings
        if stage_name == "Scan-Report" and 'all_findings' in result_data:
            self._process_previous_findings(result_data['all_findings'])
            # We don't do a recursive search for this stage type to avoid double counting
            return

        # For the extraction stage, there are no findings to process.
        if stage_name == "Grundschutz-Check-Extraction":
            return

        # Standard recursive search for newly generated findings
        newly_discovered_findings = self._extract_findings_recursive(result_data)
        for finding in newly_discovered_findings:
            self._process_new_finding(finding, stage_name)

    def _save_all_findings(self) -> None:
        """
        Saves the centrally collected list of all findings. It preserves existing IDs
        from previous reports and assigns new, sequential IDs for new findings.
        """
        if not self.all_findings:
            logging.info("No findings were collected during the audit. Skipping save.")
            return

        findings_with_ids = []
        for finding in self.all_findings:
            if 'id' in finding and finding['id']:
                # This is a finding from a previous report, ID is already set.
                findings_with_ids.append(finding)
            else:
                # This is a new finding, assign a new ID.
                category = finding['category']
                self.finding_counters[category] += 1
                finding_id = f"{category}-{self.finding_counters[category]}"
                
                # Add the new ID to the finding object
                finding_with_id = {"id": finding_id, **finding}
                findings_with_ids.append(finding_with_id)

        findings_path = f"{self.config.output_prefix}results/all_findings.json"
        self.gcs_client.upload_from_string(
            content=json.dumps(findings_with_ids, indent=2, ensure_ascii=False),
            destination_blob_name=findings_path
        )
        logging.info(f"Successfully saved {len(findings_with_ids)} findings with sequential IDs to {findings_path}")

    async def run_all_stages(self, force_overwrite: bool = False) -> None:
        """
        Runs all defined audit stages in a dependency-aware order.
        """
        # Step 0: Run the critical pre-processing step first.
        logging.info("Step 0: Running pre-processing stage 'Grundschutz-Check-Extraction'...")
        await self.run_single_stage("Grundschutz-Check-Extraction", force_overwrite=force_overwrite)
        logging.info("Completed pre-processing.")

        # Step 1: Run initial independent stages in parallel. Chapter-3 now depends on Step 0.
        initial_parallel_stages = ["Scan-Report", "Chapter-1", "Chapter-3", "Chapter-7"]
        logging.info(f"Step 1: Starting parallel execution for initial stages: {initial_parallel_stages}")
        await asyncio.gather(
            *(self.run_single_stage(stage_name, force_overwrite=force_overwrite) for stage_name in initial_parallel_stages)
        )
        logging.info("Completed initial parallel stages.")

        # Step 2: Run Chapter 4, which depends on Chapter 3's ground-truth map.
        logging.info("Step 2: Running stage Chapter-4...")
        await self.run_single_stage("Chapter-4", force_overwrite=force_overwrite)
        logging.info("Completed stage Chapter-4.")

        # Step 3: Run Chapter 5, which depends on Chapter 4's plan and Chapter 3's data.
        logging.info("Step 3: Running stage Chapter-5...")
        await self.run_single_stage("Chapter-5", force_overwrite=force_overwrite)
        logging.info("Completed stage Chapter-5.")
        
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
        
        if not force_overwrite:
            try:
                # The extraction stage does not produce a reportable JSON, its output is the intermediate file.
                # So we check for the intermediate file's existence to determine if we can skip.
                if stage_name == "Grundschutz-Check-Extraction":
                    if self.gcs_client.blob_exists(GrundschutzCheckExtractionRunner.INTERMEDIATE_CHECK_RESULTS_PATH) and \
                       self.gcs_client.blob_exists(GrundschutzCheckExtractionRunner.GROUND_TRUTH_MAP_PATH):
                        logging.info(f"Stage '{stage_name}' already completed (intermediate files exist). Skipping.")
                        return {"status": "skipped", "reason": "intermediate files found"}
                else:
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
        
        # --- DYNAMIC DEPENDENCY INJECTION ---
        # Instantiate DocumentAiClient only if needed for the specific stage
        if stage_name == "Grundschutz-Check-Extraction":
            doc_ai_client = DocumentAiClient(self.config, self.gcs_client)
            dependencies = (self.config, self.gcs_client, doc_ai_client, self.ai_client, self.rag_client)
        else:
            dependencies = self.runner_dependencies[stage_name]
        stage_runner = runner_class(*dependencies)
        logging.info(f"Initialized runner for stage: {stage_name}")

        try:
            # Pass the force_overwrite flag down to the runner.
            result_data = await stage_runner.run(force_overwrite=force_overwrite)

            # The extraction stage does not produce a reportable result, so we don't save a stage JSON.
            if stage_name != "Grundschutz-Check-Extraction":
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