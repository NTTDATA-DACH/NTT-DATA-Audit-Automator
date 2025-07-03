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
            }
        }

    async def _process_rag_subchapter(self, name: str, definition: dict) -> Dict[str, Any]:
        """Generates content for a single subchapter using the RAG pipeline."""
        logging.info(f"Starting RAG generation for subchapter: {definition['key']} ({name})")
        
        prompt_template = self._load_asset_text(definition["prompt_path"])
        schema = self._load_asset_json(definition["schema_path"])

        # 1. Use the RagClient to find relevant evidence
        context_evidence = self.rag_client.get_context_for_query(definition["rag_query"])
        
        # 2. Construct the final, context-rich prompt
        prompt = prompt_template.format(context=context_evidence)

        try:
            generated_data = await self.ai_client.generate_json_response(prompt, schema)
            logging.info(f"Successfully generated data for subchapter {definition['key']}")
            return {name: generated_data}
        except Exception as e:
            logging.error(f"Failed to generate data for subchapter {definition['key']}: {e}", exc_info=True)
            return {name: None}

    async def _process_summary_subchapter(self, previous_findings: str) -> Dict[str, Any]:
        """Generates the final verdict for 3.9 based on previous findings."""
        name = "ergebnisDerDokumentenpruefung"
        key = "3.9"
        logging.info(f"Starting summary generation for subchapter: {key} ({name})")

        prompt_template = self._load_asset_text("assets/prompts/stage_3_9_ergebnis.txt")
        schema = self._load_asset_json("assets/schemas/stage_3_9_ergebnis_schema.json")

        prompt = prompt_template.format(previous_findings=previous_findings)

        try:
            generated_data = await self.ai_client.generate_json_response(prompt, schema)
            logging.info(f"Successfully generated summary for subchapter {key}")
            return {name: generated_data}
        except Exception as e:
            logging.error(f"Failed to generate summary for subchapter {key}: {e}", exc_info=True)
            return {name: None}

    async def run(self) -> dict:
        """
        Executes the generation logic for all of Chapter 3.
        It runs RAG-based subchapters in parallel, then uses their
        findings to generate the final summary subchapter.
        """
        logging.info(f"Executing stage: {self.STAGE_NAME}")

        # 1. Run all RAG-based analysis tasks concurrently
        rag_tasks = []
        for name, definition in self.subchapter_definitions.items():
            rag_tasks.append(self._process_rag_subchapter(name, definition))
        
        rag_results_list = await asyncio.gather(*rag_tasks)

        # 2. Aggregate results and extract findings for the summary step
        aggregated_results = {}
        findings_for_summary = []
        for i, res_dict in enumerate(rag_results_list):
            aggregated_results.update(res_dict)
            # Extract findingText for summary prompt
            subchapter_name = list(res_dict.keys())[0]
            result_data = res_dict.get(subchapter_name)
            if result_data and "findingText" in result_data:
                findings_for_summary.append(f"- Finding from {subchapter_name}: {result_data['findingText']}")

        # 3. Run the summary task using the aggregated findings
        summary_text = "\n".join(findings_for_summary) if findings_for_summary else "No specific findings were generated in the previous steps."
        summary_result = await self._process_summary_subchapter(summary_text)
        aggregated_results.update(summary_result)
            
        logging.info(f"Successfully aggregated results for all of stage {self.STAGE_NAME}")
        return aggregated_results