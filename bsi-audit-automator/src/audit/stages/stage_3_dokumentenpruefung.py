# src/audit/stages/stage_3_dokumentenpruefung.py
import logging
import json
import asyncio
from typing import Dict, Any, List

from src.config import AppConfig
from src.clients.ai_client import AiClient
from src.clients.rag_client import RagClient

class Chapter3Runner:
    """
    Handles generating content for Chapter 3 "Dokumentenprüfung".
    It processes each subchapter as a separate, RAG-driven task.
    """
    STAGE_NAME = "Chapter-3"
    
    def __init__(self, config: AppConfig, ai_client: AiClient, rag_client: RagClient):
        self.config = config
        self.ai_client = ai_client
        self.rag_client = rag_client
        self.subchapter_definitions = self._load_subchapter_definitions()
        logging.info(f"Initialized runner for stage: {self.STAGE_NAME}")

    def _load_asset_text(self, path: str) -> str:
        with open(path, 'r', encoding='utf-8') as f: return f.read()

    def _load_asset_json(self, path: str) -> dict:
        with open(path, 'r', encoding='utf-8') as f: return json.load(f)

    def _load_subchapter_definitions(self) -> Dict[str, Any]:
        """Loads definitions for subchapters, including their specific RAG queries and source categories."""
        # REFACTOR: Using generic prompts and schemas. Unique content (questions) is defined here.
        return {
            # 3.1 & 3.2
            "aktualitaetDerReferenzdokumente": {"key": "3.1", "prompt_path": "assets/prompts/stage_3_1_aktualitaet.txt", "schema_path": "assets/schemas/stage_3_1_aktualitaet_schema.json", "rag_query": "Überprüfe die Aktualität und Lenkung der Referenzdokumente.", "source_categories": ["Grundschutz-Check", "Organisations-Richtlinie"]},
            "sicherheitsleitlinieUndRichtlinienInA0": {"key": "3.2", "prompt_path": "assets/prompts/stage_3_2_sicherheitsleitlinie.txt", "schema_path": "assets/schemas/stage_3_2_sicherheitsleitlinie_schema.json", "rag_query": "Analyse der Sicherheitsleitlinie.", "source_categories": ["Sicherheitsleitlinie", "Organisations-Richtlinie"]},
            
            # 3.3 Strukturanalyse A.1
            "definitionDesInformationsverbundes": {"key": "3.3.1", "prompt_path": "assets/prompts/stage_3_3_1_informationsverbund.txt", "schema_path": "assets/schemas/stage_3_3_1_informationsverbund_schema.json", "rag_query": "Ist der Informationsverbund klar abgegrenzt?", "source_categories": ["Informationsverbund", "Strukturanalyse"]},
            "bereinigterNetzplan": {"key": "3.3.2", "prompt_path": "assets/prompts/stage_3_3_2_netzplan.txt", "schema_path": "assets/schemas/stage_3_3_2_netzplan_schema.json", "rag_query": "Liegt ein aktueller Netzplan vor?", "source_categories": ["Netzplan", "Strukturanalyse"]},
            "listeDerGeschaeftsprozesse": {"key": "3.3.3", "prompt_path": "assets/prompts/stage_3_3_3_geschaeftsprozesse.txt", "schema_path": "assets/schemas/stage_3_3_3_geschaeftsprozesse_schema.json", "rag_query": "Enthält die Liste der Geschäftsprozesse alle erforderlichen Informationen?", "source_categories": ["Strukturanalyse"]},
            "listeDerAnwendungen": {"key": "3.3.4", "prompt_path": "assets/prompts/generic_question_prompt.txt", "schema_path": "assets/schemas/generic_1_question_schema.json", "questions": "1. Enthält die Liste der Anwendungen alle benötigten Informationen (eindeutige Bezeichnung, Beschreibung, Plattform, Raum, Anzahl, Zuordnung zu IT-Systemen, Status, Benutzer, Verantwortlicher)?", "rag_query": "Analyse der Liste der Anwendungen auf Vollständigkeit.", "source_categories": ["Strukturanalyse"]},
            "listeDerItSysteme": {"key": "3.3.5", "prompt_path": "assets/prompts/generic_question_prompt.txt", "schema_path": "assets/schemas/generic_1_question_schema.json", "questions": "1. Enthält die Liste der IT-Systeme alle benötigten Informationen (eindeutige Bezeichnung, Beschreibung, Plattform, Anzahl, Aufstellung, Status, Benutzer, Verantwortlicher)?", "rag_query": "Analyse der Liste der IT-Systeme auf Vollständigkeit.", "source_categories": ["Strukturanalyse"]},
            "listeDerRaeumeGebaeudeStandorte": {"key": "3.3.6", "prompt_path": "assets/prompts/generic_question_prompt.txt", "schema_path": "assets/schemas/generic_1_question_schema.json", "questions": "1. Enthält die Liste der Räume, Gebäude und Standorte alle benötigten Informationen (eindeutige Bezeichnung, Beschreibung, Art, Anzahl, Status, Verantwortlicher)?", "rag_query": "Analyse der Liste der Räume, Gebäude und Standorte auf Vollständigkeit.", "source_categories": ["Strukturanalyse"]},
            "listeDerKommunikationsverbindungen": {"key": "3.3.7", "prompt_path": "assets/prompts/generic_question_prompt.txt", "schema_path": "assets/schemas/generic_1_question_schema.json", "questions": "1. Enthält die Liste der Kommunikationsverbindungen alle benötigten Informationen und sind die Grenzen des Informationsverbundes dokumentiert?", "rag_query": "Analyse der Liste der Kommunikationsverbindungen.", "source_categories": ["Strukturanalyse"]},
            "stichprobenDokuStrukturanalyse": {"key": "3.3.8", "prompt_path": "assets/prompts/stage_3_3_8_stichproben_struktur.txt", "schema_path": "assets/schemas/stage_3_3_8_stichproben_struktur_schema.json", "rag_query": "Erstelle eine Stichprobendokumentation der Strukturanalyse.", "source_categories": ["Strukturanalyse"]},
            "listeDerDienstleister": {"key": "3.3.9", "prompt_path": "assets/prompts/generic_question_prompt.txt", "schema_path": "assets/schemas/generic_1_question_schema.json", "questions": "1. Liegt eine aktuelle und vollständige Liste aller externen Dienstleister vor, die Einfluss auf den Informationsverbund nehmen können?", "rag_query": "Liegt eine aktuelle Liste externer Dienstleister vor?", "source_categories": ["Strukturanalyse", "Dienstleister-Liste"]},
            "ergebnisDerStrukturanalyse": {"key": "3.3.10", "is_summary": True, "prompt_path": "assets/prompts/generic_summary_prompt.txt", "schema_path": "assets/schemas/generic_summary_schema.json", "summary_topic": "Strukturanalyse (A.1)"},
            
            # 3.4 Schutzbedarfsfeststellung A.2
            "definitionDerSchutzbedarfskategorien": {"key": "3.4.1", "prompt_path": "assets/prompts/stage_3_4_1_schutzbedarfskategorien.txt", "schema_path": "assets/schemas/stage_3_4_1_schutzbedarfskategorien_schema.json", "rag_query": "Ist die Definition der Schutzbedarfskategorien plausibel?", "source_categories": ["Schutzbedarfsfeststellung"]},
            "schutzbedarfGeschaeftsprozesse": {"key": "3.4.2", "prompt_path": "assets/prompts/generic_question_prompt.txt", "schema_path": "assets/schemas/generic_2_question_schema.json", "questions": "1. Ist der Schutzbedarf der Geschäftsprozesse vollständig dokumentiert?\n2. Ist der Schutzbedarf der Geschäftsprozesse nachvollziehbar begründet?", "rag_query": "Ist der Schutzbedarf der Geschäftsprozesse nachvollziehbar dokumentiert?", "source_categories": ["Schutzbedarfsfeststellung"]},
            "schutzbedarfAnwendungen": {"key": "3.4.3", "prompt_path": "assets/prompts/generic_question_prompt.txt", "schema_path": "assets/schemas/generic_2_question_schema.json", "questions": "1. Ist der Schutzbedarf der Anwendungen vollständig dokumentiert?\n2. Ist der Schutzbedarf der Anwendungen nachvollziehbar begründet?", "rag_query": "Ist der Schutzbedarf der Anwendungen nachvollziehbar dokumentiert?", "source_categories": ["Schutzbedarfsfeststellung"]},
            "schutzbedarfItSysteme": {"key": "3.4.4", "prompt_path": "assets/prompts/generic_question_prompt.txt", "schema_path": "assets/schemas/generic_2_question_schema.json", "questions": "1. Ist der Schutzbedarf der IT-Systeme vollständig dokumentiert?\n2. Ist der Schutzbedarf der IT-Systeme nachvollziehbar begründet?", "rag_query": "Ist der Schutzbedarf der IT-Systeme nachvollziehbar dokumentiert?", "source_categories": ["Schutzbedarfsfeststellung"]},
            "schutzbedarfRaeume": {"key": "3.4.5", "prompt_path": "assets/prompts/generic_question_prompt.txt", "schema_path": "assets/schemas/generic_2_question_schema.json", "questions": "1. Ist der Schutzbedarf der Räume, Gebäude und Standorte vollständig dokumentiert?\n2. Ist der Schutzbedarf der Räume, Gebäude und Standorte nachvollziehbar begründet?", "rag_query": "Ist der Schutzbedarf der Räume nachvollziehbar dokumentiert?", "source_categories": ["Schutzbedarfsfeststellung"]},
            "schutzbedarfKommunikationsverbindungen": {"key": "3.4.6", "prompt_path": "assets/prompts/generic_question_prompt.txt", "schema_path": "assets/schemas/generic_2_question_schema.json", "questions": "1. Ist der Schutzbedarf der Außenverbindungen und kritischen Kommunikationsverbindungen vollständig dokumentiert?\n2. Ist der Schutzbedarf der Kommunikationsverbindungen nachvollziehbar begründet?", "rag_query": "Ist der Schutzbedarf der Kommunikationsverbindungen nachvollziehbar?", "source_categories": ["Schutzbedarfsfeststellung"]},
            "stichprobenDokuSchutzbedarf": {"key": "3.4.7", "prompt_path": "assets/prompts/stage_3_4_7_stichproben_schutzbedarf.txt", "schema_path": "assets/schemas/stage_3_4_7_stichproben_schutzbedarf_schema.json", "rag_query": "Führe eine Stichprobenprüfung der Schutzbedarfsfeststellung durch.", "source_categories": ["Strukturanalyse", "Schutzbedarfsfeststellung"]},
            "ergebnisDerSchutzbedarfsfeststellung": {"key": "3.4.8", "is_summary": True, "prompt_path": "assets/prompts/generic_summary_prompt.txt", "schema_path": "assets/schemas/generic_summary_schema.json", "summary_topic": "Schutzbedarfsfeststellung (A.2)"},
            
            # 3.5 Modellierung
            "modellierungsdetails": {"key": "3.5.1", "prompt_path": "assets/prompts/stage_3_5_1_modellierungsdetails.txt", "schema_path": "assets/schemas/stage_3_5_1_modellierungsdetails_schema.json", "rag_query": "Analyse der Modellierung.", "source_categories": ["Modellierung", "Grundschutz-Check"]},
            "ergebnisDerModellierung": {"key": "3.5.2", "is_summary": True, "prompt_path": "assets/prompts/stage_3_5_2_ergebnis_modellierung.txt", "schema_path": "assets/schemas/generic_summary_schema.json", "summary_topic": "Modellierung (A.3)"},
            
            # 3.6 Grundschutz-Check
            "detailsZumItGrundschutzCheck": {"key": "3.6.1", "prompt_path": "assets/prompts/stage_3_6_1_grundschutz_check.txt", "schema_path": "assets/schemas/stage_3_6_1_grundschutz_check_schema.json", "rag_query": "Analyse des IT-Grundschutz-Checks.", "source_categories": ["Grundschutz-Check", "Realisierungsplan"]},
            "benutzerdefinierteBausteine": {"key": "3.6.2", "prompt_path": "assets/prompts/generic_question_prompt.txt", "schema_path": "assets/schemas/generic_2_question_schema.json", "questions": "1. Wurden benutzerdefinierte Bausteine erstellt und modelliert?\n2. Sind alle Anforderungen der Institution in den benutzerdefinierten Bausteinen enthalten?", "rag_query": "Gibt es benutzerdefinierte Bausteine?", "source_categories": ["Grundschutz-Check", "Modellierung"]},
            "ergebnisItGrundschutzCheck": {"key": "3.6.3", "is_summary": True, "prompt_path": "assets/prompts/generic_summary_prompt.txt", "schema_path": "assets/schemas/generic_summary_schema.json", "summary_topic": "IT-Grundschutz-Check (A.4)"},

            # 3.7 & 3.8
            "risikoanalyseA5": {"key": "3.7.1", "prompt_path": "assets/prompts/stage_3_7_risikoanalyse.txt", "schema_path": "assets/schemas/stage_3_7_risikoanalyse_schema.json", "rag_query": "Analyse der Risikoanalyse A.5.", "source_categories": ["Risikoanalyse"]},
            "ergebnisRisikoanalyse": {"key": "3.7.2", "is_summary": True, "prompt_path": "assets/prompts/generic_summary_prompt.txt", "schema_path": "assets/schemas/generic_summary_schema.json", "summary_topic": "Risikoanalyse (A.5)"},
            "realisierungsplanA6": {"key": "3.8.1", "prompt_path": "assets/prompts/stage_3_8_realisierungsplan.txt", "schema_path": "assets/schemas/stage_3_8_realisierungsplan_schema.json", "rag_query": "Analyse des Realisierungsplans A.6.", "source_categories": ["Realisierungsplan"]},
            "ergebnisRealisierungsplan": {"key": "3.8.2", "is_summary": True, "prompt_path": "assets/prompts/generic_summary_prompt.txt", "schema_path": "assets/schemas/generic_summary_schema.json", "summary_topic": "Realisierungsplan (A.6)"},
            
            # 3.9
            "ergebnisDerDokumentenpruefung": {"key": "3.9", "is_summary": True, "prompt_path": "assets/prompts/stage_3_9_ergebnis.txt", "schema_path": "assets/schemas/stage_3_9_ergebnis_schema.json"}
        }

    async def _process_rag_subchapter(self, name: str, definition: dict) -> Dict[str, Any]:
        """Generates content for a single subchapter using the RAG pipeline."""
        logging.info(f"Starting RAG generation for subchapter: {definition['key']} ({name})")
        
        prompt_template_str = self._load_asset_text(definition["prompt_path"])
        schema = self._load_asset_json(definition["schema_path"])

        context_evidence = self.rag_client.get_context_for_query(
            query=definition["rag_query"],
            source_categories=definition.get("source_categories")
        )
        
        # Format the prompt with context and specific questions if they exist
        format_args = {"context": context_evidence}
        if "questions" in definition:
            format_args["questions"] = definition["questions"]
        prompt = prompt_template_str.format(**format_args)

        try:
            generated_data = await self.ai_client.generate_json_response(prompt, schema)
            logging.info(f"Successfully generated data for subchapter {definition['key']}")
            return {name: generated_data}
        except Exception as e:
            logging.error(f"Failed to generate data for subchapter {definition['key']}: {e}", exc_info=True)
            return {name: {"error": str(e)}}

    async def _process_summary_subchapter(self, name: str, definition: dict, previous_findings: str) -> Dict[str, Any]:
        """Generates a summary/verdict for a subchapter based on previous findings."""
        key = definition['key']
        logging.info(f"Starting summary generation for subchapter: {key} ({name})")

        prompt_template_str = self._load_asset_text(definition["prompt_path"])
        schema = self._load_asset_json(definition["schema_path"])

        format_args = {"previous_findings": previous_findings}
        if "summary_topic" in definition:
            format_args["summary_topic"] = definition["summary_topic"]
        prompt = prompt_template_str.format(**format_args)

        try:
            generated_data = await self.ai_client.generate_json_response(prompt, schema)
            logging.info(f"Successfully generated summary for subchapter {key}")
            return {name: generated_data}
        except Exception as e:
            logging.error(f"Failed to generate summary for subchapter {key} ({name}): {e}", exc_info=True)
            return {name: {"error": str(e)}}

    def _get_findings_from_results(self, results_list: List[Dict], definitions: Dict) -> str:
        """Extracts and formats findings from a list of results for summary prompts."""
        findings_for_summary = []
        for res_dict in results_list:
            if not res_dict: continue
            subchapter_name = list(res_dict.keys())[0]
            result_data = res_dict.get(subchapter_name)
            
            if isinstance(result_data, dict) and isinstance(result_data.get('finding'), dict):
                finding = result_data['finding']
                category = finding.get('category')
                description = finding.get('description')
                key = definitions.get(subchapter_name, {}).get('key', 'N/A')
                if category and category != "OK":
                    findings_for_summary.append(f"- Finding from {key} ({subchapter_name}) [{category}]: {description}")
        
        return "\n".join(findings_for_summary) if findings_for_summary else "No specific findings or deviations were generated."

    async def run(self) -> dict:
        """
        Executes the generation logic for all of Chapter 3 by processing it in logical blocks.
        """
        logging.info(f"Executing stage: {self.STAGE_NAME} by processing in logical blocks.")
        
        aggregated_results = {}
        all_findings_text = []

        # Define the processing blocks based on the report structure
        blocks = {
            "Block_3_1_2": ["aktualitaetDerReferenzdokumente", "sicherheitsleitlinieUndRichtlinienInA0"],
            "Block_3_3": [k for k, v in self.subchapter_definitions.items() if v['key'].startswith('3.3.') and not v.get('is_summary')],
            "Summary_3_3": ["ergebnisDerStrukturanalyse"],
            "Block_3_4": [k for k, v in self.subchapter_definitions.items() if v['key'].startswith('3.4.') and not v.get('is_summary')],
            "Summary_3_4": ["ergebnisDerSchutzbedarfsfeststellung"],
            "Block_3_5": ["modellierungsdetails"],
            "Summary_3_5": ["ergebnisDerModellierung"],
            "Block_3_6": [k for k, v in self.subchapter_definitions.items() if v['key'].startswith('3.6.') and not v.get('is_summary')],
            "Summary_3_6": ["ergebnisItGrundschutzCheck"],
            "Block_3_7": ["risikoanalyseA5"],
            "Summary_3_7": ["ergebnisRisikoanalyse"],
            "Block_3_8": ["realisierungsplanA6"],
            "Summary_3_8": ["ergebnisRealisierungsplan"],
            "Summary_3_9": ["ergebnisDerDokumentenpruefung"],
        }
        
        for block_name, subchapter_keys in blocks.items():
            if not subchapter_keys: continue

            definitions_in_block = {k: self.subchapter_definitions[k] for k in subchapter_keys}
            
            if "Summary" not in block_name:
                # This is a RAG block
                logging.info(f"--- Processing RAG block: {block_name} ---")
                tasks = [self._process_rag_subchapter(name, definition) for name, definition in definitions_in_block.items()]
                results_list = await asyncio.gather(*tasks)
                
                # Aggregate results and findings from this block
                for res in results_list: aggregated_results.update(res)
                findings_text = self._get_findings_from_results(results_list, self.subchapter_definitions)
                if "No specific findings" not in findings_text:
                    all_findings_text.append(f"--- Findings from {block_name} ---\n{findings_text}")
            
            else:
                # This is a Summary block
                logging.info(f"--- Processing Summary block: {block_name} ---")
                
                # Determine which findings to use (all up to this point)
                summary_input_text = "\n\n".join(all_findings_text) if all_findings_text else "No specific findings from previous sections."
                if block_name == "Summary_3_9":
                    logging.info("Generating final summary for Chapter 3 using all collected findings.")
                
                tasks = [self._process_summary_subchapter(name, definition, summary_input_text) for name, definition in definitions_in_block.items()]
                results_list = await asyncio.gather(*tasks)
                for res in results_list: aggregated_results.update(res)


        logging.info(f"Successfully aggregated results for all of stage {self.STAGE_NAME}")
        return aggregated_results