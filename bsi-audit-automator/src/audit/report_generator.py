# src/audit/report_generator.py
import logging
import json
from google.cloud.exceptions import NotFound

from src.config import AppConfig
from src.clients.gcs_client import GcsClient

class ReportGenerator:
    """Assembles the final audit report from individual stage stubs."""
    MASTER_TEMPLATE_PATH = "assets/schemas/master_report_template.json"
    STAGES_TO_AGGREGATE = ["Chapter-1"]

    def __init__(self, config: AppConfig, gcs_client: GcsClient):
        self.config = config
        self.gcs_client = gcs_client
        logging.info("Report Generator initialized.")

    def assemble_report(self):
        """Loads template, populates it with GCS stubs, and saves the final report."""
        with open(self.MASTER_TEMPLATE_PATH, 'r', encoding='utf-8') as f:
            report = json.load(f)
        
        report['customer'] = self.config.customer_id
        report['auditType'] = self.config.audit_type
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

    def _populate_report(self, report: dict, stage_name: str, stage_data: dict):
        """Deterministically maps data from a stage stub into the report object."""
        logging.info(f"Populating report with data from stage: {stage_name}")
        if stage_name == "Chapter-1":
            if 'chapter_1_2' in stage_data:
                report['chapters']['1']['subchapters']['1.2']['content'] = stage_data['chapter_1_2']['content']
            if 'chapter_1_4' in stage_data:
                report['chapters']['1']['subchapters']['1.4']['content'] = stage_data['chapter_1_4']['content']
            if 'chapter_1_5' in stage_data:
                report['chapters']['1']['subchapters']['1.5']['content'] = stage_data['chapter_1_5']['content']
        else:
            logging.warning(f"No population logic defined for stage: {stage_name}")