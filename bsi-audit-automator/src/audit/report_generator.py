# src/audit/report_generator.py
import logging
import json
from google.cloud.exceptions import NotFound
from typing import Dict, Any

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
        """
        Loads the report template. It first tries to load from a working copy on GCS,
        falling back to the local `master_report_template.json` if it doesn't exist.
        """
        try:
            report = self.gcs_client.read_json(self.gcs_report_path)
            logging.info(f"Loaded existing report template from GCS: {self.gcs_report_path}")
            return report
        except NotFound:
            logging.info("No report template found on GCS. Initializing from local asset.")
            with open(self.LOCAL_MASTER_TEMPLATE_PATH, 'r', encoding='utf-8') as f:
                report = json.load(f)
            
            # Pre-populate with basic info
            report['bsiAuditReport']['titlePage']['auditedInstitution'] = "Audited Institution"
            audittyp_section = report.get('bsiAuditReport', {}).get('allgemeines', {}).get('audittyp', {})
            if audittyp_section:
                audittyp_section['content'] = self.config.audit_type
            
            self.gcs_client.upload_from_string(
                content=json.dumps(report, indent=2, ensure_ascii=False),
                destination_blob_name=self.gcs_report_path
            )
            logging.info(f"Saved initial report template to GCS: {self.gcs_report_path}")
            return report

    def _populate_chapter_1(self, report: dict, stage_data: dict) -> None:
        """Populates the 'Allgemeines' (Chapter 1) of the report defensively."""
        target_chapter = report.get('bsiAuditReport', {}).get('allgemeines')
        if not target_chapter:
            logging.error("Report template is missing 'bsiAuditReport.allgemeines' structure. Cannot populate Chapter 1.")
            return

        # Populate Informationsverbund (1.4) and Geltungsbereich (1.2) from the same AI result
        geltungsbereich_data = stage_data.get('geltungsbereichDerZertifizierung', {}) # This is the key used in stage_1_runner
        target_section_gelt = target_chapter.get('geltungsbereichDerZertifizierung')
        target_section_info = target_chapter.get('informationsverbund')

        # Populate Geltungsbereich with the main text
        if target_section_gelt and isinstance(geltungsbereich_data, dict):
            # Main description text goes into Geltungsbereich
            final_text = geltungsbereich_data.get('description', '')
            if isinstance(geltungsbereich_data.get('finding'), dict):
                finding = geltungsbereich_data['finding']
                if finding.get('category') != 'OK':
                    final_text += f"\n\nFeststellung: [{finding.get('category')}] {finding.get('description')}"
            
            # Defensively ensure the structure exists before writing to it
            if 'content' not in target_section_gelt or not isinstance(target_section_gelt.get('content'), list):
                target_section_gelt['content'] = []
            if not target_section_gelt['content']:
                target_section_gelt['content'].append({"type": "prose", "text": ""})
            target_section_gelt['content'][0]['text'] = final_text
        else:
            logging.warning("Could not populate 'geltungsbereichDerZertifizierung' due to missing key in stage data or report template.")

        # Populate Informationsverbund with its specific fields from the same AI result
        if target_section_info and isinstance(geltungsbereich_data, dict) and isinstance(target_section_info.get('content'), list):
            target_section_info['content'][0]['text'] = geltungsbereich_data.get('kurzbezeichnung', '')
            target_section_info['content'][1]['text'] = geltungsbereich_data.get('kurzbeschreibung', '')
        else:
             logging.warning("Could not populate 'informationsverbund' due to missing key in stage data or report template.")

        # Populate Audittyp (1.3)
        # The key in stage_1_general is 'audittyp' and it holds a simple string.
        target_section_typ = target_chapter.get('audittyp')
        if target_section_typ and 'content' in target_section_typ:
            target_section_typ['content'] = stage_data.get('audittyp', {}).get('content', self.config.audit_type)
        else:
            logging.warning("Could not populate 'audittyp' due to missing key in stage data or report template.")


    def _populate_chapter_7_findings(self, report: dict) -> None:
        """Populates the findings tables in Chapter 7.2 from the central findings file."""
        logging.info("Populating Chapter 7.2 with collected findings...")
        findings_path = f"{self.config.output_prefix}results/all_findings.json"
        try:
            all_findings = self.gcs_client.read_json(findings_path)
        except NotFound:
            logging.warning("Central findings file not found. Chapter 7.2 will be empty.")
            return

        findings_section = report.get('bsiAuditReport', {}).get('anhang', {}).get('abweichungenUndEmpfehlungen')
        if not findings_section:
            logging.warning("Report template is missing '...anhang.abweichungenUndEmpfehlungen'. Skipping findings population.")
            return

        ag_table = findings_section.get('geringfuegigeAbweichungen', {}).get('table', {}).get('rows')
        as_table = findings_section.get('schwerwiegendeAbweichungen', {}).get('table', {}).get('rows')
        e_table = findings_section.get('empfehlungen', {}).get('table', {}).get('rows')

        if not all(isinstance(table, list) for table in [ag_table, as_table, e_table]):
            logging.warning("One or more findings tables are missing the 'rows' list in the report template. Skipping population.")
            return

        ag_table.clear(); as_table.clear(); e_table.clear()

        for finding in all_findings:
            category = finding.get('category')
            base_row_data = {
                "Nr.": finding.get('id', f"{category}-?"),
                "Quelle (Kapitel)": finding.get('source_chapter', 'N/A'),
                "Behebungsfrist": "30 Tage nach Audit",  # Placeholder
                "Status": "Offen"  # Default
            }

            if category == 'AG':
                row_data = base_row_data.copy()
                row_data["Beschreibung der Abweichung"] = finding.get('description', 'N/A')
                ag_table.append(row_data)
            elif category == 'AS':
                row_data = base_row_data.copy()
                row_data["Beschreibung der Abweichung"] = finding.get('description', 'N/A')
                as_table.append(row_data)
            elif category == 'E':
                row_data = base_row_data.copy()
                row_data["Beschreibung der Empfehlung"] = finding.get('description', 'N/A')
                # Recommendations don't have a strict deadline or status in the same way
                row_data["Behebungsfrist"] = "N/A"
                row_data["Status"] = "Zur Umsetzung empfohlen"
                e_table.append(row_data)
        
        logging.info(f"Populated Chapter 7.2 with {len(all_findings)} total findings.")

    def assemble_report(self) -> None:
        """
        Main method to assemble the final report. It loads all stage results
        and collected findings, populates a master template, and saves the final
        output to GCS.
        """
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

    def _populate_chapter_3(self, report: dict, stage_data: dict) -> None:
        """Populates Chapter 3 (Dokumentenprüfung) content into the report."""
        chapter_3_target = report.get('bsiAuditReport', {}).get('dokumentenpruefung')
        if not chapter_3_target:
            logging.error("Report template is missing 'bsiAuditReport.dokumentenpruefung' structure. Cannot populate Chapter 3.")
            return

        # Mapping from stage_data keys to their location in the report template
        key_to_path_map = {
            "aktualitaetDerReferenzdokumente": ["aktualitaetDerReferenzdokumente"],
            "sicherheitsleitlinieUndRichtlinienInA0": ["sicherheitsleitlinieUndRichtlinienInA0"],
            "definitionDesInformationsverbundes": ["strukturanalyseA1", "definitionDesInformationsverbundes"],
            "bereinigterNetzplan": ["strukturanalyseA1", "bereinigterNetzplan"],
            "listeDerGeschaeftsprozesse": ["strukturanalyseA1", "listeDerGeschaeftsprozesse"],
            "definitionDerSchutzbedarfskategorien": ["schutzbedarfsfeststellungA2", "definitionDerSchutzbedarfskategorien"],
            "modellierungsdetails": ["modellierungDesInformationsverbundesA3", "modellierungsdetails"],
            "ergebnisDerModellierung": ["modellierungDesInformationsverbundesA3", "ergebnisDerModellierung"],
            "detailsZumItGrundschutzCheck": ["itGrundschutzCheckA4", "detailsZumItGrundschutzCheck"],
            "ergebnisDerDokumentenpruefung": ["ergebnisDerDokumentenpruefung"],
        }

        for subchapter_key, result in stage_data.items():
            if not isinstance(result, dict): continue
            path_keys = key_to_path_map.get(subchapter_key)
            if not path_keys: continue

            target_section = chapter_3_target
            for key in path_keys:
                target_section = target_section.get(key, {})

            if not target_section:
                logging.warning(f"Could not find target section for '{subchapter_key}' in report template."); continue
            
            # Populate finding text
            if 'finding' in result and isinstance(result.get('finding'), dict):
                finding = result['finding']
                finding_text = f"[{finding.get('category')}] {finding.get('description')}"
                for item in target_section.get("content", []):
                    if item.get("type") == "finding":
                        item["findingText"] = finding_text
                        break
            
            # Populate answers for questions or text for prose
            if "answers" in result:
                answers = result.get("answers", [])
                answer_idx = 0
                for item in target_section.get("content", []):
                    if item.get("type") == "question":
                        if answer_idx < len(answers): item["answer"] = answers[answer_idx]; answer_idx += 1
                        else: logging.warning(f"Not enough answers in result for questions in '{subchapter_key}'"); break
            elif "votum" in result:
                for item in target_section.get("content", []):
                    if item.get("type") == "prose": item["text"] = result.get("votum", ""); break

    def _populate_chapter_4(self, report: dict, stage_data: dict) -> None:
        """Populates Chapter 4 (Prüfplan) content into the report."""
        chapter_4_target = report.get('bsiAuditReport', {}).get('erstellungEinesPruefplans', {}).get('auditplanung')
        if not chapter_4_target:
            logging.error("Report template is missing '...erstellungEinesPruefplans.auditplanung' structure. Cannot populate Chapter 4.")
            return
            
        ch4_plan_key = next(iter(stage_data)) if stage_data else None
        if not ch4_plan_key: return

        result = stage_data.get(ch4_plan_key, {})
        target_key_map = {"auswahlBausteineUeberwachung": "auswahlBausteineErstRezertifizierung"}
        target_key = target_key_map.get(ch4_plan_key, ch4_plan_key)

        target_section = chapter_4_target.get(target_key)
        if isinstance(target_section, dict):
            target_section['rows'] = result.get('rows', [])
        else:
            logging.warning(f"Could not find or invalid target section for '{ch4_plan_key}' (mapped to '{target_key}') in Chapter 4.")

    def _populate_chapter_5(self, report: dict, stage_data: dict) -> None:
        """Populates Chapter 5 (Vor-Ort-Audit) content into the report."""
        chapter_5_target = report.get('bsiAuditReport', {}).get('vorOrtAudit')
        if not chapter_5_target:
            logging.error("Report template is missing 'bsiAuditReport.vorOrtAudit' structure. Cannot populate Chapter 5.")
            return

        for subchapter_key, result in stage_data.items():
            if not isinstance(result, dict): continue

            if subchapter_key == "verifikationDesITGrundschutzChecks":
                target_section_wrapper = chapter_5_target.get(subchapter_key, {})
                target_section = target_section_wrapper.get("einzelergebnisse")
                if isinstance(target_section, dict):
                    target_section["bausteinPruefungen"] = result.get("bausteinPruefungen", [])
                else:
                    logging.warning(f"Could not find target structure 'einzelergebnisse' for '{subchapter_key}'")
            
            elif subchapter_key == "einzelergebnisseDerRisikoanalyse":
                target_section_wrapper = chapter_5_target.get("risikoanalyseA5", {})
                target_section = target_section_wrapper.get(subchapter_key)
                if isinstance(target_section, dict):
                    target_section["massnahmenPruefungen"] = result.get("massnahmenPruefungen", [])
                else: logging.warning(f"Could not find target structure for '{subchapter_key}'")

    def _populate_chapter_7(self, report: dict, stage_data: dict) -> None:
        """Populates Chapter 7 (Anhang) content into the report."""
        anhang_target = report.get('bsiAuditReport', {}).get('anhang')
        if not anhang_target:
            logging.error("Report template is missing 'bsiAuditReport.anhang' structure. Cannot populate Chapter 7.")
            return

        # Populate reference documents table
        ref_docs_data = stage_data.get('referenzdokumente', {})
        target_section = anhang_target.get('referenzdokumente')
        if isinstance(target_section, dict) and isinstance(ref_docs_data.get('table'), dict):
            target_table = target_section.get('table')
            if isinstance(target_table, dict):
                target_table['rows'] = ref_docs_data['table'].get('rows', [])
            else:
                 logging.warning("Could not populate 'referenzdokumente' because target 'table' is not a dict.")
        else:
            logging.warning("Could not populate 'referenzdokumente' due to missing or invalid structure.")

    def _populate_report(self, report: dict, stage_name: str, stage_data: dict) -> None:
        """Router function to call the correct population logic for a given stage."""
        logging.info(f"Populating report with data from stage: {stage_name}")
        population_map = {
            "Chapter-1": self._populate_chapter_1,
            "Chapter-3": self._populate_chapter_3,
            "Chapter-4": self._populate_chapter_4,
            "Chapter-5": self._populate_chapter_5,
            "Chapter-7": self._populate_chapter_7,
        }
        
        populate_func = population_map.get(stage_name)
        if populate_func:
            populate_func(report, stage_data)
        else:
            logging.warning(f"No population logic defined for stage: {stage_name}")