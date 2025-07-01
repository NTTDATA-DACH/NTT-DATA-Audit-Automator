# src/audit/report_generator.py
import logging
import json
from google.cloud.exceptions import NotFound

from src.config import AppConfig
from src.clients.gcs_client import GcsClient

class ReportGenerator:
    """Assembles the final audit report from individual stage stubs."""
    MASTER_TEMPLATE_PATH = "assets/schemas/master_report_template.json"
    STAGES_TO_AGGREGATE = ["Chapter-1", "Chapter-3"]

    def __init__(self, config: AppConfig, gcs_client: GcsClient):
        self.config = config
        self.gcs_client = gcs_client
        logging.info("Report Generator initialized.")

    def assemble_report(self):
        """Loads template, populates it with GCS stubs, and saves the final report."""
        with open(self.MASTER_TEMPLATE_PATH, 'r', encoding='utf-8') as f:
            report = json.load(f)
        
        # Populate top-level info
        report['bsiAuditReport']['titlePage']['auditedInstitution'] = self.config.customer_id
        # This is a simplification; a real app might map this differently.
        report['bsiAuditReport']['allgemeines']['audittyp']['content'] = self.config.audit_type

        logging.info("Master report template loaded.")

        for stage_name in self.STAGES_TO_AGGREGATE:
            stage_output_path = f"{self.config.output_prefix}results/{stage_name}.json"
            try:
                stage_data = self.gcs_client.read_json(stage_output_path)
                self._populate_report(report, stage_name, stage_data)
            except NotFound:
                logging.error(f"Result for required stage '{stage_name}' not found. Report will be incomplete.")
                continue
        
        final_report_path = f"{self.config.output_prefix}final_audit_report.json"
        self.gcs_client.upload_from_string(
            content=json.dumps(report, indent=2, ensure_ascii=False),
            destination_blob_name=final_report_path
        )
        logging.info(f"Final report saved to: gs://{self.config.bucket_name}/{final_report_path}")

    def _populate_chapter_1(self, report: dict, stage_data: dict):
        """Populates Chapter 1 data from the 'Chapter-1' stage stub."""
        # Note: Chapter 1 is not implemented based on the new template.
        # This function is a placeholder for if we re-implement it.
        logging.warning("Population logic for Chapter 1 is not implemented for the new template.")
        pass

    def _populate_chapter_3(self, report: dict, stage_data: dict):
        """Populates Chapter 3 data from the 'Chapter-3' stage stub."""
        chapter_3_target = report['bsiAuditReport']['dokumentenpruefung']

        for subchapter_key, result in stage_data.items():
            if result is None:
                logging.warning(f"Skipping population for failed subchapter: {subchapter_key}")
                continue

            target_section = None
            if subchapter_key == "definitionDesInformationsverbundes":
                target_section = chapter_3_target.get("strukturanalyseA1", {}).get(subchapter_key)
            else:
                target_section = chapter_3_target.get(subchapter_key)
                
            if not target_section:
                logging.warning(f"Could not find target section for '{subchapter_key}' in report template.")
                continue
            
            # Populate answers and finding
            content_list = target_section.get("content", [])
            answers = result.get("answers", [])
            
            # Find the finding object and update it
            for item in content_list:
                if item.get("type") == "finding":
                    item["findingText"] = result.get("findingText")
                    break
            
            # Populate answers in order
            answer_idx = 0
            for item in content_list:
                if item.get("type") == "question":
                    if answer_idx < len(answers):
                        item["answer"] = answers[answer_idx]
                        answer_idx += 1
                    else:
                        logging.warning(f"Not enough answers in result for questions in '{subchapter_key}'")
                        break

    def _populate_report(self, report: dict, stage_name: str, stage_data: dict):
        """Deterministically maps data from a stage stub into the report object."""
        logging.info(f"Populating report with data from stage: {stage_name}")
        if stage_name == "Chapter-1":
            # The old Chapter-1 structure doesn't match the new template.
            # We skip it for now.
            self._populate_chapter_1(report, stage_data)
        elif stage_name == "Chapter-3":
            self._populate_chapter_3(report, stage_data)
        else:
            logging.warning(f"No population logic defined for stage: {stage_name}")