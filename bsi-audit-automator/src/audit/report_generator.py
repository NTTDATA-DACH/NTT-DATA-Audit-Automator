# src/audit/report_generator.py
import logging
import json
import asyncio
from google.cloud.exceptions import NotFound
from typing import Dict, Any, List
from jsonschema import validate, ValidationError

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
            if not isinstance(current_level, dict) or key not in current_level:
                logging.warning(f"Template path '{path}' missing part '{key}'. Cannot set value.")
                return
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
            if not isinstance(current_level, dict) or key not in current_level:
                logging.warning(f"Template path '{path}' missing part '{key}'. Cannot ensure list path.")
                return []
            current_level = current_level[key]

        if not isinstance(current_level, list):
            logging.warning(f"Target for path '{path}' is not a list. Cannot pad.")
            return []
        
        while len(current_level) < min_length:
            current_level.append(default_item.copy())
            
        return current_level

    async def _initialize_report_on_gcs(self) -> dict:
        """
        Loads the report template. It first tries to load from a working copy on GCS,
        falling back to the local `master_report_template.json` if it doesn't exist.
        """
        try:
            report = await self.gcs_client.read_json_async(self.gcs_report_path)
            logging.info(f"Loaded existing report template from GCS: {self.gcs_report_path}")
            return report
        except NotFound:
            logging.info("No report template found on GCS. Initializing from local asset.")
            with open(self.LOCAL_MASTER_TEMPLATE_PATH, 'r', encoding='utf-8') as f:
                report = json.load(f)
            
            # Pre-populate with basic info
            self._set_value_by_path(report, 'bsiAuditReport.titlePage.auditedInstitution', "Audited Institution")
            self._set_value_by_path(report, 'bsiAuditReport.allgemeines.audittyp.content', self.config.audit_type)
            
            await self.gcs_client.upload_from_string_async(
                content=json.dumps(report, indent=2, ensure_ascii=False),
                destination_blob_name=self.gcs_report_path
            )
            logging.info(f"Saved initial report template to GCS: {self.gcs_report_path}")
            return report

    def _populate_chapter_1(self, report: dict, stage_data: dict) -> None:
        """Populates the 'Allgemeines' (Chapter 1) of the report defensively."""
        geltungsbereich_data = stage_data.get('geltungsbereichDerZertifizierung', {})
        
        # Populate Geltungsbereich (1.2)
        final_text = geltungsbereich_data.get('description', '')
        if isinstance(geltungsbereich_data.get('finding'), dict):
            finding = geltungsbereich_data['finding']
            if finding.get('category') != 'OK':
                final_text += f"\n\nFeststellung: [{finding.get('category')}] {finding.get('description')}"
        
        geltungsbereich_list = self._ensure_list_path_exists(report, 'bsiAuditReport.allgemeines.geltungsbereichDerZertifizierung.content')
        if geltungsbereich_list:
            geltungsbereich_list[0]['text'] = final_text

        # Populate Informationsverbund (1.4)
        info_list = self._ensure_list_path_exists(report, 'bsiAuditReport.allgemeines.informationsverbund.content', min_length=2)
        if info_list:
            info_list[0]['text'] = geltungsbereich_data.get('kurzbezeichnung', '')
            info_list[1]['text'] = geltungsbereich_data.get('kurzbeschreibung', '')

        # Populate Audittyp (1.5)
        audittyp_content = stage_data.get('audittyp', {}).get('content', self.config.audit_type)
        self._set_value_by_path(report, 'bsiAuditReport.allgemeines.audittyp.content', audittyp_content)

    def _populate_chapter_7_findings(self, report: dict) -> None:
        """Populates the findings tables in Chapter 7.2 from the central findings file."""
        logging.info("Populating Chapter 7.2 with collected findings...")
        findings_path = f"{self.config.output_prefix}results/all_findings.json"
        try:
            all_findings = self.gcs_client.read_json(findings_path)
        except NotFound:
            logging.warning("Central findings file not found. Chapter 7.2 will be empty.")
            return

        ag_table_rows = self._ensure_list_path_exists(report, 'bsiAuditReport.anhang.abweichungenUndEmpfehlungen.geringfuegigeAbweichungen.table.rows')
        as_table_rows = self._ensure_list_path_exists(report, 'bsiAuditReport.anhang.abweichungenUndEmpfehlungen.schwerwiegendeAbweichungen.table.rows')
        e_table_rows = self._ensure_list_path_exists(report, 'bsiAuditReport.anhang.abweichungenUndEmpfehlungen.empfehlungen.table.rows')
        
        if ag_table_rows is None or as_table_rows is None or e_table_rows is None: return

        ag_table_rows.clear(); as_table_rows.clear(); e_table_rows.clear()
        ag_counter, as_counter, e_counter = 0, 0, 0

        for finding in all_findings:
            category = finding.get('category')
            if category == 'AG':
                ag_counter += 1
                ag_table_rows.append({
                    "Nr.": f"AG-{ag_counter}", "Beschreibung der Abweichung": finding.get('description', 'N/A'),
                    "Quelle (Kapitel)": finding.get('source_chapter', 'N/A'), "Behebungsfrist": "30 Tage nach Audit", "Status": "Offen"
                })
            elif category == 'AS':
                as_counter += 1
                as_table_rows.append({
                    "Nr.": f"AS-{as_counter}", "Beschreibung der Abweichung": finding.get('description', 'N/A'),
                    "Quelle (Kapitel)": finding.get('source_chapter', 'N/A'), "Behebungsfrist": "30 Tage nach Audit", "Status": "Offen"
                })
            elif category == 'E':
                e_counter += 1
                e_table_rows.append({
                    "Nr.": f"E-{e_counter}", "Beschreibung der Empfehlung": finding.get('description', 'N/A'),
                    "Quelle (Kapitel)": finding.get('source_chapter', 'N/A'), "Behebungsfrist": "N/A", "Status": "Zur Umsetzung empfohlen"
                })
        
        logging.info(f"Populated Chapter 7.2 with {len(all_findings)} total findings.")

    async def assemble_report(self) -> None:
        """
        Main method to assemble the final report. It loads all stage results
        and collected findings, populates a master template, and saves the final
        output to GCS.
        """
        report = await self._initialize_report_on_gcs()

        # Read all stage results concurrently for better performance
        stage_read_tasks = [self.gcs_client.read_json_async(f"{self.config.output_prefix}results/{s}.json") for s in self.STAGES_TO_AGGREGATE]
        stage_results = await asyncio.gather(*stage_read_tasks, return_exceptions=True)
        
        stage_data_map = {}
        for i, result in enumerate(stage_results):
            stage_name = self.STAGES_TO_AGGREGATE[i]
            if isinstance(result, Exception):
                logging.warning(f"Result for stage '{stage_name}' not found or failed to load. Skipping population. Error: {result}")
            else:
                stage_data_map[stage_name] = result

        for stage_name, stage_data in stage_data_map.items():
            self._populate_report(report, stage_name, stage_data)
        
        self._populate_chapter_7_findings(report)

        # Validate the final report against the schema before saving
        try:
            validate(instance=report, schema=self.report_schema)
            logging.info("Final report successfully validated against the master schema.")
        except ValidationError as e:
            logging.error(f"CRITICAL: Final report failed schema validation. Report will not be saved. Error: {e.message}")
            return # Do not save a corrupted report

        final_report_path = f"{self.config.output_prefix}final_audit_report.json"
        await self.gcs_client.upload_from_string_async(
            content=json.dumps(report, indent=2, ensure_ascii=False),
            destination_blob_name=final_report_path
        )
        logging.info(f"Final report assembled and saved to: gs://{self.config.bucket_name}/{final_report_path}")
        
        # Optimized write: copy the final report to the working directory instead of a second upload
        await self.gcs_client.copy_blob_async(
            source_blob_name=final_report_path,
            destination_blob_name=self.gcs_report_path
        )
        logging.info(f"Updated master report state on GCS via copy: gs://{self.config.bucket_name}/{self.gcs_report_path}")

    def _populate_chapter_3(self, report: dict, stage_data: dict) -> None:
        """Populates Chapter 3 (Dokumentenprüfung) content into the report."""
        base_path = "bsiAuditReport.dokumentenpruefung"
        key_to_path_map = {
            # ... (Full map as defined in previous correct implementation)
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
            "risikoanalyseA5": f"{base_path}.risikoanalyseA5.risikoanalyse",
            "ergebnisRisikoanalyse": f"{base_path}.risikoanalyseA5.ergebnisRisikoanalyse",
            "realisierungsplanA6": f"{base_path}.realisierungsplanA6.realisierungsplan",
            "ergebnisRealisierungsplan": f"{base_path}.realisierungsplanA6.ergebnisRealisierungsplan",
            "ergebnisDerDokumentenpruefung": f"{base_path}.ergebnisDerDokumentenpruefung",
        }

        for subchapter_key, result in stage_data.items():
            if not isinstance(result, dict): continue
            
            target_path = key_to_path_map.get(subchapter_key)
            if not target_path: continue

            # Populate finding text
            if 'finding' in result and isinstance(result.get('finding'), dict):
                finding = result['finding']
                finding_text = f"[{finding.get('category')}] {finding.get('description')}"
                finding_list = self._ensure_list_path_exists(report, f"{target_path}.content")
                if finding_list:
                    for item in finding_list:
                        if item.get("type") == "finding":
                            item["findingText"] = finding_text; break
            
            # Populate answers for questions or text for prose
            if "answers" in result:
                answers = result.get("answers", [])
                content_list = self._ensure_list_path_exists(report, f"{target_path}.content", len(answers))
                if content_list:
                    answer_idx = 0
                    for item in content_list:
                        if item.get("type") == "question":
                            if answer_idx < len(answers):
                                item["answer"] = answers[answer_idx]; answer_idx += 1
            elif "votum" in result:
                content_list = self._ensure_list_path_exists(report, f"{target_path}.content")
                if content_list:
                    for item in content_list:
                        if item.get("type") == "prose": item["text"] = result.get("votum", ""); break
            
            # Populate table data
            if "table" in result and isinstance(result.get("table"), dict):
                self._set_value_by_path(report, f"{target_path}.table.rows", result['table'].get('rows', []))

    def _populate_chapter_4(self, report: dict, stage_data: dict) -> None:
        """Populates Chapter 4 (Prüfplan) content into the report."""
        ch4_plan_key = next(iter(stage_data)) if stage_data else None
        if not ch4_plan_key: return

        result = stage_data.get(ch4_plan_key, {})
        target_key_map = {"auswahlBausteineUeberwachung": "auswahlBausteineErstRezertifizierung"}
        target_key = target_key_map.get(ch4_plan_key, ch4_plan_key)
        
        path = f"bsiAuditReport.erstellungEinesPruefplans.auditplanung.{target_key}.rows"
        self._set_value_by_path(report, path, result.get('rows', []))

    def _populate_chapter_5(self, report: dict, stage_data: dict) -> None:
        """Populates Chapter 5 (Vor-Ort-Audit) content into the report."""
        if "verifikationDesITGrundschutzChecks" in stage_data:
            data = stage_data["verifikationDesITGrundschutzChecks"]
            path = "bsiAuditReport.vorOrtAudit.verifikationDesITGrundschutzChecks.einzelergebnisse.bausteinPruefungen"
            self._set_value_by_path(report, path, data.get("bausteinPruefungen", []))

        if "einzelergebnisseDerRisikoanalyse" in stage_data:
            data = stage_data["einzelergebnisseDerRisikoanalyse"]
            path = "bsiAuditReport.vorOrtAudit.risikoanalyseA5.einzelergebnisseDerRisikoanalyse.massnahmenPruefungen"
            self._set_value_by_path(report, path, data.get("massnahmenPruefungen", []))

    def _populate_chapter_7(self, report: dict, stage_data: dict) -> None:
        """Populates Chapter 7 (Anhang) content into the report."""
        ref_docs_data = stage_data.get('referenzdokumente', {})
        if isinstance(ref_docs_data.get('table'), dict):
            path = "bsiAuditReport.anhang.referenzdokumente.table.rows"
            self._set_value_by_path(report, path, ref_docs_data['table'].get('rows', []))

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