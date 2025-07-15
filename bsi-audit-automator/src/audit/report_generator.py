# src/audit/report_generator.py
import logging
import json
import asyncio
from google.cloud.exceptions import NotFound
from typing import Dict, Any, List
from jsonschema import validate, ValidationError

from datetime import datetime

from src.config import AppConfig
from src.clients.gcs_client import GcsClient

class ReportGenerator:
    """Assembles the final audit report from individual stage stubs."""
    LOCAL_MASTER_TEMPLATE_PATH = "assets/json/master_report_template.json"
    STAGES_TO_AGGREGATE = ["Scan-Report", "Chapter-1", "Chapter-3", "Chapter-4", "Chapter-5", "Chapter-7"]

    def __init__(self, config: AppConfig, gcs_client: GcsClient):
        self.config = config
        self.gcs_client = gcs_client
        self.report_schema = self._load_report_schema()
        logging.info("Report Generator initialized.")
    
    def _load_report_schema(self) -> Dict[str, Any]:
        """Loads the master template to use as a validation schema."""
        try:
            with open(self.LOCAL_MASTER_TEMPLATE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"FATAL: Could not load the master report schema from {self.LOCAL_MASTER_TEMPLATE_PATH}. Error: {e}")
            raise

    def _set_value_by_path(self, report: Dict, path: str, value: Any):
        """
        Safely sets a value in a nested dictionary using a dot-separated path.
        This is more robust than sequential `get` calls.
        """
        keys = path.split('.')
        current_level = report
        for i, key in enumerate(keys[:-1]):
            if not isinstance(current_level, dict):
                logging.warning(f"Path part '{key}' is not a dict in path '{path}'. Cannot set value.")
                return
            if key not in current_level:
                # Create missing dictionary keys if they don't exist
                current_level[key] = {}
            current_level = current_level[key]
        
        if isinstance(current_level, dict):
            current_level[keys[-1]] = value
        else:
            logging.warning(f"Target for path '{path}' is not a dictionary. Cannot set final key '{keys[-1]}'.")

    def _ensure_list_path_exists(self, report: Dict, path: str, min_length: int = 1, default_item: Dict = None) -> List:
        """
        Ensures a list at a given path exists and has a minimum length, padding it if necessary.
        Returns the list object for modification.
        """
        if default_item is None:
            default_item = {"type": "prose", "text": ""}

        keys = path.split('.')
        current_level = report
        for key in keys:
            if not isinstance(current_level, dict):
                logging.warning(f"Path part is not a dict in path '{path}' at key '{key}'. Cannot ensure list path.")
                return []
            if key not in current_level:
                current_level[key] = [] if key == keys[-1] else {}
            current_level = current_level[key]

        if not isinstance(current_level, list):
            logging.warning(f"Target for path '{path}' is not a list. Cannot pad.")
            return []
        
        while len(current_level) < min_length:
            current_level.append(default_item.copy())
            
        return current_level

    def _load_local_report_template(self) -> dict:
        """
        Loads the pristine report template from the local assets folder and
        injects initial configuration like the audit type.
        """
        logging.info(f"Loading pristine report template from local asset: {self.LOCAL_MASTER_TEMPLATE_PATH}")
        try:
            with open(self.LOCAL_MASTER_TEMPLATE_PATH, 'r', encoding='utf-8') as f:
                report = json.load(f)
            
            # Inject dynamic configuration into the fresh template
            self._set_value_by_path(report, 'bsiAuditReport.allgemeines.audittyp.content', self.config.audit_type)
            
            logging.info("Successfully loaded and configured local report template.")
            return report
        except Exception as e:
            logging.error(f"FATAL: Could not load the master report template from {self.LOCAL_MASTER_TEMPLATE_PATH}. Error: {e}")
            raise

    def _populate_chapter_1(self, report: dict, stage_data: dict) -> None:
        """Populates the 'Allgemeines' (Chapter 1) of the report defensively."""
        # Populate 1.4 Informationsverbund
        informationsverbund_data = stage_data.get('informationsverbund', {})
        if informationsverbund_data:
            path_prefix = 'bsiAuditReport.allgemeines.informationsverbund.content'
            content_list = self._ensure_list_path_exists(report, path_prefix, min_length=2)
            if content_list:
                content_list[0]['text'] = informationsverbund_data.get('kurzbezeichnung', '')
                content_list[1]['text'] = informationsverbund_data.get('kurzbeschreibung', '')

        # Populate 1.5 Audittyp
        audittyp_content = stage_data.get('audittyp', {}).get('content', self.config.audit_type)
        self._set_value_by_path(report, 'bsiAuditReport.allgemeines.audittyp.content', audittyp_content)

    async def assemble_report(self) -> None:
        """
        Main method to assemble the final report.
        """
        report = self._load_local_report_template()

        stage_read_tasks = [self.gcs_client.read_json_async(f"{self.config.output_prefix}results/{s}.json") for s in self.STAGES_TO_AGGREGATE]
        stage_results = await asyncio.gather(*stage_read_tasks, return_exceptions=True)
        
        stage_data_map = {}
        for i, result in enumerate(stage_results):
            stage_name = self.STAGES_TO_AGGREGATE[i]
            if isinstance(result, Exception):
                logging.warning(f"Result for stage '{stage_name}' not found or failed to load. Skipping. Error: {result}")
            # Add a check to ensure stage_data is a dictionary before processing.
            elif not result or not isinstance(result, dict):
                logging.warning(f"Result for stage '{stage_name}' is empty or not a dictionary. Skipping.")
            else:
                stage_data_map[stage_name] = result

        # The order of population matters. Populate from Scan-Report first as a baseline.
        if "Scan-Report" in stage_data_map:
            self._populate_from_scan_report(report, stage_data_map["Scan-Report"])

        # Then, let the other stages overwrite with fresher, generated data.
        for stage_name, stage_data in stage_data_map.items():
            if stage_name != "Scan-Report": # Avoid running it twice
                self._populate_report(report, stage_name, stage_data)
        
        # Populate the final aggregated findings last.
        self._populate_chapter_7_findings(report)

        try:
            validate(instance=report, schema=self.report_schema)
            logging.info("Final report successfully validated against the master schema.")
        except ValidationError as e:
            logging.error(f"CRITICAL: Final report failed schema validation. Report will not be saved. Error: {e.message}")
            return

        today = datetime.now()
        date_str = today.strftime("%y%m%d")
        final_report_path = f"{self.config.output_prefix}report-{date_str}.json"
        await self.gcs_client.upload_from_string_async(
            content=json.dumps(report, indent=2, ensure_ascii=False),
            destination_blob_name=final_report_path
        )
        logging.info(f"Final report assembled and saved to: gs://{self.config.bucket_name}/{final_report_path}")
        

    def _populate_chapter_3(self, report: dict, stage_data: dict) -> None:
        """Populates Chapter 3 (Dokumentenprüfung) content into the report."""
        base_path = "bsiAuditReport.dokumentenpruefung"
        key_to_path_map = {
            "aktualitaetDerReferenzdokumente": f"{base_path}.aktualitaetDerReferenzdokumente",
            "sicherheitsleitlinieUndRichtlinienInA0": f"{base_path}.sicherheitsleitlinieUndRichtlinienInA0",
            "definitionDesInformationsverbundes": f"{base_path}.strukturanalyseA1.definitionDesInformationsverbundes",
            "bereinigterNetzplan": f"{base_path}.strukturanalyseA1.bereinigterNetzplan",
            "listeDerGeschaeftsprozesse": f"{base_path}.strukturanalyseA1.listeDerGeschaeftsprozesse",
            "listeDerAnwendungen": f"{base_path}.strukturanalyseA1.listeDerAnwendungen",
            "listeDerItSysteme": f"{base_path}.strukturanalyseA1.listeDerItSysteme",
            "listeDerRaeumeGebaeudeStandorte": f"{base_path}.strukturanalyseA1.listeDerRaeumeGebaeudeStandorte",
            "listeDerKommunikationsverbindungen": f"{base_path}.strukturanalyseA1.listeDerKommunikationsverbindungen",
            "stichprobenDokuStrukturanalyse": f"{base_path}.strukturanalyseA1.stichprobenDokuStrukturanalyse",
            "listeDerDienstleister": f"{base_path}.strukturanalyseA1.listeDerDienstleister",
            "ergebnisDerStrukturanalyse": f"{base_path}.strukturanalyseA1.ergebnisDerStrukturanalyse",
            "definitionDerSchutzbedarfskategorien": f"{base_path}.schutzbedarfsfeststellungA2.definitionDerSchutzbedarfskategorien",
            "schutzbedarfGeschaeftsprozesse": f"{base_path}.schutzbedarfsfeststellungA2.schutzbedarfGeschaeftsprozesse",
            "schutzbedarfAnwendungen": f"{base_path}.schutzbedarfsfeststellungA2.schutzbedarfAnwendungen",
            "schutzbedarfItSysteme": f"{base_path}.schutzbedarfsfeststellungA2.schutzbedarfItSysteme",
            "schutzbedarfRaeume": f"{base_path}.schutzbedarfsfeststellungA2.schutzbedarfRaeume",
            "schutzbedarfKommunikationsverbindungen": f"{base_path}.schutzbedarfsfeststellungA2.schutzbedarfKommunikationsverbindungen",
            "stichprobenDokuSchutzbedarf": f"{base_path}.schutzbedarfsfeststellungA2.stichprobenDokuSchutzbedarf",
            "ergebnisDerSchutzbedarfsfeststellung": f"{base_path}.schutzbedarfsfeststellungA2.ergebnisDerSchutzbedarfsfeststellung",
            "modellierungsdetails": f"{base_path}.modellierungDesInformationsverbundesA3.modellierungsdetails",
            "ergebnisDerModellierung": f"{base_path}.modellierungDesInformationsverbundesA3.ergebnisDerModellierung",
            "detailsZumItGrundschutzCheck": f"{base_path}.itGrundschutzCheckA4.detailsZumItGrundschutzCheck",
            "benutzerdefinierteBausteine": f"{base_path}.itGrundschutzCheckA4.benutzerdefinierteBausteine",
            "ergebnisItGrundschutzCheck": f"{base_path}.itGrundschutzCheckA4.ergebnisItGrundschutzCheck",
            "risikoanalyse": f"{base_path}.risikoanalyseA5.risikoanalyse",
            "realisierungsplan": f"{base_path}.realisierungsplanA6.realisierungsplan",
            "ergebnisDerDokumentenpruefung": f"{base_path}.ergebnisDerDokumentenpruefung",
        }

        for subchapter_key, result in stage_data.items():
            if not isinstance(result, dict): continue
            
            target_path = key_to_path_map.get(subchapter_key)
            if not target_path: continue

            if 'finding' in result and isinstance(result.get('finding'), dict):
                finding = result['finding']
                finding_text = f"[{finding.get('category')}] {finding.get('description')}"
                finding_list = self._ensure_list_path_exists(report, f"{target_path}.content")
                if finding_list:
                    for item in finding_list:
                        if item.get("type") == "finding":
                            item["findingText"] = finding_text; break
            
            if "answers" in result:
                answers = result.get("answers", [])
                content_list = self._ensure_list_path_exists(report, f"{target_path}.content", len(answers))
                if content_list:
                    answer_idx = 0
                    for item in content_list:
                        if item.get("type") == "question":
                            if answer_idx < len(answers):
                                item["answer"] = answers[answer_idx]; answer_idx += 1

            if "votum" in result:
                content_list = self._ensure_list_path_exists(report, f"{target_path}.content")
                if content_list:
                    for item in content_list:
                        if item.get("type") == "prose": item["text"] = result.get("votum", ""); break
            
            if "table" in result and isinstance(result.get("table"), dict):
                self._set_value_by_path(report, f"{target_path}.table.rows", result['table'].get('rows', []))

    def _populate_chapter_7_findings(self, report: dict) -> None:
        """
        Populates the findings tables in Chapter 7.2 from the central findings file,
        ensuring the findings are sorted numerically by their ID.
        """
        logging.info("Populating Chapter 7.2 with collected findings...")
        findings_path = f"{self.config.output_prefix}results/all_findings.json"
        try:
            all_findings = self.gcs_client.read_json(findings_path)
        except NotFound:
            logging.warning("Central findings file not found. Chapter 7.2 will be empty.")
            return

        # Use clean local lists for collection
        ag_table_rows, as_table_rows, e_table_rows = [], [], []

        for finding in all_findings:
            category = finding.get('category')
            row_data = {
                "Nummer": finding.get('id', 'N/A'),
                "Quelle (Kapitel)": finding.get('source_chapter', 'N/A')
            }
            if category == 'AG':
                row_data["Beschreibung der Abweichung"] = finding.get('description', 'N/A')                
                if finding.get('status') is not None:
                    row_data["Status"] = finding.get('status', 'Unbekannt')
                    row_data["Behebungsfrist"] = finding.get('behebungsfrist', 'N/A')
                else:
                    row_data["Status"] = "Offen"
                    row_data["Behebungsfrist"] = "30 Tage nach Audit"
                ag_table_rows.append(row_data)
            elif category == 'AS':
                row_data["Beschreibung der Abweichung"] = finding.get('description', 'N/A')                
                if finding.get('status') is not None:
                    row_data["Status"] = finding.get('status', 'Unbekannt')
                    row_data["Behebungsfrist"] = finding.get('behebungsfrist', 'N/A')
                else:
                    row_data["Status"] = "Offen"
                    row_data["Behebungsfrist"] = "Bis zum Abschluss des Audit"
                as_table_rows.append(row_data)
            elif category == 'E':
                row_data["Beschreibung der Empfehlung"] = finding.get('description', 'N/A')                
                if finding.get('status') is not None:
                    row_data["Status"] = finding.get('status', 'Unbekannt')
                    row_data["Behebungsfrist"] = finding.get('behebungsfrist', 'N/A')
                else:
                    row_data["Status"] = "Zur Umsetzung empfohlen"
                    row_data["Behebungsfrist"] = "N/A"
                e_table_rows.append(row_data)

        # --- FIX (Task J): Sort findings numerically by ID ---
        def sort_key(finding_dict: Dict[str, Any]) -> int:
            """Extracts the integer part of a finding ID for sorting."""
            try:
                # 'AG-12' -> '12' -> 12
                return int(finding_dict.get("Nummer", "0").split('-')[-1])
            except (ValueError, IndexError):
                return 0  # Fallback for malformed IDs

        ag_table_rows.sort(key=sort_key)
        as_table_rows.sort(key=sort_key)
        e_table_rows.sort(key=sort_key)
        logging.info("Sorted all findings tables numerically by ID.")
        # --- End of FIX ---

        # Now, set the sorted lists into the report dictionary
        self._set_value_by_path(report, 'bsiAuditReport.anhang.abweichungenUndEmpfehlungen.geringfuegigeAbweichungen.table.rows', ag_table_rows)
        self._set_value_by_path(report, 'bsiAuditReport.anhang.abweichungenUndEmpfehlungen.schwerwiegendeAbweichungen.table.rows', as_table_rows)
        self._set_value_by_path(report, 'bsiAuditReport.anhang.abweichungenUndEmpfehlungen.empfehlungen.table.rows', e_table_rows)

        logging.info(f"Populated Chapter 7.2 with {len(all_findings)} total findings.")

    def _populate_chapter_4(self, report: dict, stage_data: dict) -> None:
        """Populates Chapter 4 (Prüfplan) content into the report."""
        base_path = "bsiAuditReport.erstellungEinesPruefplans.auditplanung"
        # This map now consistently points to the 'rows' property inside a 'table' object.
        key_to_path_map = {
            "auswahlBausteineErstRezertifizierung": f"{base_path}.auswahlBausteineErstRezertifizierung.table.rows",
            "auswahlBausteine1Ueberwachungsaudit": f"{base_path}.auswahlBausteine1Ueberwachungsaudit.table.rows",
            "auswahlBausteine2Ueberwachungsaudit": f"{base_path}.auswahlBausteine2Ueberwachungsaudit.table.rows",
            "auswahlStandorte": f"{base_path}.auswahlStandorte.table.rows",
            "auswahlMassnahmenAusRisikoanalyse": f"{base_path}.auswahlMassnahmenAusRisikoanalyse.table.rows"
        }

        for key, data in stage_data.items():
            target_path = key_to_path_map.get(key)
            if not target_path: continue
            
            # Non-destructive update: Only write to the report if the stage data
            # for this section is non-empty. This preserves the baseline from Scan-Report.
            rows_data = data.get('rows', [])
            if rows_data:
                self._set_value_by_path(report, target_path, rows_data)


    def _populate_chapter_5(self, report: dict, stage_data: dict) -> None:
        """Populates Chapter 5 (Vor-Ort-Audit) content into the report."""
        if "verifikationDesITGrundschutzChecks" in stage_data:
            data = stage_data["verifikationDesITGrundschutzChecks"]
            path = "bsiAuditReport.vorOrtAudit.verifikationDesITGrundschutzChecks.einzelergebnisse.bausteinPruefungen"
            self._set_value_by_path(report, path, data.get("einzelergebnisse", {}).get("bausteinPruefungen", []))

        if "risikoanalyseA5" in stage_data:
            data = stage_data["risikoanalyseA5"]
            path = "bsiAuditReport.vorOrtAudit.risikoanalyseA5.einzelergebnisseDerRisikoanalyse.massnahmenPruefungen"
            self._set_value_by_path(report, path, data.get("einzelergebnisseDerRisikoanalyse", {}).get("massnahmenPruefungen", []))

    def _populate_chapter_7(self, report: dict, stage_data: dict) -> None:
        """Populates Chapter 7 (Anhang) content into the report."""
        ref_docs_data = stage_data.get('referenzdokumente', {})
        if isinstance(ref_docs_data.get('table'), dict):
            path = "bsiAuditReport.anhang.referenzdokumente.table.rows"
            self._set_value_by_path(report, path, ref_docs_data['table'].get('rows', []))

    def _populate_from_scan_report(self, report: dict, stage_data: dict) -> None:
        """Populates the report with baseline data from a scanned previous report."""
        logging.info("Populating baseline data from Scan-Report stage...")

        # 1. Populate Chapter 1 tables
        self._set_value_by_path(report, 'bsiAuditReport.allgemeines.versionshistorie.table.rows', stage_data.get('versionshistorie', {}).get('table', {}).get('rows', []))
        
        audit_institution_data = stage_data.get('auditierteInstitution', {})
        self._set_value_by_path(report, 'bsiAuditReport.allgemeines.auditierteInstitution.kontaktinformationenAntragsteller.table.rows', audit_institution_data.get('kontaktinformationenAntragsteller', {}).get('table', {}).get('rows', []))
        self._set_value_by_path(report, 'bsiAuditReport.allgemeines.auditierteInstitution.ansprechpartnerZertifizierung.table.rows', audit_institution_data.get('ansprechpartnerZertifizierung', {}).get('table', {}).get('rows', []))
        
        auditteam_data = stage_data.get('auditteam', {})
        self._set_value_by_path(report, 'bsiAuditReport.allgemeines.auditteam.auditteamleiter.table.rows', auditteam_data.get('auditteamleiter', {}).get('table', {}).get('rows', []))
        self._set_value_by_path(report, 'bsiAuditReport.allgemeines.auditteam.auditor.table.rows', auditteam_data.get('auditor', {}).get('table', {}).get('rows', []))
        self._set_value_by_path(report, 'bsiAuditReport.allgemeines.auditteam.fachexperte.table.rows', auditteam_data.get('fachexperte', {}).get('table', {}).get('rows', []))
        
        # 2. Populate Chapter 4 tables as a fallback/baseline
        self._set_value_by_path(report, 'bsiAuditReport.erstellungEinesPruefplans.auditplanung.auswahlBausteineErstRezertifizierung.table.rows', stage_data.get('auswahlBausteineErstRezertifizierung', {}).get('table', {}).get('rows', []))
        self._set_value_by_path(report, 'bsiAuditReport.erstellungEinesPruefplans.auditplanung.auswahlBausteine1Ueberwachungsaudit.table.rows', stage_data.get('auswahlBausteine1Ueberwachungsaudit', {}).get('table', {}).get('rows', []))
        self._set_value_by_path(report, 'bsiAuditReport.erstellungEinesPruefplans.auditplanung.auswahlStandorte.table.rows', stage_data.get('auswahlStandorte', {}).get('table', {}).get('rows', []))


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