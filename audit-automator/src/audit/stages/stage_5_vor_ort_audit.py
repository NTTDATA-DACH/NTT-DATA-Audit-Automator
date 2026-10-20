# file: src/audit/stages/stage_5_vor_ort_audit.py
import json
import logging
import re
from typing import Dict, Any, List, Optional, Tuple
from google.cloud.exceptions import NotFound

from src.assets_loader import load_asset_json
from src.config import AppConfig
from src.clients.ai_client import AiClient
from src.clients.gcs_client import GcsClient
from src.clients.rag_client import RagClient
from src.audit.stages.control_catalog import ControlCatalog
from src.audit.stages.gs_extraction.anforderung_fields import (
    STATUS_ENTBEHRLICH,
    STATUS_JA,
    STATUS_NEIN,
    STATUS_TEILWEISE,
    normalize_status,
)
from src.audit.stages.gs_extraction.zielobjekt_index import ZielobjektIndex, normalize_key
from src.audit.stages.targeted_questions import finding_payload, list_requirement_ids, record_targeted_answer
from src.constants import EXTRACTED_CHECK_DATA_PATH, PROMPT_CONFIG_PATH, STAGE_RESULTS_PATH

# The auditor's verdict column, derived from the customer's own Umsetzungsstatus in the A.4.
BEWERTUNG_BY_STATUS = {
    STATUS_JA: "Umgesetzt",
    STATUS_NEIN: "Nicht umgesetzt",
    STATUS_TEILWEISE: "Teilweise umgesetzt",
    STATUS_ENTBEHRLICH: "Entbehrlich",
}

# A requirement ID quoted in a chapter-4 table cell ("RA.1.A3 Härtung ...", "siehe SYS.1.1.A5").
_REQUIREMENT_ID_RE = re.compile(r"\b([A-Z]{2,6}(?:\.\d+)+\.A\d+)\b")

class Chapter5Runner:
    """
    Handles generating content for Chapter 5 "Vor-Ort-Audit".
    It deterministically prepares the control checklist for the manual audit,
    enriching it with data extracted in prior stages. Everything it produces is derived
    from the Chapter-4 plan, the official catalog and the extracted Grundschutz-Check;
    the one model call (5.6.1, are the open additional measures in the A.6?) mirrors
    3.6.1 and is skipped when nothing is open or no Realisierungsplan is delivered.
    """
    STAGE_NAME = "Chapter-5"
    MAX_LISTED_REQUIREMENTS = 50

    def __init__(self, config: AppConfig, gcs_client: GcsClient, ai_client: Optional[AiClient] = None, rag_client: Optional[RagClient] = None):
        self.config = config
        self.gcs_client = gcs_client
        self.ai_client = ai_client
        self.rag_client = rag_client
        self.control_catalog = ControlCatalog()
        self.prompt_config = load_asset_json(PROMPT_CONFIG_PATH)
        logging.info(f"Initialized runner for stage: {self.STAGE_NAME}")

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
            # The plan names Zielobjekte in the system map's spelling; the check may address the
            # same object under its canonical Kürzel ("Firewall FW-01" for the map's "FW-01").
            registry = data.get("zielobjekte")
            self._zielobjekt_index = ZielobjektIndex(registry) if registry else ZielobjektIndex.from_anforderungen(anforderungen_list)
            logging.info(f"Successfully loaded and mapped {len(lookup_map)} unique requirement-object pairs for Chapter 5.")
            return lookup_map
        except NotFound:
            logging.warning(f"Refined check data file '{EXTRACTED_CHECK_DATA_PATH}' not found. Checklist will not contain customer explanations. Please run the 'Grundschutz-Check-Extraction' stage first.")
            return {}
        except Exception as e:
            logging.error(f"Failed to load or parse refined check data: {e}", exc_info=True)
            return {}

    @staticmethod
    def _parse_baustein_id(baustein_cell: str) -> str:
        """Extracts the Baustein ID from a free-form Chapter-4 table cell.

        The AI writes cells like 'ISMS.1 Sicherheitsmanagement', but nothing in the
        schema enforces that separator, so 'ISMS.1: Titel' or 'ISMS.1 - Titel' occur
        too. Match the ID pattern rather than splitting on the first space.
        """
        match = re.match(r"\s*([A-Z]+(?:\.\d+)+)", baustein_cell)
        return match.group(1) if match else baustein_cell.split(" ")[0].strip(":-. ")

    @staticmethod
    def _controls_from_extracted_data(
        baustein_id: str,
        zielobjekt_kuerzel: str,
        extracted_data_map: Dict[Tuple[str, str], Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Reconstructs a custom Baustein's requirements from the extracted check data.

        Matches on the requirement ID prefix ('ORP.6.' for Baustein 'ORP.6'), which is
        the numbering convention the BSI mandates and customer catalogs follow.
        """
        if not zielobjekt_kuerzel:
            return []
        prefix = f"{baustein_id}."
        return [
            {"id": control_id, "titel": item.get("titel") or control_id, "level": "benutzerdefiniert"}
            for (control_id, kuerzel), item in sorted(extracted_data_map.items())
            if kuerzel == zielobjekt_kuerzel and control_id.startswith(prefix)
        ]

    NO_CHECK_ENTRY = "Keine spezifische Angabe für dieses Zielobjekt im Grundschutz-Check gefunden."

    def _resolve_plan_kuerzel(self, kuerzel: Optional[str], extracted_data_map: Dict[Tuple[str, str], Dict[str, Any]]) -> Optional[str]:
        """The Kürzel under which the extracted check knows the plan's Zielobjekt.

        A Kürzel the check uses as is stays. Otherwise the plan's spelling (a map Kürzel, a
        name, an alias) is resolved through the Zielobjekt index; one candidate is taken, several
        are left alone with a warning, since guessing would show another object's answers.
        """
        index = getattr(self, "_zielobjekt_index", None)
        if not kuerzel or index is None:
            return kuerzel
        known = {k for _, k in extracted_data_map}
        if kuerzel in known:
            return kuerzel
        candidates = index.resolve(kuerzel) & known
        if len(candidates) == 1:
            resolved = next(iter(candidates))
            logging.info(f"Audit plan Zielobjekt '{kuerzel}' is '{resolved}' in the Grundschutz-Check.")
            return resolved
        if candidates:
            logging.warning(f"Audit plan Zielobjekt '{kuerzel}' is ambiguous in the Grundschutz-Check ({sorted(candidates)}); no entry taken.")
        return kuerzel

    @staticmethod
    def _customer_documentation(extracted_details: Dict[str, Any], status: Optional[str]) -> str:
        """The A.4 entry as the auditor reads it: the Umsetzungsstatus as stated, then the
        Umsetzungserläuterung. 'Bewertung der Anforderung' is the auditor's own verdict, so
        the customer's status has to be visible here, not only mapped into that field."""
        if not extracted_details:
            return Chapter5Runner.NO_CHECK_ENTRY
        status_text = status or str(extracted_details.get("umsetzungsstatus") or "").strip() or "nicht angegeben"
        explanation = str(extracted_details.get("umsetzungserlaeuterung") or "").strip() or "(keine Umsetzungserläuterung im A.4)"
        return f"Umsetzungsstatus: {status_text}\n\n{explanation}"

    def _generate_control_checklist(self, chapter_4_data: Dict[str, Any], extracted_data_map: Dict[Tuple[str, str], Dict[str, Any]]) -> Dict[str, Any]:
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
            baustein_id = self._parse_baustein_id(baustein_id_full)

            # --- ROBUSTNESS FIX (Task H) ---
            # Directly get the name and Kürzel from the plan. No more fragile name-based lookups.
            zielobjekt_name_from_plan = baustein_plan_item.get("Zielobjekt-Name")
            planned_zielobjekt_kuerzel = self._resolve_plan_kuerzel(baustein_plan_item.get("Zielobjekt-Kürzel"), extracted_data_map)

            if not planned_zielobjekt_kuerzel:
                logging.warning(f"Could not find 'Zielobjekt-Kürzel' in audit plan for Baustein '{baustein_id}'. Specific details for its controls will be missing.")

            controls = self.control_catalog.get_controls_for_baustein_id(baustein_id)
            if not controls and not self.control_catalog.is_known_baustein(baustein_id):
                # An institution's own Baustein is not in the Kompendium. Its requirements
                # exist only in the customer's Grundschutz-Check, so build the rows from
                # there — emitting an empty subchapter would drop it from the audit.
                controls = self._controls_from_extracted_data(
                    baustein_id, planned_zielobjekt_kuerzel, extracted_data_map
                )
                logging.warning(
                    f"Baustein '{baustein_id}' is not in the official Kompendium; "
                    f"derived {len(controls)} requirement(s) from the extracted Grundschutz-Check."
                )

            anforderungen_list = []
            for control in controls:
                control_id = control.get("id", "N/A")
                # The lookup key is now robustly created using the Kürzel from the plan.
                lookup_key = (control_id, planned_zielobjekt_kuerzel) if planned_zielobjekt_kuerzel else None
                extracted_details = extracted_data_map.get(lookup_key, {})
                
                # Normalise before mapping: raw casing from the customer document would
                # otherwise be passed straight into the auditor's checklist.
                status = normalize_status(extracted_details.get("umsetzungsstatus"))
                customer_explanation = self._customer_documentation(extracted_details, status)
                final_bewertung_status = BEWERTUNG_BY_STATUS.get(status, "N/A")

                # The level suffix (B/S/H) is what the auditor needs to see next to the
                # requirement; the official Kompendium carries it in the heading, not the title.
                anforderung_text = control.get("titel", "N/A")
                if control.get("level"):
                    anforderung_text = f"{anforderung_text} ({control['level']})"

                anforderungen_list.append({
                    "nummer": control_id,
                    "anforderung": anforderung_text,
                    "bewertung": final_bewertung_status,
                    "dokuAntragsteller": customer_explanation,
                    "pruefmethode": { "D": False, "I": False, "C": False, "S": False, "A": False, "B": False },
                    "auditfeststellung": "",
                    "abweichungen": ""
                })

            if not anforderungen_list:
                # An empty subchapter reads as "nothing to audit here". Say why instead,
                # so the gap is visible in the report the auditor works from.
                logging.warning(f"No requirements could be listed for Baustein '{baustein_id_full}'.")
                anforderungen_list.append({
                    "nummer": baustein_id,
                    "anforderung": (
                        "Für diesen Baustein konnten keine Anforderungen ermittelt werden "
                        "(nicht im amtlichen Kompendium und nicht im Grundschutz-Check gefunden). "
                        "Die Prüfung ist manuell zu ergänzen."
                    ),
                    "bewertung": "N/A",
                    "dokuAntragsteller": "",
                    "pruefmethode": {"D": False, "I": False, "C": False, "S": False, "A": False, "B": False},
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
        
    # ------------------------------------------------------------------ 5.6 Risikoanalyse
    # The additional measures of the Risikoanalyse are consolidated into the Grundschutz-Check as
    # requirements of the institution's own Bausteine (BSI-Standard 200-3, Kap. 8), which is the
    # only way a requirement outside the official Kompendium gets into the A.4. So "zusätzliche
    # Maßnahme" in the A.4 means: a requirement the catalog does not know.

    def _zusaetzliche_massnahmen(self, extracted_data_map: Dict[Tuple[str, str], Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The A.4 entries outside the official Kompendium, in requirement order."""
        return [
            item for (control_id, _kuerzel), item in sorted(extracted_data_map.items())
            if not self.control_catalog.is_known_control(control_id)
        ]

    @staticmethod
    def _selected_measures(chapter_4_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows = chapter_4_data.get("auswahlMassnahmenAusRisikoanalyse", {}).get("rows", [])
        return [r for r in rows if isinstance(r, dict)]

    @staticmethod
    def _measure_label(item: Dict[str, Any]) -> str:
        status = normalize_status(item.get("umsetzungsstatus")) or str(item.get("umsetzungsstatus") or "ohne Status")
        return f"{item.get('id')} ({item.get('zielobjekt_kuerzel') or 'Zielobjekt unbekannt'}: {status})"

    def _match_measure_to_check(
        self,
        measure: Dict[str, Any],
        extracted_data_map: Dict[Tuple[str, str], Dict[str, Any]],
        zusaetzliche: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """The A.4 entry a chapter-4.1.5 measure refers to, or None.

        Chapter 4 names the measure in free text. A requirement ID quoted in the row wins;
        otherwise the measure's wording is compared with the titles of the additional
        requirements (equal, or one containing the other). Several candidates are narrowed by
        the row's Zielobjekt; what stays ambiguous is left unmatched, since the wrong entry
        would show the auditor another measure's status.
        """
        measure_text = f"{measure.get('Maßnahme') or ''} {measure.get('Risikoanalyse') or ''}"
        quoted_ids = set(_REQUIREMENT_ID_RE.findall(measure_text))
        if quoted_ids:
            candidates = [item for (control_id, _k), item in sorted(extracted_data_map.items()) if control_id in quoted_ids]
        else:
            wording = normalize_key(measure.get("Maßnahme"))
            candidates = []
            if len(wording) >= 6:
                for item in zusaetzliche:
                    title = normalize_key(item.get("titel"))
                    if len(title) >= 6 and (title == wording or title in wording or wording in title):
                        candidates.append(item)
        if len(candidates) > 1:
            index = getattr(self, "_zielobjekt_index", None)
            zielobjekt = measure.get("Zielobjekt") or ""
            known = {normalize_key(zielobjekt)} | (index.resolve(zielobjekt) if index else set())
            narrowed = [c for c in candidates if c.get("zielobjekt_kuerzel") in known or normalize_key(c.get("zielobjekt_kuerzel")) in known]
            if narrowed:
                candidates = narrowed
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            logging.warning(f"5.6.2: measure '{measure.get('Maßnahme')}' matches {len(candidates)} A.4 entries ({sorted({c.get('id') for c in candidates})}); none taken.")
        return None

    def _generate_risikoanalyse_checklist(
        self, chapter_4_data: Dict[str, Any], extracted_data_map: Dict[Tuple[str, str], Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Generates the checklist for risk analysis measures (5.6.2) based on
        the measures selected in Chapter 4.1.5, with the customer's Umsetzungsstatus
        and Umsetzungserläuterung from the A.4 where the measure can be found there.
        """
        name = "risikoanalyseA5"
        logging.info(f"Deterministically generating checklist for {name} (5.6.2)...")

        selected_measures = self._selected_measures(chapter_4_data)
        if not selected_measures:
            logging.warning("No measures from risk analysis found in Chapter 4 results. Checklist for 5.6.2 will be empty.")
            return {name: {"einzelergebnisseDerRisikoanalyse": {"massnahmenPruefungen": []}}}

        zusaetzliche = self._zusaetzliche_massnahmen(extracted_data_map)
        massnahmen_pruefungen_list = []
        matched = 0
        for measure in selected_measures:
            entry = self._match_measure_to_check(measure, extracted_data_map, zusaetzliche)
            if entry:
                matched += 1
                status = normalize_status(entry.get("umsetzungsstatus"))
                bewertung = BEWERTUNG_BY_STATUS.get(status, "N/A")
                doku = (
                    f"Anforderung {entry.get('id')} im Grundschutz-Check (Zielobjekt {entry.get('zielobjekt_kuerzel') or 'unbekannt'}): "
                    f"{entry.get('titel') or ''}\n{self._customer_documentation(entry, status)}"
                )
            else:
                bewertung = ""
                doku = "Keine Angabe zu dieser Maßnahme im Grundschutz-Check (A.4) gefunden."
            massnahmen_pruefungen_list.append({
                "massnahme": measure.get("Maßnahme", "N/A"),
                "zielobjekt": measure.get("Zielobjekt", "N/A"),
                "bewertung": bewertung,
                "dokuAntragsteller": doku,
                "pruefmethode": { "D": False, "I": False, "C": False, "S": False, "A": False, "B": False },
                "auditfeststellung": "",
                "abweichungen": ""
            })

        logging.info(f"Generated checklist with {len(massnahmen_pruefungen_list)} risk analysis measures; {matched} found in the A.4.")
        return {name: {"einzelergebnisseDerRisikoanalyse": {"massnahmenPruefungen": massnahmen_pruefungen_list}}}

    async def _generate_risikoanalyse_zusammenfassung(
        self, chapter_4_data: Dict[str, Any], extracted_data_map: Dict[Tuple[str, str], Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Answers of 5.6.1 'Zusammenfassung der Risikoanalyse', in the order of the Muster:

        1. Zielobjekte with Schutzbedarf 'hoch'/'sehr hoch'? Evidence, not judgment: a
           Risikoanalyse with additional measures exists only for such Zielobjekte, so measures
           in the audit plan (4.1.5) or additional requirements in the A.4 answer it with yes.
           Without either the question stays open for the auditor.
        2. Does the stated status match reality? The on-site verdict; left to the auditor.
        3. All additional measures marked as implemented? Computed from the A.4.
        4. Are the open ones in the A.6? A targeted model call over the Realisierungsplan,
           as in 3.6.1; no call without open measures or without the document.
        """
        name = "zusammenfassungDerRisikoanalyse"
        logging.info(f"Answering 5.6.1 '{name}' from the audit plan and the extracted Grundschutz-Check...")
        selected_measures = self._selected_measures(chapter_4_data)
        zusaetzliche = self._zusaetzliche_massnahmen(extracted_data_map)
        answers: List[Optional[bool]] = [None] * 4
        findings: List[Dict[str, Any]] = []

        # Q1: Schutzbedarf hoch / sehr hoch vorhanden?
        if selected_measures or zusaetzliche:
            answers[0] = True
        else:
            logging.warning("5.6.1-Q1: neither measures in 4.1.5 nor additional requirements in the A.4; the Schutzbedarf question is left to the auditor.")

        # Q3: alle zusätzlichen Maßnahmen umgesetzt?
        if zusaetzliche:
            not_implemented = [a for a in zusaetzliche if normalize_status(a.get("umsetzungsstatus")) != STATUS_JA]
            answers[2] = not not_implemented
            logging.info(f"5.6.1-Q3: {len(zusaetzliche)} additional requirements in the A.4, {len(not_implemented)} not 'Ja'; no model call.")
            if not_implemented:
                shown = list_requirement_ids([self._measure_label(a) for a in not_implemented], self.MAX_LISTED_REQUIREMENTS)
                findings.append({
                    "category": "AG",
                    "description": (
                        f"{len(not_implemented)} zusätzliche Maßnahmen aus der Risikoanalyse sind im IT-Grundschutz-Check "
                        f"nicht als umgesetzt gekennzeichnet: {shown}."
                    ),
                })
        elif selected_measures:
            answers[2] = False
            findings.append({
                "category": "AG",
                "description": (
                    f"Die Risikoanalyse legt zusätzliche Maßnahmen fest ({len(selected_measures)} davon im Prüfplan 4.1.5), "
                    "der IT-Grundschutz-Check (A.4) enthält jedoch keine Anforderungen außerhalb des Kompendiums. "
                    "Der Umsetzungsstatus der zusätzlichen Maßnahmen ist damit im A.4 nicht nachvollziehbar."
                ),
            })
        else:
            logging.info("5.6.1-Q3: no additional measures known; nothing to assess.")

        # Q4: offene zusätzliche Maßnahmen im A.6?
        unmet = [a for a in zusaetzliche if normalize_status(a.get("umsetzungsstatus")) in (STATUS_NEIN, STATUS_TEILWEISE)]
        realisierungsplan_uris = self.rag_client.get_gcs_uris_for_categories(["Realisierungsplan"]) if self.rag_client else []
        if unmet and realisierungsplan_uris and self.ai_client:
            prompt = self.prompt_config["stages"]["Chapter-3"]["targeted_question"]["prompt"].format(
                question=self.prompt_config["stages"]["Chapter-5"]["questions"]["zusaetzliche_massnahmen_nicht_umgesetzt"],
                json_data=json.dumps(unmet, indent=2, ensure_ascii=False),
            )
            res = await self.ai_client.generate_checked_json_response(
                prompt, load_asset_json("assets/schemas/generic_1_question_schema.json"),
                gcs_uris=realisierungsplan_uris, request_context_log="5.6.1-Q4"
            )
            record_targeted_answer(res, answers, 3, findings, "5.6.1-Q4 (zusätzliche Maßnahmen nicht umgesetzt)")
        else:
            answers[3] = not unmet
            if unmet:
                findings.append({
                    "category": "AG",
                    "description": (
                        f"{len(unmet)} zusätzliche Maßnahmen aus der Risikoanalyse sind nicht oder teilweise umgesetzt, "
                        "aber der Realisierungsplan (A.6) wurde nicht gefunden, um ihre Dokumentation zu überprüfen."
                    ),
                })

        return {name: {"answers": answers, **finding_payload(findings, "Alle Prüfungen zu den zusätzlichen Maßnahmen der Risikoanalyse waren erfolgreich.")}}

    async def run(self, force_overwrite: bool = False) -> dict:
        """
        Executes the generation logic for Chapter 5.
        """
        logging.info(f"Executing stage: {self.STAGE_NAME}")
        
        # Load all dependencies first. Read via the same constant the controller writes
        # with, so a non-default OUTPUT_PREFIX cannot split the read and write paths.
        try:
            chapter_4_data = self.gcs_client.read_json(STAGE_RESULTS_PATH.format(stage_name="Chapter-4"))
            logging.info("Successfully loaded dependency: Chapter 4 results.")
        except Exception as e:
            logging.error(f"Could not load Chapter 4 results, which are required for Chapter 5. Aborting stage. Error: {e}")
            raise

        extracted_check_data_map = self._load_extracted_check_data()

        checklist_result = self._generate_control_checklist(chapter_4_data, extracted_check_data_map)
        risiko_result = self._generate_risikoanalyse_checklist(chapter_4_data, extracted_check_data_map)
        zusammenfassung = await self._generate_risikoanalyse_zusammenfassung(chapter_4_data, extracted_check_data_map)
        risiko_result["risikoanalyseA5"].update(zusammenfassung)

        final_result = {**checklist_result, **risiko_result}
        
        logging.info(f"Successfully prepared data for stage {self.STAGE_NAME}")
        return final_result