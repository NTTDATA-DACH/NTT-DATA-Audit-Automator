# src/audit/controller.py
import logging
import json
import asyncio
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict
from google.cloud.exceptions import NotFound

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.ai_client import AiClient, current_stage
from src.clients.document_ai_client import DocumentAiClient
from src.clients.rag_client import RagClient
from src.constants import STAGE_RESULTS_PATH, ALL_FINDINGS_PATH, EXTRACTED_CHECK_DATA_PATH, GROUND_TRUTH_MAP_PATH, CHECKER_LOG_PATH
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
        # Stages run concurrently; the findings list and the checker log are
        # read-modify-written files, so their updates are serialized behind this lock.
        self._state_lock = asyncio.Lock()

        self.stage_runner_classes = {
            "Scan-Report": PreviousReportScanner,
            "Grundschutz-Check-Extraction": GrundschutzCheckExtractionRunner,
            "Chapter-1": Chapter1Runner,
            "Chapter-3": Chapter3Runner,
            "Chapter-4": Chapter4Runner,
            "Chapter-5": Chapter5Runner,
            "Chapter-7": Chapter7Runner,
        }
        # The exact order of dependencies for each runner's constructor. The extraction
        # stage is absent on purpose: its Document AI client is built per run (see
        # run_single_stage), so an entry here would never be reached.
        self.runner_dependencies = {
            "Scan-Report": (self.config, self.ai_client, self.rag_client),
            "Chapter-1": (self.config, self.ai_client, self.rag_client),
            "Chapter-3": (self.config, self.gcs_client, self.ai_client, self.rag_client),
            "Chapter-4": (self.config, self.gcs_client, self.ai_client, self.rag_client),
            "Chapter-5": (self.config, self.gcs_client),
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

    @staticmethod
    def _process_previous_findings(previous_findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Converts findings of a previous report scan, preserving their IDs."""
        logging.info(f"Processing {len(previous_findings)} findings from previous audit report.")
        converted = []
        for finding in previous_findings:
            finding_id = finding.get("nummer")
            if not finding_id:
                continue

            converted.append({
                "id": finding_id,
                "category": finding.get("category"),
                "description": finding.get("beschreibung", "No description provided."),
                "source_chapter": f"Previous Audit ({finding.get('quelle', 'N/A')})",
                "status": finding.get("status"),
                "behebungsfrist": finding.get("behebungsfrist")
            })
        return converted

    @staticmethod
    def _process_new_finding(finding: Dict[str, Any], stage_name: str) -> Dict[str, Any]:
        """Converts a newly generated finding; the ID is assigned when it is persisted."""
        category = finding.get("category")
        if category not in ("AG", "AS", "E"):
            # The ID is built from the category, so a missing one would produce
            # 'None-1' in the report. Fall back to the conservative category the
            # stages already use for degraded AI answers.
            logging.warning(f"Finding from {stage_name} has an unusable category {category!r}; recording it as 'AG'.")
            category = "AG"
        logging.info(f"Collected new finding from {stage_name}: {category}")
        return {
            "category": category,
            "description": finding.get("description"),
            "source_chapter": stage_name.replace('Chapter-', '')
        }

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

    def _collect_stage_findings(self, stage_name: str, result_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Parses stage results and returns all structured `finding` objects found in them.

        The findings are returned rather than accumulated on the controller: stages run
        concurrently and would otherwise overwrite each other's collection.
        """
        if not result_data:
            return []

        # Special handling for Scan-Report which has a flat list of previous findings
        if stage_name == "Scan-Report" and 'all_findings' in result_data:
            # No recursive search for this stage type, to avoid double counting.
            return self._process_previous_findings(result_data['all_findings'])

        # For the extraction stage, there are no findings to process.
        if stage_name == "Grundschutz-Check-Extraction":
            return []

        # Standard recursive search for newly generated findings
        return [
            self._process_new_finding(finding, stage_name)
            for finding in self._extract_findings_recursive(result_data)
        ]

    def _save_stage_findings(self, stage_name: str, stage_findings: List[Dict[str, Any]]) -> None:
        """
        Merges one stage's findings into the central list and persists it.

        Load, merge and save happen here as one step under `_state_lock`, so parallel
        stages cannot interleave: each replaces only its own previous entries, IDs are
        assigned exactly once, and the assigned IDs are part of what is written back
        (so a later save can never renumber them).
        """
        current_findings = []
        try:
            if self.gcs_client.blob_exists(ALL_FINDINGS_PATH):
                current_findings = self.gcs_client.read_json(ALL_FINDINGS_PATH)
        except Exception as e:
            logging.warning(f"Could not load or parse existing findings file: {e}. Starting with an empty list.")

        if stage_name == "Scan-Report":
            kept = [f for f in current_findings if not str(f.get("source_chapter", "")).startswith("Previous Audit")]
        else:
            kept = [f for f in current_findings if f.get("source_chapter") != stage_name.replace('Chapter-', '')]

        counters = defaultdict(int)
        for finding in kept + stage_findings:
            category, num = self._parse_finding_id(finding.get("id"))
            if category and num > 0:
                counters[category] = max(counters[category], num)

        merged = list(kept)
        for finding in stage_findings:
            if finding.get("id"):
                merged.append(finding)  # ID preserved from a previous report
                continue
            category = finding["category"]
            counters[category] += 1
            merged.append({"id": f"{category}-{counters[category]}", **finding})

        if not merged:
            logging.info("No findings were collected during the audit. Skipping save.")
            return

        # Single source of truth: write to the same constant the readers use
        # (run_single_stage and ReportGenerator), so the write/read paths can't drift.
        self.gcs_client.write_json(merged, ALL_FINDINGS_PATH)
        logging.info(
            f"Saved {len(merged)} findings to {ALL_FINDINGS_PATH} "
            f"({len(stage_findings)} from stage '{stage_name}')."
        )

    def _save_checker_log(self, stage_name: str) -> None:
        """Persists the maker/checker verdicts of this stage as the audit's QS trail.

        Only this stage's verdicts are harvested from the shared in-memory log — the
        other stages running in parallel keep theirs until they persist them. Entries of
        the stage being (re-)run replace their predecessors, mirroring how findings are
        handled, so a repeated run does not duplicate the protocol.
        """
        new_entries = [e for e in self.ai_client.checker_log if e.get("stage") == stage_name]
        if not new_entries:
            # A skipped stage ran no AI calls. Rewriting the log here would strip this
            # stage's persisted verdicts from a previous run — the QS trail must survive
            # a no-op re-run.
            return
        self.ai_client.checker_log[:] = [e for e in self.ai_client.checker_log if e.get("stage") != stage_name]

        existing = []
        try:
            if self.gcs_client.blob_exists(CHECKER_LOG_PATH):
                existing = self.gcs_client.read_json(CHECKER_LOG_PATH)
        except Exception as e:
            logging.warning(f"Could not read existing checker log: {e}. Starting a new one.")

        combined = [e for e in existing if e.get("stage") != stage_name] + new_entries

        self.gcs_client.write_json(combined, CHECKER_LOG_PATH)
        corrections = sum(1 for e in new_entries if e.get("korrektur_uebernommen"))
        logging.info(
            f"Checker log for '{stage_name}': {len(new_entries)} verdict(s), "
            f"{corrections} correction(s) applied. Saved to {CHECKER_LOG_PATH}"
        )

    async def _persist_stage_state(self, stage_name: str, stage_findings: List[Dict[str, Any]]) -> None:
        """Persists a stage's findings and checker verdicts as one serialized step.

        Both files are read-modify-written, so concurrent stages must not interleave.
        Failures are logged, never raised: on the error path this runs inside an
        `except` block and must not replace the original stage error.
        """
        async with self._state_lock:
            try:
                self._save_stage_findings(stage_name, stage_findings)
            except Exception as e:
                logging.error(f"Failed to save findings for stage '{stage_name}': {e}", exc_info=True)
            try:
                self._save_checker_log(stage_name)
            except Exception as e:
                logging.error(f"Failed to save the checker log for stage '{stage_name}': {e}", exc_info=True)

    async def run_all_stages(self, force_overwrite: bool = False) -> None:
        """
        Runs all defined audit stages in a dependency-aware order. Each stage run
        will update and persist the central findings list.
        """
        # Step 0: Run the critical pre-processing step first.
        logging.info("Step 0: Running pre-processing stage 'Grundschutz-Check-Extraction'...")
        await self.run_single_stage("Grundschutz-Check-Extraction", force_overwrite=force_overwrite)
        logging.info("Completed pre-processing.")

        # Step 1: Run initial independent stages in parallel.
        initial_parallel_stages = ["Scan-Report", "Chapter-1", "Chapter-3", "Chapter-7"]
        logging.info(f"Step 1: Starting parallel execution for initial stages: {initial_parallel_stages}")
        # Fail-fast (MAX-7): later steps (Chapter-4/5) depend on these stages' persisted
        # output, so if one fails the run cannot produce a valid report. Surface the error
        # immediately rather than masking it and proceeding into dependent stages.
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
        
        logging.info("All audit stages completed.")

    async def run_single_stage(self, stage_name: str, force_overwrite: bool = False) -> Dict[str, Any]:
        """
        Runs a single, specified audit stage: runs it (or skips it if results exist),
        collects the findings it produced, and merges them into the central findings
        list, which replaces only this stage's previous entries.
        """
        if stage_name not in self.stage_runner_classes:
            logging.error(f"Unknown stage '{stage_name}'. Available: {list(self.stage_runner_classes.keys())}")
            raise ValueError(f"Unknown stage: {stage_name}")

        # Tag every AI call this stage makes, so its checker verdicts are attributed to
        # it even while other stages run concurrently on the same client. asyncio.gather
        # gives each stage its own task context, so this does not leak between stages.
        current_stage.set(stage_name)

        # 1. Execute the stage logic
        stage_output_path = STAGE_RESULTS_PATH.format(stage_name=stage_name)
        result_data = None

        if not force_overwrite:
            try:
                if stage_name == "Grundschutz-Check-Extraction":
                    if self.gcs_client.blob_exists(EXTRACTED_CHECK_DATA_PATH) and \
                       self.gcs_client.blob_exists(GROUND_TRUTH_MAP_PATH):
                        logging.info(f"Stage '{stage_name}' already completed (intermediate files exist). Skipping.")
                        result_data = {"status": "skipped", "reason": "intermediate files found"}
                else:
                    result_data = self.gcs_client.read_json(stage_output_path)
                    logging.info(f"Stage '{stage_name}' already completed. Skipping generation.")
            except NotFound:
                logging.info(f"No results for stage '{stage_name}' found. Generating...")
            except Exception as e:
                logging.warning(f"Could not read existing state for stage '{stage_name}': {e}. Proceeding.")

        if result_data is None:
            logging.info(f"Running generation for stage '{stage_name}'.")
            runner_class = self.stage_runner_classes[stage_name]
            
            if stage_name == "Grundschutz-Check-Extraction":
                doc_ai_client = DocumentAiClient(self.config, self.gcs_client)
                dependencies = (self.config, self.gcs_client, doc_ai_client, self.ai_client, self.rag_client)
            else:
                dependencies = self.runner_dependencies[stage_name]
            stage_runner = runner_class(*dependencies)
            logging.info(f"Initialized runner for stage: {stage_name}")

            try:
                result_data = await stage_runner.run(force_overwrite=force_overwrite)

                if stage_name != "Grundschutz-Check-Extraction":
                    self.gcs_client.write_json(result_data, stage_output_path)
                    logging.info(f"Successfully saved results for stage '{stage_name}'.")
            except Exception as e:
                logging.error(f"Stage '{stage_name}' failed: {e}", exc_info=True)
                # Persist what the stage produced before it failed, then re-raise the
                # original error (the saves swallow their own failures on purpose).
                await self._persist_stage_state(stage_name, [])
                raise

        # 2. Collect findings from the result (either newly generated or from the skipped file)
        stage_findings = self._collect_stage_findings(stage_name, result_data)

        # 3. Merge them into the central findings list and persist the maker/checker trail
        await self._persist_stage_state(stage_name, stage_findings)

        return result_data