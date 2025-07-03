# src/audit/report_generator.py
import logging
import json
from google.cloud.exceptions import NotFound

from src.config import AppConfig
from src.clients.gcs_client import GcsClient

class ReportGenerator:
    """Assembles the final audit report from individual stage stubs."""
    LOCAL_MASTER_TEMPLATE_PATH = "assets/json/master_report_template.json"
    STAGES_TO_AGGREGATE = ["Chapter-1", "Chapter-3", "Chapter-4", "Chapter-5", "Chapter-7"]

    def __init__(self, config: AppConfig, gcs_client: GcsClient):
        self.config = config
        self.gcs_client = gcs_client
        self.gcs_report_path = "report/master_report_template.json"
        logging.info("Report Generator initialized.")

    def _initialize_report_on_gcs(self) -> dict:
        try:
            report = self.gcs_client.read_json(self.gcs_report_path)
            logging.info(f"Loaded existing report template from GCS: {self.gcs_report_path}")
            return report
        except NotFound:
            logging.info("No report template found on GCS. Initializing from local asset.")
            with open(self.LOCAL_MASTER_TEMPLATE_PATH, 'r', encoding='utf-8') as f:
                report = json.load(f)
            
            # Use a generic placeholder, as customer_id is no longer a direct config
            report['bsiAuditReport']['titlePage']['auditedInstitution'] = "Audited Institution"
            report['bsiAuditReport']['allgemeines']['audittyp']['content'] = self.config.audit_type
            
            self.gcs_client.upload_from_string(
                content=json.dumps(report, indent=2, ensure_ascii=False),
                destination_blob_name=self.gcs_report_path
            )
            logging.info(f"Saved initial report template to GCS: {self.gcs_report_path}")
            return report

    def _populate_chapter_7_findings(self, report: dict):
        """Populates the findings tables in Chapter 7.2 from the central findings file."""
        logging.info("Populating Chapter 7.2 with collected findings...")
        findings_path = f"{self.config.output_prefix}results/all_findings.json"
        try:
            all_findings = self.gcs_client.read_json(findings_path)
        except NotFound:
            logging.warning("Central findings file not found. Chapter 7.2 will be empty.")
            return

        # Get references to the target tables in the report template
        findings_section = report['bsiAuditReport']['anhang']['abweichungenUndEmpfehlungen']
        ag_table = findings_section['geringfuegigeAbweichungen']['table']['rows']
        as_table = findings_section['schwerwiegendeAbweichungen']['table']['rows']
        e_table = findings_section['empfehlungen']['table']['rows']

        # Clear existing dummy rows if any
        ag_table.clear()
        as_table.clear()
        e_table.clear()

        for finding in all_findings:
            if finding['category'] == 'AG':
                ag_table.append({
                    "Nr.": finding['id'],
                    "Beschreibung der Abweichung": finding['description'],
                    "Quelle (Kapitel)": finding['source_chapter']
                })
            elif finding['category'] == 'AS':
                as_table.append({
                    "Nr.": finding['id'],
                    "Beschreibung der Abweichung": finding['description'],
                    "Quelle (Kapitel)": finding['source_chapter']
                })
            elif finding['category'] == 'E':
                e_table.append({
                    "Nr.": finding['id'],
                    "Beschreibung der Empfehlung": finding['description'],
                    "Quelle (Kapitel)": finding['source_chapter']
                })
        
        logging.info(f"Populated Chapter 7.2 with {len(all_findings)} total findings.")

    def assemble_report(self):
        report = self._initialize_report_on_gcs()

        for stage_name in self.STAGES_TO_AGGREGATE:
            stage_output_path = f"{self.config.output_prefix}results/{stage_name}.json"
            try:
                stage_data = self.gcs_client.read_json(stage_output_path)
                self._populate_report(report, stage_name, stage_data)
            except NotFound:
                logging.warning(f"Result for stage '{stage_name}' not found. Skipping population.")
                continue
        
        # Populate the findings after all other stages are done
        self._populate_chapter_7_findings(report)

        final_report_path = f"{self.config.output_prefix}final_audit_report.json"
        self.gcs_client.upload_from_string(
            content=json.dumps(report, indent=2, ensure_ascii=False),
            destination_blob_name=final_report_path
        )
        logging.info(f"Final report assembled and saved to: gs://{self.config.bucket_name}/{final_report_path}")
        self.gcs_client.upload_from_string(
            content=json.dumps(report, indent=2, ensure_ascii=False),
            destination_blob_name=self.gcs_report_path
        )
        logging.info(f"Updated master report state on GCS: gs://{self.config.bucket_name}/{self.gcs_report_path}")

    def _populate_chapter_3(self, report: dict, stage_data: dict):
        chapter_3_target = report['bsiAuditReport']['dokumentenpruefung']
        for subchapter_key, result in stage_data.items():
            if result is None: continue
            target_section = chapter_3_target.get("strukturanalyseA1", {}).get(subchapter_key) if subchapter_key == "definitionDesInformationsverbundes" else chapter_3_target.get(subchapter_key)
            if not target_section:
                logging.warning(f"Could not find target section for '{subchapter_key}' in report template."); continue
            
            if 'finding' in result and isinstance(result.get('finding'), dict):
                finding = result['finding']
                finding_text = f"[{finding.get('category')}] {finding.get('description')}"
                for item in target_section.get("content", []):
                    if item.get("type") == "finding": item["findingText"] = finding_text; break
            
            answers = result.get("answers", [])
            answer_idx = 0
            for item in target_section.get("content", []):
                if item.get("type") == "question":
                    if answer_idx < len(answers): item["answer"] = answers[answer_idx]; answer_idx += 1
                    else: logging.warning(f"Not enough answers in result for questions in '{subchapter_key}'"); break

    def _populate_chapter_4(self, report: dict, stage_data: dict):
        chapter_4_target = report['bsiAuditReport']['erstellungEinesPruefplans']['auditplanung']
        ch4_plan_key = next(iter(stage_data)) if stage_data else None
        result = stage_data.get(ch4_plan_key, {})
        if not result or 'rows' not in result: return

        target_key_map = {"auswahlBausteineUeberwachung": "auswahlBausteineErstRezertifizierung"}
        target_key = target_key_map.get(ch4_plan_key, ch4_plan_key)

        if target_key in chapter_4_target:
            chapter_4_target[target_key]['rows'] = result['rows']
        else:
            logging.warning(f"Could not find target section for '{ch4_plan_key}' (mapped to '{target_key}') in Chapter 4.")

    def _populate_chapter_5(self, report: dict, stage_data: dict):
        chapter_5_target = report['bsiAuditReport']['vorOrtAudit']
        for subchapter_key, result in stage_data.items():
            if result is None: continue
            if subchapter_key == "verifikationDesITGrundschutzChecks":
                target_section = chapter_5_target.get(subchapter_key, {}).get("einzelergebnisse")
                if target_section: target_section["bausteinPruefungen"] = result.get("bausteinPruefungen", [])
                else: logging.warning(f"Could not find target section for '{subchapter_key}'")
            # Other subchapters in Ch5 are now manual, so no population logic needed.

    def _populate_chapter_7(self, report: dict, stage_data: dict):
        anhang_target = report['bsiAuditReport']['anhang']
        if 'referenzdokumente' in stage_data and 'table' in stage_data['referenzdokumente']:
            anhang_target['referenzdokumente']['table']['rows'] = stage_data['referenzdokumente']['table']['rows']

    def _populate_report(self, report: dict, stage_name: str, stage_data: dict):
        logging.info(f"Populating report with data from stage: {stage_name}")
        if stage_name == "Chapter-1":
            logging.warning("Population logic for Chapter 1 is not fully implemented for the new template.")
        elif stage_name == "Chapter-3":
            self._populate_chapter_3(report, stage_data)
        elif stage_name == "Chapter-4":
            self._populate_chapter_4(report, stage_data)
        elif stage_name == "Chapter-5":
            self._populate_chapter_5(report, stage_data)
        elif stage_name == "Chapter-7":
            self._populate_chapter_7(report, stage_data)
        else:
            logging.warning(f"No population logic defined for stage: {stage_name}")