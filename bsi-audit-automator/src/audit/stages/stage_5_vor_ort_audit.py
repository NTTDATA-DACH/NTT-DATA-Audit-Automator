# file: src/audit/stages/stage_5_vor_ort_audit.py
import logging
import json
from typing import Dict, Any, List, Tuple
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
    INTERMEDIATE_CHECK_RESULTS_PATH = "output/results/intermediate/extracted_grundschutz_check_merged.json"

    def __init__(self, config: AppConfig, gcs_client: GcsClient, ai_client: AiClient):
        self.config = config
        self.gcs_client = gcs_client
        self.ai_client = ai_client
        self.control_catalog = ControlCatalog()
        logging.info(f"Initialized runner for stage: {self.STAGE_NAME}")

    def _load_extracted_check_data(self) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """
        Loads the refined Grundschutz-Check data and creates a lookup map
        keyed by a composite tuple of (requirement_id, zielobjekt_kuerzel)
        for efficient, context-aware access.
        """
        try:
            data = self.gcs_client.read_json(self.INTERMEDIATE_CHECK_RESULTS_PATH)
            anforderungen_list = data.get("anforderungen", [])
            # The key is a tuple of the requirement ID and the target object's short ID (Kürzel).
            # This correctly handles the same requirement applied to multiple objects.
            lookup_map = {
                (item['id'], item['zielobjekt_kuerzel']): item 
                for item in anforderungen_list if 'id' in item and 'zielobjekt_kuerzel' in item
            }
            logging.info(f"Successfully loaded and mapped {len(lookup_map)} unique requirement-object pairs for Chapter 5.")
            return lookup_map
        except NotFound:
            logging.warning(f"Refined check data file '{self.INTERMEDIATE_CHECK_RESULTS_PATH}' not found. Checklist will not contain customer explanations.")
            return {}
        except Exception as e:
            logging.error(f"Failed to load or parse refined check data: {e}", exc_info=True)
            return {}

    def _generate_control_checklist(self, chapter_4_data: Dict[str, Any], extracted_data_map: Dict[Tuple[str, str], Dict[str, Any]]) -> Dict[str, Any]:
        """
        Deterministically generates the control checklist for subchapter 5.5.2,
        enriching it with the high-quality, merged customer explanations that are
        specific to the Zielobjekt selected in the audit plan.
        """
        name = "verifikationDesITGrundschutzChecks"
        logging.info(f"Generating enriched and context-aware control checklist for {name} (5.5.2)...")
        
        # Build a helper map to resolve Zielobjekt names from the plan to their Kürzel.
        name_to_kuerzel_map = {}
        for req_data in extracted_data_map.values():
            zielobjekt_name = req_data.get('zielobjekt_name')
            zielobjekt_kuerzel = req_data.get('zielobjekt_kuerzel')
            if zielobjekt_name and zielobjekt_kuerzel:
                # This will overwrite but should be consistent
                name_to_kuerzel_map[zielobjekt_name] = zielobjekt_kuerzel
        # Handle the special case for ISMS etc.
        name_to_kuerzel_map["Gesamter Informationsverbund"] = "Informationsverbund"

        # Combine bausteine from all possible sections of chapter 4
        selected_bausteine = []
        baustein_sections = [
            "auswahlBausteineErstRezertifizierung", 
            "auswahlBausteine1Ueberwachungsaudit", 
            "auswahlBausteine2Ueberwachungsaudit"
        ]
        for section in baustein_sections:
            # Use underscore_case key for headers now
            section_data = chapter_4_data.get(section, {})
            if isinstance(section_data, dict) and "table" in section_data:
                 selected_bausteine.extend(section_data.get("table", {}).get("rows", []))
        
        if not selected_bausteine:
            logging.warning("No Bausteine found in Chapter 4 results. Checklist for 5.5.2 will be empty.")
            return {name: {"einzelergebnisse": {"bausteinPruefungen": []}}}

        baustein_pruefungen_list = []
        for baustein_plan_item in selected_bausteine:
            baustein_id_full = baustein_plan_item.get("Baustein", "")
            if not baustein_id_full:
                continue

            baustein_id = baustein_id_full.split(" ")[0]
            zielobjekt_name_from_plan = baustein_plan_item.get("Zielobjekt", "")
            
            # Resolve the name to the short ID (Kürzel) needed for the lookup key
            zielobjekt_kuerzel_from_plan = name_to_kuerzel_map.get(zielobjekt_name_from_plan)
            if not zielobjekt_kuerzel_from_plan:
                logging.warning(f"Could not resolve Zielobjekt name '{zielobjekt_name_from_plan}' to a Kürzel for Baustein '{baustein_id}'. Skipping its specific details.")

            controls = self.control_catalog.get_controls_for_baustein_id(baustein_id)

            anforderungen_list = []
            for control in controls:
                control_id = control.get("id", "N/A")
                
                # Perform the context-aware lookup using the resolved Kürzel
                lookup_key = (control_id, zielobjekt_kuerzel_from_plan) if zielobjekt_kuerzel_from_plan else None
                extracted_details = extracted_data_map.get(lookup_key, {})
                
                customer_explanation = extracted_details.get("umsetzungserlaeuterung", "Keine spezifische Angabe für dieses Zielobjekt im Grundschutz-Check gefunden.")
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
                "bezogenAufZielobjekt": zielobjekt_name_from_plan,
                "auditiertAm": "", # To be filled by auditor
                "auditor": "", # To be filled by auditor
                "befragtWurde": "", # To be filled by auditor
                "anforderungen": anforderungen_list
            })

        logging.info(f"Generated checklist with {len(baustein_pruefungen_list)} Bausteine for manual audit, now filtered by selected Zielobjekt.")
        return {name: {"einzelergebnisse": {"bausteinPruefungen": baustein_pruefungen_list}}}
        
    def _generate_risikoanalyse_checklist(self, chapter_4_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generates the checklist for risk analysis measures (5.6.2) based on
        the measures selected in Chapter 4.1.5.
        """
        name = "risikoanalyseA5"
        logging.info(f"Deterministically generating checklist for {name} (5.6.2)...")
        
        # Use underscore_case key for headers now
        selected_measures = chapter_4_data.get("auswahlMassnahmenAusRisikoanalyse", {}).get("table", {}).get("rows", [])
        
        if not selected_measures:
            logging.warning("No measures from risk analysis found in Chapter 4 results. Checklist for 5.6.2 will be empty.")
            return {name: {"einzelergebnisseDerRisikoanalyse": {"massnahmenPruefungen": []}}}

        massnahmen_pruefungen_list = []
        for measure in selected_measures:
            massnahmen_pruefungen_list.append({
                "massnahme": measure.get("Massnahme", "N/A"),
                "zielobjekt": measure.get("Zielobjekt", "N/A"),
                "bewertung": "",
                "pruefmethode": { "D": False, "I": False, "C": False, "S": False, "A": False, "B": False },
                "auditfeststellung": "", # To be filled by auditor
                "abweichungen": "" # To be filled by auditor
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