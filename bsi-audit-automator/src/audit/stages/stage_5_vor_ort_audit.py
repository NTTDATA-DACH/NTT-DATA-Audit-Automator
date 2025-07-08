# src/audit/stages/stage_5_vor_ort_audit.py
import logging
import json
from typing import Dict, Any, List
from google.cloud.exceptions import NotFound

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.ai_client import AiClient
from src.audit.stages.control_catalog import ControlCatalog

class Chapter5Runner:
    """
    Handles generating content for Chapter 5 "Vor-Ort-Audit".
    It deterministically prepares the control checklist for the manual audit,
    enriching it with data extracted in prior stages.
    """
    STAGE_NAME = "Chapter-5"
    INTERMEDIATE_CHECK_RESULTS_PATH = "output/results/intermediate/extracted_grundschutz_check.json"

    def __init__(self, config: AppConfig, gcs_client: GcsClient, ai_client: AiClient):
        self.config = config
        self.gcs_client = gcs_client
        self.ai_client = ai_client
        self.control_catalog = ControlCatalog()
        logging.info(f"Initialized runner for stage: {self.STAGE_NAME}")

    def _load_extracted_check_data(self) -> Dict[str, Dict[str, Any]]:
        """Loads the extracted Grundschutz-Check data and creates a lookup map."""
        try:
            data = self.gcs_client.read_json(self.INTERMEDIATE_CHECK_RESULTS_PATH)
            anforderungen_list = data.get("anforderungen", [])
            # Create a map from requirement ID to the full object for easy lookup
            lookup_map = {item['id']: item for item in anforderungen_list}
            logging.info(f"Successfully loaded and mapped {len(lookup_map)} extracted requirements.")
            return lookup_map
        except NotFound:
            logging.warning(f"Intermediate file '{self.INTERMEDIATE_CHECK_RESULTS_PATH}' not found. Checklist will not contain customer explanations.")
            return {}
        except Exception as e:
            logging.error(f"Failed to load or parse intermediate check data: {e}", exc_info=True)
            return {}

    def _generate_control_checklist(self, chapter_4_data: Dict[str, Any], extracted_data_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """
        Deterministically generates the control checklist for subchapter 5.5.2,
        enriching it with customer explanations from the extracted data.
        """
        name = "verifikationDesITGrundschutzChecks"
        logging.info(f"Generating enriched control checklist for {name} (5.5.2)...")

        # Combine bausteine from all possible sections of chapter 4
        selected_bausteine = []
        baustein_sections = [
            "auswahlBausteineErstRezertifizierung", 
            "auswahlBausteine1Ueberwachungsaudit", 
            "auswahlBausteine2Ueberwachungsaudit"
        ]
        for section in baustein_sections:
            section_data = chapter_4_data.get(section, {})
            if isinstance(section_data, dict) and "table" in section_data:
                 selected_bausteine.extend(section_data.get("table", {}).get("rows", []))
        
        if not selected_bausteine:
            logging.warning("No Bausteine found in Chapter 4 results. Checklist for 5.5.2 will be empty.")
            return {name: {"einzelergebnisse": {"bausteinPruefungen": []}}}

        baustein_pruefungen_list = []
        for baustein in selected_bausteine:
            baustein_id_full = baustein.get("Baustein", "")
            if not baustein_id_full:
                continue

            baustein_id = baustein_id_full.split(" ")[0]
            controls = self.control_catalog.get_controls_for_baustein_id(baustein_id)

            anforderungen_list = []
            for control in controls:
                control_id = control.get("id", "N/A")
                extracted_details = extracted_data_map.get(control_id, {})
                customer_explanation = extracted_details.get("umsetzungserlaeuterung", "Keine Angabe im Grundschutz-Check gefunden.")
                bewertung_status = extracted_details.get("umsetzungsstatus", "N/A")

                anforderungen_list.append({
                    "nummer": control_id,
                    "anforderung": control.get("title", "N/A"),
                    "bewertung": bewertung_status,
                    "dokuAntragsteller": customer_explanation,
                    "pruefmethode": { "D": False, "I": False, "C": False, "S": False, "A": False, "B": False },
                    "auditfeststellung": "", # To be filled by auditor
                    "abweichungen": "" # To be filled by auditor
                })
            
            baustein_pruefungen_list.append({
                "baustein": baustein_id_full,
                "bezogenAufZielobjekt": baustein.get("Zielobjekt", ""),
                "auditiertAm": "", # To be filled by auditor
                "auditor": "", # To be filled by auditor
                "befragtWurde": "", # To be filled by auditor
                "anforderungen": anforderungen_list
            })

        logging.info(f"Generated checklist with {len(baustein_pruefungen_list)} Bausteine for manual audit.")
        return {name: {"einzelergebnisse": {"bausteinPruefungen": baustein_pruefungen_list}}}
        
    def _generate_risikoanalyse_checklist(self, chapter_4_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generates the checklist for risk analysis measures (5.6.2) based on
        the measures selected in Chapter 4.1.5.
        """
        name = "risikoanalyseA5"
        logging.info(f"Deterministically generating checklist for {name} (5.6.2)...")
        
        selected_measures = chapter_4_data.get("auswahlMassnahmenAusRisikoanalyse", {}).get("table", {}).get("rows", [])
        
        if not selected_measures:
            logging.warning("No measures from risk analysis found in Chapter 4 results. Checklist for 5.6.2 will be empty.")
            return {name: {"einzelergebnisseDerRisikoanalyse": {"massnahmenPruefungen": []}}}

        massnahmen_pruefungen_list = []
        for measure in selected_measures:
            massnahmen_pruefungen_list.append({
                "massnahme": measure.get("Maßnahme", "N/A"),
                "zielobjekt": measure.get("Zielobjekt", "N/A"),
                "bewertung": "",
                "auditfeststellung": "",
                "abweichungen": ""
            })
            
        logging.info(f"Generated checklist with {len(massnahmen_pruefungen_list)} risk analysis measures.")
        return {name: {"einzelergebnisseDerRisikoanalyse": {"massnahmenPruefungen": massnahmen_pruefungen_list}}}

    async def run(self) -> dict:
        """
        Executes the generation logic for Chapter 5.
        """
        logging.info(f"Executing stage: {self.STAGE_NAME}")
        
        # Load all dependencies first
        try:
            ch4_results_path = f"{self.config.output_prefix}results/Chapter-4.json"
            chapter_4_data = self.gcs_client.read_json(ch4_results_path)
            logging.info("Successfully loaded dependency: Chapter 4 results.")
        except Exception as e:
            logging.error(f"Could not load Chapter 4 results, which are required for Chapter 5. Aborting stage. Error: {e}")
            raise

        # This will return a map or an empty dict if the file doesn't exist. The process can continue.
        extracted_check_data_map = self._load_extracted_check_data()
        
        checklist_result = self._generate_control_checklist(chapter_4_data, extracted_check_data_map)
        risiko_result = self._generate_risikoanalyse_checklist(chapter_4_data)
        
        final_result = {**checklist_result, **risiko_result}
            
        logging.info(f"Successfully prepared data for stage {self.STAGE_NAME}")
        return final_result