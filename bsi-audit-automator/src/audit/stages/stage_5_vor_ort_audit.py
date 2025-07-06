# src/audit/stages/stage_5_vor_ort_audit.py
import logging
import json
from typing import Dict, Any

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.ai_client import AiClient
from src.audit.stages.control_catalog import ControlCatalog

class Chapter5Runner:
    """
    Handles generating content for Chapter 5 "Vor-Ort-Audit".
    It deterministically prepares the control checklist for the manual audit.
    """
    STAGE_NAME = "Chapter-5"

    def __init__(self, config: AppConfig, gcs_client: GcsClient, ai_client: AiClient):
        self.config = config
        self.gcs_client = gcs_client
        self.ai_client = ai_client
        self.control_catalog = ControlCatalog()
        logging.info(f"Initialized runner for stage: {self.STAGE_NAME}")

    def _generate_control_checklist(self, chapter_4_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Deterministically generates the control checklist for subchapter 5.5.2.
        This list is for the human auditor to use during the on-site audit.
        """
        name = "verifikationDesITGrundschutzChecks"
        logging.info(f"Deterministically generating control checklist for {name} (5.5.2)...")

        # Combine bausteine from all possible sections of chapter 4
        selected_bausteine = []
        baustein_sections = [
            "auswahlBausteineErstRezertifizierung", 
            "auswahlBausteine1Ueberwachungsaudit", 
            "auswahlBausteine2Ueberwachungsaudit"
        ]
        for section in baustein_sections:
            if section in chapter_4_data:
                 selected_bausteine.extend(chapter_4_data[section].get("rows", []))
        
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
                anforderungen_list.append({
                    "nummer": control.get("id", "N/A"),
                    "anforderung": control.get("title", "N/A"),
                    "bewertung": "",
                    "auditfeststellung": "",
                    "abweichungen": ""
                })
            
            baustein_pruefungen_list.append({
                "baustein": baustein_id_full,
                "zielobjekt": baustein.get("Zielobjekt", ""),
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
        
        try:
            ch4_results_path = f"{self.config.output_prefix}results/Chapter-4.json"
            chapter_4_data = self.gcs_client.read_json(ch4_results_path)
            logging.info("Successfully loaded dependency: Chapter 4 results.")
        except Exception as e:
            logging.error(f"Could not load Chapter 4 results, which are required for Chapter 5. Aborting stage. Error: {e}")
            raise
        
        checklist_result = self._generate_control_checklist(chapter_4_data)
        risiko_result = self._generate_risikoanalyse_checklist(chapter_4_data)
        
        final_result = {**checklist_result, **risiko_result}
            
        logging.info(f"Successfully prepared data for stage {self.STAGE_NAME}")
        return final_result