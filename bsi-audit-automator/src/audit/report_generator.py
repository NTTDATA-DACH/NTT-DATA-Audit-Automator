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
            
            report['bsiAuditReport']['titlePage']['auditedInstitution'] = "Audited Institution"
            report['bsiAuditReport']['allgemeines']['audittyp']['content'] = self.config.audit_type
            
            self.gcs_client.upload_from_string(
                content=json.dumps(report, indent=2, ensure_ascii=False),
                destination_blob_name=self.gcs_report_path
            )
            logging.info(f"Saved initial report template to GCS: {self.gcs_report_path}")
            return report

    def _populate_chapter_1(self, report: dict, stage_data: dict):
        """Populates the 'Allgemeines' chapter of the report defensively."""
        target_chapter = report.get('bsiAuditReport', {}).get('allgemeines')
        if not target_chapter:
            logging.error("Report template is missing 'bsiAuditReport.allgemeines' structure. Cannot populate Chapter 1.")
            return

        # Populate Geltungsbereich (1.2) including its new finding logic
        if 'geltungsbereichDerZertifizierung' in stage_data:
            geltungsbereich_data = stage_data.get('geltungsbereichDerZertifizierung', {})
            final_text = geltungsbereich_data.get('text', '')
            
            if isinstance(geltungsbereich_data.get('finding'), dict):
                finding = geltungsbereich_data['finding']
                if finding.get('category') != 'OK':
                    final_text += f"\n\nFeststellung: [{finding.get('category')}] {finding.get('description')}"
            
            # --- START OF ROBUST FIX (using setdefault) ---
            # Ensure the entire path exists, creating empty structures as needed.
            target_section = target_chapter.setdefault('geltungsbereichDerZertifizierung', {})
            content_list = target_section.setdefault('content', [])
            
            if not content_list:
                content_list.append({"type": "prose", "text": ""})
            
            # Now we can safely write to the path we've guaranteed exists.
            content_list[0]['text'] = final_text
            # --- END OF ROBUST FIX ---

        # Populate Audit-Team (1.4) - now a manual placeholder
        if 'auditTeam' in stage_data:
             text = stage_data.get('auditTeam', {}).get('text', '')
             target_section = target_chapter.get('auditTeam')
             if target_section and isinstance(target_section.get('content'), list) and target_section['content']:
                 target_section['content'][0]['text'] = text
             else:
                logging.warning("Could not populate 'auditTeam' due to missing or invalid structure in report template.")

        # Populate Audittyp (1.3)
        if 'audittyp' in stage_data:
            if 'audittyp' in target_chapter:
                target_chapter['audittyp']['content'] = stage_data.get('audittyp', {}).get('content', '')
            else:
                logging.warning("Key 'audittyp' not found in report template under 'allgemeines'.")

    def _populate_chapter_7_findings(self, report: dict):
        """Populates the findings tables in Chapter 7.2 from the central findings file."""
        logging.info("Populating Chapter 7.2 with collected findings...")
        findings_path = f"{self.config.output_prefix}results/all_findings.json"
        try:
            all_findings = self.gcs_client.read_json(findings_path)
        except NotFound:
            logging.warning("Central findings file not found. Chapter 7.2 will be empty.")
            return

        # Use .get() for safe navigation and provide clear warnings
        findings_section = report.get('bsiAuditReport', {}).get('anhang', {}).get('abweichungenUndEmpfehlungen')
        if not findings_section:
            logging.warning("Report template is missing '...anhang.abweichungenUndEmpfehlungen'. Skipping findings population.")
            return

        ag_table = findings_section.get('geringfuegigeAbweichungen', {}).get('table', {}).get('rows')
        as_table = findings_section.get('schwerwiegendeAbweichungen', {}).get('table', {}).get('rows')
        e_table = findings_section.get('empfehlungen', {}).get('table', {}).get('rows')

        # Check if all targets are valid lists before proceeding
        if not all(isinstance(table, list) for table in [ag_table, as_table, e_table]):
            logging.warning("One or more findings tables are missing the 'rows' list in the report template. Skipping population.")
            return

        ag_table.clear()
        as_table.clear()
        e_table.clear()

        for finding in all_findings:
            if finding.get('category') == 'AG':
                ag_table.append({
                    "Nr.": finding.get('id', 'AG-?'),
                    "Beschreibung der Abweichung": finding.get('description', 'N/A'),
                    "Quelle (Kapitel)": finding.get('source_chapter', 'N/A')
                })
            elif finding.get('category') == 'AS':
                as_table.append({
                    "Nr.": finding.get('id', 'AS-?'),
                    "Beschreibung der Abweichung": finding.get('description', 'N/A'),
                    "Quelle (Kapitel)": finding.get('source_chapter', 'N/A')
                })
            elif finding.get('category') == 'E':
                e_table.append({
                    "Nr.": finding.get('id', 'E-?'),
                    "Beschreibung der Empfehlung": finding.get('description', 'N/A'),
                    "Quelle (Kapitel)": finding.get('source_chapter', 'N/A')
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
        chapter_3_target = report.get('bsiAuditReport', {}).get('dokumentenpruefung')
        if not chapter_3_target:
            logging.error("Report template is missing 'bsiAuditReport.dokumentenpruefung' structure. Cannot populate Chapter 3.")
            return
            
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
        chapter_4_target = report.get('bsiAuditReport', {}).get('erstellungEinesPruefplans', {}).get('auditplanung')
        if not chapter_4_target:
            logging.error("Report template is missing '...erstellungEinesPruefplans.auditplanung' structure. Cannot populate Chapter 4.")
            return
            
        ch4_plan_key = next(iter(stage_data)) if stage_data else None
        result = stage_data.get(ch4_plan_key, {})
        if not result or 'rows' not in result: return

        target_key_map = {"auswahlBausteineUeberwachung": "auswahlBausteineErstRezertifizierung"}
        target_key = target_key_map.get(ch4_plan_key, ch4_plan_key)

        if target_key in chapter_4_target and isinstance(chapter_4_target[target_key], dict):
            chapter_4_target[target_key]['rows'] = result['rows']
        else:
            logging.warning(f"Could not find target section for '{ch4_plan_key}' (mapped to '{target_key}') in Chapter 4.")

    def _populate_chapter_5(self, report: dict, stage_data: dict):
        chapter_5_target = report.get('bsiAuditReport', {}).get('vorOrtAudit')
        if not chapter_5_target:
            logging.error("Report template is missing 'bsiAuditReport.vorOrtAudit' structure. Cannot populate Chapter 5.")
            return

        for subchapter_key, result in stage_data.items():
            if result is None: continue
            if subchapter_key == "verifikationDesITGrundschutzChecks":
                target_section = chapter_5_target.get(subchapter_key, {}).get("einzelergebnisse")
                if target_section and "bausteinPruefungen" in result:
                    target_section["bausteinPruefungen"] = result["bausteinPruefungen"]
                else:
                    logging.warning(f"Could not find target structure for '{subchapter_key}'")

    def _populate_chapter_7(self, report: dict, stage_data: dict):
        anhang_target = report.get('bsiAuditReport', {}).get('anhang')
        if not anhang_target:
            logging.error("Report template is missing 'bsiAuditReport.anhang' structure. Cannot populate Chapter 7.")
            return

        if 'referenzdokumente' in stage_data and 'table' in stage_data.get('referenzdokumente', {}):
            target_section = anhang_target.get('referenzdokumente')
            if target_section and isinstance(target_section.get('table'), dict):
                target_section['table']['rows'] = stage_data['referenzdokumente']['table']['rows']
            else:
                logging.warning("Could not populate 'referenzdokumente' due to missing or invalid structure in report template.")


    def _populate_report(self, report: dict, stage_name: str, stage_data: dict):
        logging.info(f"Populating report with data from stage: {stage_name}")
        if stage_name == "Chapter-1":
            self._populate_chapter_1(report, stage_data)
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