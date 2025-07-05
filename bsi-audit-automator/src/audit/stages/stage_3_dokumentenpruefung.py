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
        """Loads definitions for subchapters, including their specific RAG queries and source categories."""
        # **NEW**: The queries are more like natural questions, and we specify 'source_categories'.
        return {
            "aktualitaetDerReferenzdokumente": {
                "key": "3.1",
                "prompt_path": "assets/prompts/stage_3_1_aktualitaet.txt",
                "schema_path": "assets/schemas/stage_3_1_aktualitaet_schema.json",
                "rag_query": "Überprüfe die Aktualität und Lenkung der Referenzdokumente. Wurden A.1 bis A.6 neu erstellt und A.4 innerhalb der letzten 12 Monate bewertet?",
                "source_categories": ["Grundschutz-Check", "Organisations-Richtlinie"]
            },
            "sicherheitsleitlinieUndRichtlinienInA0": {
                "key": "3.2",
                "prompt_path": "assets/prompts/stage_3_2_sicherheitsleitlinie.txt",
                "schema_path": "assets/schemas/stage_3_2_sicherheitsleitlinie_schema.json",
                "rag_query": "Analyse der Sicherheitsleitlinie: Ist sie angemessen, erfüllt sie ISMS.1-Anforderungen, und wird sie vom Management getragen und veröffentlicht?",
                "source_categories": ["Sicherheitsleitlinie", "Organisations-Richtlinie"]
            },
            "definitionDesInformationsverbundes": {
                "key": "3.3.1",
                "prompt_path": "assets/prompts/stage_3_3_1_informationsverbund.txt",
                "schema_path": "assets/schemas/stage_3_3_1_informationsverbund_schema.json",
                "rag_query": "Ist der Informationsverbund klar abgegrenzt und sind alle notwendigen Komponenten und Schnittstellen definiert?",
                "source_categories": ["Informationsverbund", "Strukturanalyse"]
            },
            "bereinigterNetzplan": {
                "key": "3.3.2",
                "prompt_path": "assets/prompts/stage_3_3_2_netzplan.txt",
                "schema_path": "assets/schemas/stage_3_3_2_netzplan_schema.json",
                "rag_query": "Liegt ein aktueller und vollständiger Netzplan vor und sind alle Komponenten korrekt bezeichnet?",
                "source_categories": ["Netzplan"]
            },
            "listeDerGeschaeftsprozesse": {
                "key": "3.3.3",
                "prompt_path": "assets/prompts/stage_3_3_3_geschaeftsprozesse.txt",
                "schema_path": "assets/schemas/stage_3_3_3_geschaeftsprozesse_schema.json",
                "rag_query": "Enthält die Liste der Geschäftsprozesse alle erforderlichen Informationen wie Bezeichnung, Verantwortlicher und benötigte Anwendungen?",
                "source_categories": ["Strukturanalyse"]
            },
            "definitionDerSchutzbedarfskategorien": {
                "key": "3.4.1",
                "prompt_path": "assets/prompts/stage_3_4_1_schutzbedarfskategorien.txt",
                "schema_path": "assets/schemas/stage_3_4_1_schutzbedarfskategorien_schema.json",
                "rag_query": "Ist die Definition der Schutzbedarfskategorien plausibel, angemessen und wurden mehr als drei Kategorien definiert?",
                "source_categories": ["Schutzbedarfsfeststellung"]
            },
            "modellierungsdetails": {
                "key": "3.5.1",
                "prompt_path": "assets/prompts/stage_3_5_1_modellierungsdetails.txt",
                "schema_path": "assets/schemas/stage_3_5_1_modellierungsdetails_schema.json",
                "rag_query": "Analyse der Modellierung: Wurden alle relevanten Bausteine auf alle Zielobjekte angewandt und Abweichungen plausibel begründet?",
                "source_categories": ["Modellierung", "Grundschutz-Check"]
            },
            "ergebnisDerModellierung": {
                "key": "3.5.2",
                "is_summary": True,
                "prompt_path": "assets/prompts/stage_3_5_2_ergebnis_modellierung.txt",
                "schema_path": "assets/schemas/stage_3_summary_schema.json"
            },
            "detailsZumItGrundschutzCheck": {
                "key": "3.6.1",
                "prompt_path": "assets/prompts/stage_3_6_1_grundschutz_check.txt",
                "schema_path": "assets/schemas/stage_3_6_1_grundschutz_check_schema.json",
                "rag_query": "Analyse des IT-Grundschutz-Checks: Wurde der Umsetzungsstatus für jede Anforderung erhoben und wurden alle MUSS-Anforderungen erfüllt?",
                "source_categories": ["Grundschutz-Check", "Realisierungsplan"]
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
        
        if definition.get("is_summary"):
            return {name: {}}

        prompt_template = self._load_asset_text(definition["prompt_path"])
        schema = self._load_asset_json(definition["schema_path"])

        # **NEW**: Pass the source categories to the RAG client for filtering.
        context_evidence = self.rag_client.get_context_for_query(
            query=definition["rag_query"],
            source_categories=definition.get("source_categories")
        )
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

        rag_definitions = {k: v for k, v in self.subchapter_definitions.items() if not v.get("is_summary")}
        summary_definitions = {k: v for k, v in self.subchapter_definitions.items() if v.get("is_summary")}

        rag_tasks = [self._process_rag_subchapter(name, definition) for name, definition in rag_definitions.items()]
        
        rag_results_list = await asyncio.gather(*rag_tasks)
        rag_results_list = [r for r in rag_results_list if r]

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
        
        summary_tasks = [
            self._process_summary_subchapter(name, definition, summary_text)
            for name, definition in summary_definitions.items()
        ]
        summary_results_list = await asyncio.gather(*summary_tasks)
        for res in summary_results_list: aggregated_results.update(res)

        logging.info(f"Successfully aggregated results for all of stage {self.STAGE_NAME}")
        return aggregated_results