# src/audit/stages/stage_3_dokumentenpruefung.py
import logging
import json
import asyncio
from typing import Dict, Any

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
        """Loads definitions for subchapters, including their specific RAG queries."""
        return {
            "aktualitaetDerReferenzdokumente": {
                "key": "3.1",
                "prompt_path": "assets/prompts/stage_3_1_aktualitaet.txt",
                "schema_path": "assets/schemas/stage_3_1_aktualitaet_schema.json",
                "rag_query": "Richtlinien zur Lenkung von Dokumenten, Überarbeitung und Aktualität der Referenzdokumente A.0. Status und Datum der letzten Bewertung des IT-Grundschutz-Checks A.4."
            },
            "sicherheitsleitlinieUndRichtlinienInA0": {
                "key": "3.2",
                "prompt_path": "assets/prompts/stage_3_2_sicherheitsleitlinie.txt",
                "schema_path": "assets/schemas/stage_3_2_sicherheitsleitlinie_schema.json",
                "rag_query": "Inhalt und Angemessenheit der Leitlinie zur Informationssicherheit A.0.1, Abdeckung der Anforderungen aus ISMS.1, Konsistenz der Sicherheitsziele in den Dokumenten A.0.2 bis A.0.5, und Nachweis der Genehmigung und Veröffentlichung durch das Management."
            },
            "definitionDesInformationsverbundes": {
                "key": "3.3.1",
                "prompt_path": "assets/prompts/stage_3_3_1_informationsverbund.txt",
                "schema_path": "assets/schemas/stage_3_3_1_informationsverbund_schema.json",
                "rag_query": "Definition und Abgrenzung des Informationsverbunds; enthaltene infrastrukturelle, organisatorische, personelle und technische Komponenten; Definition der Schnittstellen zu externen Prozessen und Systemen."
            },
            "bereinigterNetzplan": {
                "key": "3.3.2",
                "prompt_path": "assets/prompts/stage_3_3_2_netzplan.txt",
                "schema_path": "assets/schemas/stage_3_3_2_netzplan_schema.json",
                "rag_query": "Dokumentation zum Netzwerkplan, Netzpläne, Topologie, Netzwerkkomponenten und deren Bezeichner."
            },
            "listeDerGeschaeftsprozesse": {
                "key": "3.3.3",
                "prompt_path": "assets/prompts/stage_3_3_3_geschaeftsprozesse.txt",
                "schema_path": "assets/schemas/stage_3_3_3_geschaeftsprozesse_schema.json",
                "rag_query": "Liste der Geschäftsprozesse, Prozessbeschreibungen, Prozessverantwortliche und beteiligte Anwendungen."
            },
            "definitionDerSchutzbedarfskategorien": {
                "key": "3.4.1",
                "prompt_path": "assets/prompts/stage_3_4_1_schutzbedarfskategorien.txt",
                "schema_path": "assets/schemas/stage_3_4_1_schutzbedarfskategorien_schema.json",
                "rag_query": "Dokumentation zur Definition der Schutzbedarfskategorien (normal, hoch, sehr hoch) und deren Kriterien."
            },
            "modellierungsdetails": {
                "key": "3.5.1",
                "prompt_path": "assets/prompts/stage_3_5_1_modellierungsdetails.txt",
                "schema_path": "assets/schemas/stage_3_5_1_modellierungsdetails_schema.json",
                "rag_query": "Dokumentation A.3 Modellierung, Zuordnung von Bausteinen zu Zielobjekten, Begründungen für nicht angewandte Bausteine, Umgang mit benutzerdefinierten Bausteinen."
            },
            "ergebnisDerModellierung": {
                "key": "3.5.2",
                "is_summary": True, # Flag for summary chapters
                "prompt_path": "assets/prompts/stage_3_5_2_ergebnis_modellierung.txt",
                "schema_path": "assets/schemas/stage_3_summary_schema.json"
            },
            "detailsZumItGrundschutzCheck": {
                "key": "3.6.1",
                "prompt_path": "assets/prompts/stage_3_6_1_grundschutz_check.txt",
                "schema_path": "assets/schemas/stage_3_6_1_grundschutz_check_schema.json",
                "rag_query": "Dokumentation A.4 IT-Grundschutz-Check, Umsetzungsstatus von Anforderungen, Begründungen für als 'entbehrlich' markierte Anforderungen, Erfüllung von MUSS-Anforderungen, Verweis auf Realisierungsplan A.6."
            },
            "ergebnisDerDokumentenpruefung": {
                "key": "3.9",
                "is_summary": True,
                "prompt_path": "assets/prompts/stage_3_9_ergebnis.txt",
                "schema_path": "assets/schemas/stage_3_9_ergebnis_schema.json"
            }
        }

    async def _process_rag_subchapter(self, name: str, definition: dict) -> Dict[str, Any]:
        """Generates content for a single subchapter using the RAG pipeline."""
        logging.info(f"Starting RAG generation for subchapter: {definition['key']} ({name})")
        
        # Handle summary chapters which don't have their own RAG query
        if definition.get("is_summary"):
            # This logic is now handled in the main run() method
            return {name: {}}

        prompt_template = self._load_asset_text(definition["prompt_path"])
        schema = self._load_asset_json(definition["schema_path"])

        context_evidence = self.rag_client.get_context_for_query(definition["rag_query"])
        prompt = prompt_template.format(context=context_evidence)

        try:
            generated_data = await self.ai_client.generate_json_response(prompt, schema)
            logging.info(f"Successfully generated data for subchapter {definition['key']}")
            return {name: generated_data}
        except Exception as e:
            logging.error(f"Failed to generate data for subchapter {definition['key']}: {e}", exc_info=True)
            return {name: None}

    async def _process_summary_subchapter(self, name: str, definition: dict, previous_findings: str) -> Dict[str, Any]:
        """Generates a summary/verdict for a subchapter based on previous findings."""
        key = definition['key']
        logging.info(f"Starting summary generation for subchapter: {key} ({name})")

        prompt_template = self._load_asset_text(definition["prompt_path"])
        schema = self._load_asset_json(definition["schema_path"])

        prompt = prompt_template.format(previous_findings=previous_findings)

        try:
            generated_data = await self.ai_client.generate_json_response(prompt, schema)
            logging.info(f"Successfully generated summary for subchapter {key}")
            return {name: generated_data}
        except Exception as e:
            logging.error(f"Failed to generate summary for subchapter {key} ({name}): {e}", exc_info=True)
            return {name: None}

    async def run(self) -> dict:
        """
        Executes the generation logic for all of Chapter 3.
        It runs RAG-based subchapters in parallel, then uses their
        findings to generate the final summary subchapter.
        """
        logging.info(f"Executing stage: {self.STAGE_NAME}")

        # Separate RAG tasks from summary tasks
        rag_definitions = {k: v for k, v in self.subchapter_definitions.items() if not v.get("is_summary")}
        summary_definitions = {k: v for k, v in self.subchapter_definitions.items() if v.get("is_summary")}

        # Run all standard RAG tasks in parallel
        rag_tasks = [self._process_rag_subchapter(name, definition) for name, definition in rag_definitions.items()]
        
        rag_results_list = await asyncio.gather(*rag_tasks)
        rag_results_list = [r for r in rag_results_list if r] # Filter out potential Nones

        aggregated_results = {}
        findings_for_summary = []
        for res_dict in rag_results_list:
            aggregated_results.update(res_dict)
            subchapter_name = list(res_dict.keys())[0]
            result_data = res_dict.get(subchapter_name)
            
            if isinstance(result_data, dict) and isinstance(result_data.get('finding'), dict):
                finding = result_data['finding']
                category = finding.get('category')
                description = finding.get('description')
                key = self.subchapter_definitions.get(subchapter_name, {}).get('key', 'N/A')
                if category and category != "OK":
                    findings_for_summary.append(f"- Finding from {key} ({subchapter_name}) [{category}]: {description}")

        summary_text = "\n".join(findings_for_summary) if findings_for_summary else "No specific findings or deviations were generated in the previous steps."
        
        # Now run summary tasks sequentially, feeding them the collected findings
        summary_tasks = [
            self._process_summary_subchapter(name, definition, summary_text)
            for name, definition in summary_definitions.items()
        ]
        summary_results_list = await asyncio.gather(*summary_tasks)
        for res in summary_results_list: aggregated_results.update(res)

        logging.info(f"Successfully aggregated results for all of stage {self.STAGE_NAME}")
        return aggregated_results