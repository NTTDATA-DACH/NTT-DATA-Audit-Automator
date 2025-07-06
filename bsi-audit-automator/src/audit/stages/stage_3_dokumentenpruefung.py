# src/audit/stages/stage_3_dokumentenpruefung.py
import logging
import json
import asyncio
from typing import Dict, Any, List, Tuple

from src.config import AppConfig
from src.clients.ai_client import AiClient
from src.clients.rag_client import RagClient

class Chapter3Runner:
    """
    Handles generating content for Chapter 3 "Dokumentenprüfung" by dynamically
    parsing the master report template.
    """
    STAGE_NAME = "Chapter-3"
    TEMPLATE_PATH = "assets/json/master_report_template.json"

    _RAG_METADATA_MAP = {
        # FIX: aktualitaetDerReferenzdokumente should search ALL documents, so no category filter.
        "aktualitaetDerReferenzdokumente": {"source_categories": None},
        "sicherheitsleitlinieUndRichtlinienInA0": {"source_categories": ["Sicherheitsleitlinie", "Organisations-Richtlinie"]},
        "definitionDesInformationsverbundes": {"source_categories": ["Informationsverbund", "Strukturanalyse"]},
        "bereinigterNetzplan": {"source_categories": ["Netzplan", "Strukturanalyse"]},
        "listeDerGeschaeftsprozesse": {"source_categories": ["Strukturanalyse"]},
        "listeDerAnwendungen": {"source_categories": ["Strukturanalyse"]},
        "listeDerItSysteme": {"source_categories": ["Strukturanalyse"]},
        "listeDerRaeumeGebaeudeStandorte": {"source_categories": ["Strukturanalyse"]},
        "listeDerKommunikationsverbindungen": {"source_categories": ["Strukturanalyse"]},
        "stichprobenDokuStrukturanalyse": {"rag_queries": ["Erstelle eine Stichprobendokumentation der Strukturanalyse."], "source_categories": ["Strukturanalyse"]},
        "listeDerDienstleister": {"source_categories": ["Strukturanalyse", "Dienstleister-Liste"]},
        "definitionDerSchutzbedarfskategorien": {"source_categories": ["Schutzbedarfsfeststellung"]},
        "schutzbedarfGeschaeftsprozesse": {"source_categories": ["Schutzbedarfsfeststellung"]},
        "schutzbedarfAnwendungen": {"source_categories": ["Schutzbedarfsfeststellung"]},
        "schutzbedarfItSysteme": {"source_categories": ["Schutzbedarfsfeststellung"]},
        "schutzbedarfRaeume": {"source_categories": ["Schutzbedarfsfeststellung"]},
        "schutzbedarfKommunikationsverbindungen": {"source_categories": ["Schutzbedarfsfeststellung"]},
        "stichprobenDokuSchutzbedarf": {"rag_queries": ["Führe eine Stichprobenprüfung der Schutzbedarfsfeststellung durch."], "source_categories": ["Strukturanalyse", "Schutzbedarfsfeststellung"]},
        "modellierungsdetails": {"source_categories": ["Modellierung", "Grundschutz-Check"]},
        "detailsZumItGrundschutzCheck": {"source_categories": ["Grundschutz-Check", "Realisierungsplan"]},
        "benutzerdefinierteBausteine": {"source_categories": ["Grundschutz-Check", "Modellierung"]},
        "risikoanalyse": {"source_categories": ["Risikoanalyse"]},
        "realisierungsplan": {"source_categories": ["Realisierungsplan"]},
    }

    def __init__(self, config: AppConfig, ai_client: AiClient, rag_client: RagClient):
        self.config = config
        self.ai_client = ai_client
        self.rag_client = rag_client
        self.execution_plan = self._build_execution_plan_from_template()
        # NEW: Load the document map once for business logic checks
        self._doc_map = self.rag_client._document_category_map
        logging.info(f"Initialized runner for stage: {self.STAGE_NAME} with dynamic execution plan.")

    def _load_asset_text(self, path: str) -> str:
        with open(path, 'r', encoding='utf-8') as f: return f.read()

    def _load_asset_json(self, path: str) -> dict:
        with open(path, 'r', encoding='utf-8') as f: return json.load(f)

    def _check_document_coverage(self) -> Dict[str, Any]:
        """
        Checks if all critical BSI document types are present.
        Returns a finding if any are missing.
        """
        REQUIRED_CATEGORIES = {
            "Sicherheitsleitlinie", "Strukturanalyse", "Schutzbedarfsfeststellung",
            "Modellierung", "Grundschutz-Check", "Risikoanalyse", "Realisierungsplan"
        }
        present_categories = set(self._doc_map.keys())
        missing_categories = REQUIRED_CATEGORIES - present_categories

        if not missing_categories:
            return {"category": "OK", "description": "Alle kritischen Dokumententypen sind vorhanden."}
        else:
            desc = f"Die folgenden kritischen Dokumententypen wurden in den eingereichten Unterlagen nicht gefunden oder klassifiziert: {', '.join(sorted(list(missing_categories)))}. Dies stellt eine schwerwiegende Abweichung dar, da die grundlegende Dokumentation für das Audit unvollständig ist."
            logging.warning(f"Document coverage check failed. Missing: {missing_categories}")
            return {"category": "AS", "description": desc}

    def _check_richtlinien_coverage(self) -> Dict[str, Any]:
        """
        Checks for the 5 required policies (A.0.1 - A.0.5).
        Returns a finding if any are missing.
        """
        REQUIRED_RICHTLINIEN = {
            "A.0.1 Leitlinie zur Informationssicherheit": ["Sicherheitsleitlinie"],
            "A.0.2 Richtlinie zur Risikoanalyse": ["Risikoanalyse"],
            "A.0.3 Richtlinie zur Lenkung von Dokumenten und Aufzeichnungen": ["Organisations-Richtlinie"],
            "A.0.4 Richtlinie zur internen ISMS-Auditierung": ["Organisations-Richtlinie"],
            "A.0.5 Richtlinie zur Lenkung von Korrektur- und Vorbeugungsmaßnahmen": ["Organisations-Richtlinie"],
        }
        
        missing_richtlinien = []
        for policy, categories in REQUIRED_RICHTLINIEN.items():
            found = any(cat in self._doc_map for cat in categories)
            if not found:
                # Fallback: Use RAG to search for the policy name in the Org-Richtlinien
                logging.info(f"Policy document for '{policy}' not found by category. Searching content...")
                context = self.rag_client.get_context_for_query(
                    queries=[f"Suche nach der Richtlinie '{policy}'"],
                    source_categories=["Sicherheitsleitlinie", "Organisations-Richtlinie"]
                )
                if "No highly relevant context found" in context:
                     missing_richtlinien.append(policy)
        
        if not missing_richtlinien:
            return {"category": "OK", "description": "Alle geforderten Richtlinien (A.0.1-A.0.5) scheinen vorhanden oder in den übergeordneten Dokumenten enthalten zu sein."}
        else:
            desc = f"Die folgenden geforderten Richtlinien konnten weder als eigenständiges Dokument noch inhaltlich in den übergeordneten Richtlinien gefunden werden: {', '.join(missing_richtlinien)}."
            logging.warning(f"Richtlinien check failed. Missing: {missing_richtlinien}")
            return {"category": "AG", "description": desc}

    def _build_execution_plan_from_template(self) -> List[Dict[str, Any]]:
        """
        Parses the master_report_template.json to build a dynamic list of
        tasks for Chapter 3, establishing the template as the single source of truth.
        """
        plan = []
        template = self._load_asset_json(self.TEMPLATE_PATH)
        ch3_template = template.get("bsiAuditReport", {}).get("dokumentenpruefung", {})
        subchapter_keys = sorted(ch3_template.keys())

        for subchapter_key in subchapter_keys:
            subchapter_data = ch3_template[subchapter_key]
            if not isinstance(subchapter_data, dict): continue
            
            section_keys = sorted(subchapter_data.keys())
            for section_key in section_keys:
                section_data = subchapter_data[section_key]
                if not isinstance(section_data, dict): continue

                task = self._create_task_from_section(section_key, section_data)
                if task:
                    plan.append(task)
        return plan

    def _create_task_from_section(self, key: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Creates a single task dictionary for the execution plan."""
        content = data.get("content", [])
        if not content: return None

        task = {"key": key}
        if any("Votum" in item.get("label", "") for item in content if item.get("type") == "prose"):
            task["type"] = "summary"
            task["prompt_path"] = "assets/prompts/generic_summary_prompt.txt"
            task["schema_path"] = "assets/schemas/generic_summary_schema.json"
            task["summary_topic"] = data.get("title", key)
            return task

        questions = [item["questionText"] for item in content if item.get("type") == "question"]
        if not questions: return None
        
        task["type"] = "rag"
        task["questions_formatted"] = "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))
        task["prompt_path"] = "assets/prompts/generic_question_prompt.txt"
        
        metadata = self._RAG_METADATA_MAP.get(key, {})
        task["rag_queries"] = metadata.get("rag_queries", questions)
        task["source_categories"] = metadata.get("source_categories")

        num_questions = len(questions)
        if num_questions == 1: task["schema_path"] = "assets/schemas/generic_1_question_schema.json"
        elif num_questions == 2: task["schema_path"] = "assets/schemas/generic_2_question_schema.json"
        elif num_questions == 3: task["schema_path"] = "assets/schemas/generic_3_question_schema.json"
        elif num_questions == 4: task["schema_path"] = "assets/schemas/stage_3_7_risikoanalyse_schema.json"
        elif num_questions == 5: task["schema_path"] = "assets/schemas/generic_5_question_schema.json"
        else:
            logging.error(f"No generic schema for {num_questions} questions in section '{key}'.")
            return None

        if "stichproben" in key.lower():
            task["prompt_path"] = f"assets/prompts/stage_3_{data['subchapterNumber'].replace('.', '_')}_{key}.txt"
            task["schema_path"] = f"assets/schemas/stage_3_{data['subchapterNumber'].replace('.', '_')}_{key}_schema.json"

        return task

    async def _process_rag_subchapter(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Generates content for a single RAG-based subchapter."""
        key = task["key"]
        logging.info(f"Starting RAG generation for subchapter: {key}")
        
        prompt_template_str = self._load_asset_text(task["prompt_path"])
        schema = self._load_asset_json(task["schema_path"])

        context_evidence = self.rag_client.get_context_for_query(
            queries=task["rag_queries"],
            source_categories=task.get("source_categories")
        )
        
        prompt = prompt_template_str.format(
            context=context_evidence, 
            questions=task["questions_formatted"]
        )

        try:
            generated_data = await self.ai_client.generate_json_response(prompt, schema)
            
            # Inject findings from business logic checks into specific sections
            if key == "aktualitaetDerReferenzdokumente":
                coverage_finding = self._check_document_coverage()
                if coverage_finding['category'] != 'OK':
                    generated_data['finding'] = coverage_finding
            elif key == "sicherheitsleitlinieUndRichtlinienInA0":
                 richtlinien_finding = self._check_richtlinien_coverage()
                 if richtlinien_finding['category'] != 'OK':
                    generated_data['finding'] = richtlinien_finding

            return {key: generated_data}
        except Exception as e:
            logging.error(f"Failed to generate data for subchapter {key}: {e}", exc_info=True)
            return {key: {"error": str(e)}}

    async def _process_summary_subchapter(self, task: Dict[str, Any], previous_findings: str) -> Dict[str, Any]:
        """Generates a summary/verdict for a subchapter."""
        key = task["key"]
        if self.config.is_test_mode:
            logging.info(f"Starting summary generation for subchapter: {key}")

        prompt_template_str = self._load_asset_text(task["prompt_path"])
        schema = self._load_asset_json(task["schema_path"])
        
        prompt = prompt_template_str.format(
            summary_topic=task["summary_topic"],
            previous_findings=previous_findings
        )

        try:
            generated_data = await self.ai_client.generate_json_response(prompt, schema)
            return {key: generated_data}
        except Exception as e:
            logging.error(f"Failed to generate summary for subchapter {key}: {e}", exc_info=True)
            return {key: {"error": str(e)}}

    def _get_findings_from_results(self, results_list: List[Dict]) -> str:
        """Extracts and formats findings from a list of results for summary prompts."""
        findings_for_summary = []
        for res_dict in results_list:
            if not res_dict: continue
            key = list(res_dict.keys())[0]
            result_data = res_dict.get(key)
            
            if isinstance(result_data, dict) and isinstance(result_data.get('finding'), dict):
                finding = result_data['finding']
                category = finding.get('category')
                description = finding.get('description')
                if category and category != "OK":
                    findings_for_summary.append(f"- Finding from {key} [{category}]: {description}")
        
        return "\n".join(findings_for_summary) if findings_for_summary else "No specific findings or deviations were generated."

    async def run(self) -> dict:
        """Executes the dynamically generated plan for Chapter 3."""
        logging.info(f"Executing dynamically generated plan for stage: {self.STAGE_NAME}")
        
        aggregated_results = {}
        processed_rag_results = []
        
        rag_tasks = [task for task in self.execution_plan if task and task.get("type") == "rag"]
        summary_tasks = [task for task in self.execution_plan if task and task.get("type") == "summary"]

        if rag_tasks:
            logging.info(f"--- Processing {len(rag_tasks)} RAG subchapters ---")
            rag_coroutines = [self._process_rag_subchapter(task) for task in rag_tasks]
            processed_rag_results = await asyncio.gather(*rag_coroutines)
            
            for res in processed_rag_results:
                aggregated_results.update(res)

        if summary_tasks:
            logging.info(f"--- Processing {len(summary_tasks)} summary subchapters ---")
            all_findings_text = self._get_findings_from_results(processed_rag_results)
            
            summary_coroutines = [self._process_summary_subchapter(task, all_findings_text) for task in summary_tasks]
            summary_results_list = await asyncio.gather(*summary_coroutines)

            for res in summary_results_list:
                aggregated_results.update(res)

        logging.info(f"Successfully aggregated results for all of stage {self.STAGE_NAME}")
        return aggregated_results