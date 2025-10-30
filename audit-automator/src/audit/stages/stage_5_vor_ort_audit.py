# file: src/audit/stages/stage_5_vor_ort_audit.py
import logging
import json
from typing import Dict, Any, List, Tuple
from google.cloud.exceptions import NotFound

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.ai_client import AiClient
from src.audit.stages.control_catalog import ControlCatalog
from src.constants import EXTRACTED_CHECK_DATA_PATH, GROUND_TRUTH_MAP_PATH

class Chapter5Runner:
    """
    Handles generating content for Chapter 5 "Vor-Ort-Audit".
    It deterministically prepares the control checklist for the manual audit,
    enriching it with data extracted in prior stages.
    """
    STAGE_NAME = "Chapter-5"

    def __init__(self, config: AppConfig, gcs_client: GcsClient, ai_client: AiClient):
        self.config = config
        self.gcs_client = gcs_client
        self.ai_client = ai_client
        self.control_catalog = ControlCatalog()
        logging.info(f"Initialized runner for stage: {self.STAGE_NAME}")

    def _load_system_structure_map(self) -> Dict[str, Any]:
        """
        Loads the ground-truth system structure map which contains the authoritative
        Baustein-to-Zielobjekt mappings.
        """
        try:
            system_map = self.gcs_client.read_json(GROUND_TRUTH_MAP_PATH)
            logging.info(f"Successfully loaded ground truth map from: {GROUND_TRUTH_MAP_PATH}")
            return system_map
        except NotFound:
            logging.error(f"FATAL: Ground truth map '{GROUND_TRUTH_MAP_PATH}' not found. Cannot generate Chapter 5 checklist. Please run the 'Grundschutz-Check-Extraction' stage first.")
            raise
        except Exception as e:
            logging.error(f"Failed to load or parse ground truth map: {e}", exc_info=True)
            raise

    def _load_extracted_check_data(self) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """
        Loads the refined Grundschutz-Check data and creates a lookup map
        keyed by a composite tuple of (requirement_id, zielobjekt_kuerzel)
        for efficient, context-aware access.
        """
        try:
            data = self.gcs_client.read_json(EXTRACTED_CHECK_DATA_PATH)
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
            logging.warning(f"Refined check data file '{EXTRACTED_CHECK_DATA_PATH}' not found. Checklist will not contain customer explanations. Please run the 'Grundschutz-Check-Extraction' stage first.")
            return {}
        except Exception as e:
            logging.error(f"Failed to load or parse refined check data: {e}", exc_info=True)
            return {}

    def _generate_control_checklist(self, chapter_4_data: Dict[str, Any], system_structure_map: Dict[str, Any], extracted_data_map: Dict[Tuple[str, str], Dict[str, Any]]) -> Dict[str, Any]:
        """
        Deterministically generates the control checklist for subchapter 5.5.2,
        using the specific Zielobjekt selected in the audit plan (Chapter 4)
        to select the exact instance to audit and populate with customer data.
        """
        name = "verifikationDesITGrundschutzChecks"
        logging.info(f"Generating control checklist for {name} based on the specific audit plan...")
        
        # Combine bausteine from all possible sections of chapter 4
        selected_bausteine = []
        baustein_sections = [
            "auswahlBausteineErstRezertifizierung", 
            "auswahlBausteine1Ueberwachungsaudit", 
            "auswahlBausteine2Ueberwachungsaudit"
        ]
        for section in baustein_sections:
            section_data = chapter_4_data.get(section, {})
            if isinstance(section_data, dict):
                 selected_bausteine.extend(section_data.get("rows", []))
        
        if not selected_bausteine:
            logging.warning("No Bausteine found in Chapter 4 results. Checklist for 5.5.2 will be empty.")
            return {name: {"einzelergebnisse": {"bausteinPruefungen": []}}}

        baustein_pruefungen_list = []
        for i, baustein_plan_item in enumerate(selected_bausteine):
            baustein_id_full = baustein_plan_item.get("Baustein", "")
            if not baustein_id_full: continue
            baustein_id = baustein_id_full.split(" ")[0]

            # --- ROBUSTNESS FIX (Task H) ---
            # Directly get the name and Kürzel from the plan. No more fragile name-based lookups.
            zielobjekt_name_from_plan = baustein_plan_item.get("Zielobjekt-Name")
            planned_zielobjekt_kuerzel = baustein_plan_item.get("Zielobjekt-Kürzel")

            if not planned_zielobjekt_kuerzel:
                logging.warning(f"Could not find 'Zielobjekt-Kürzel' in audit plan for Baustein '{baustein_id}'. Specific details for its controls will be missing.")

            controls = self.control_catalog.get_controls_for_baustein_id(baustein_id)
            
            anforderungen_list = []
            for control in controls:
                control_id = control.get("id", "N/A")
                # The lookup key is now robustly created using the Kürzel from the plan.
                lookup_key = (control_id, planned_zielobjekt_kuerzel) if planned_zielobjekt_kuerzel else None
                extracted_details = extracted_data_map.get(lookup_key, {})
                
                customer_explanation = extracted_details.get("umsetzungserlaeuterung", "Keine spezifische Angabe für dieses Zielobjekt im Grundschutz-Check gefunden.")
                bewertung_status_raw = extracted_details.get("umsetzungsstatus", "N/A")

                status_map = {"Ja": "Umgesetzt", "Nein": "Nicht umgesetzt", "teilweise": "Teilweise umgesetzt", "entbehrlich": "Entbehrlich"}
                final_bewertung_status = status_map.get(bewertung_status_raw, bewertung_status_raw)

                anforderungen_list.append({
                    "nummer": control_id,
                    "anforderung": control.get("title", "N/A"),
                    "bewertung": final_bewertung_status,
                    "dokuAntragsteller": customer_explanation,
                    "pruefmethode": { "D": False, "I": False, "C": False, "S": False, "A": False, "B": False },
                    "auditfeststellung": "",
                    "abweichungen": ""
                })
            
            # Create the new subchapter structure
            baustein_pruefungen_list.append({
                "subchapterNumber": f"5.5.2.{i+1}",
                "title": f"Prüfung für Baustein: {baustein_id_full}",
                "baustein": baustein_id_full,
                "bezogenAufZielobjekt": zielobjekt_name_from_plan,
                "auditiertAm": "",
                "auditor": "",
                "befragtWurde": "",
                "anforderungen": anforderungen_list
            })

        logging.info(f"Generated checklist with {len(baustein_pruefungen_list)} specific Baustein/Zielobjekt subchapters for manual audit.")
        return {name: {"einzelergebnisse": {"bausteinPruefungen": baustein_pruefungen_list}}}
        
    def _generate_risikoanalyse_checklist(self, chapter_4_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generates the checklist for risk analysis measures (5.6.2) based on
        the measures selected in Chapter 4.1.5.
        """
        name = "risikoanalyseA5"
        logging.info(f"Deterministically generating checklist for {name} (5.6.2)...")
        
        selected_measures = chapter_4_data.get("auswahlMassnahmenAusRisikoanalyse", {}).get("rows", [])
        
        if not selected_measures:
            logging.warning("No measures from risk analysis found in Chapter 4 results. Checklist for 5.6.2 will be empty.")
            return {name: {"einzelergebnisseDerRisikoanalyse": {"massnahmenPruefungen": []}}}

        massnahmen_pruefungen_list = []
        for measure in selected_measures:
            massnahmen_pruefungen_list.append({
                "massnahme": measure.get("Maßnahme", "N/A"),
                "zielobjekt": measure.get("Zielobjekt", "N/A"),
                "bewertung": "",
                "dokuAntragsteller": "",
                "pruefmethode": { "D": False, "I": False, "C": False, "S": False, "A": False, "B": False },
                "auditfeststellung": "",
                "abweichungen": ""
            })
            
        logging.info(f"Generated checklist with {len(massnahmen_pruefungen_list)} risk analysis measures.")
        return {name: {"einzelergebnisseDerRisikoanalyse": {"massnahmenPruefungen": massnahmen_pruefungen_list}}}

    async def run(self, force_overwrite: bool = False) -> dict:
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

        system_structure_map = self._load_system_structure_map()
        extracted_check_data_map = self._load_extracted_check_data()
        
        checklist_result = self._generate_control_checklist(chapter_4_data, system_structure_map, extracted_check_data_map)
        risiko_result = self._generate_risikoanalyse_checklist(chapter_4_data)
        
        final_result = {**checklist_result, **risiko_result}
        
        logging.info(f"Successfully prepared data for stage {self.STAGE_NAME}")
        return final_result