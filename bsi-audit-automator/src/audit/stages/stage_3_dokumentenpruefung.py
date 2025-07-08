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

    _DOC_CATEGORY_MAP = {
        "aktualitaetDerReferenzdokumente": {"source_categories": None},
        "sicherheitsleitlinieUndRichtlinienInA0": {"source_categories": ["Sicherheitsleitlinie", "Organisations-Richtlinie"]},
        "definitionDesInformationsverbundes": {"source_categories": ["Informationsverbund", "Strukturanalyse"]},
        "bereinigterNetzplan": {"source_categories": ["Netzplan", "Strukturanalyse"]},
        "listeDerGeschaeftsprozesse": {"source_categories": ["Strukturanalyse"]},
        "listeDerAnwendungen": {"source_categories": ["Strukturanalyse"]},
        "listeDerItSysteme": {"source_categories": ["Strukturanalyse"]},
        "listeDerRaeumeGebaeudeStandorte": {"source_categories": ["Strukturanalyse"]},
        "listeDerKommunikationsverbindungen": {"source_categories": ["Strukturanalyse"]},
        "stichprobenDokuStrukturanalyse": {"source_categories": ["Strukturanalyse"]},
        "listeDerDienstleister": {"source_categories": ["Strukturanalyse", "Dienstleister-Liste"]},
        "definitionDerSchutzbedarfskategorien": {"source_categories": ["Schutzbedarfsfeststellung"]},
        "schutzbedarfGeschaeftsprozesse": {"source_categories": ["Schutzbedarfsfeststellung"]},
        "schutzbedarfAnwendungen": {"source_categories": ["Schutzbedarfsfeststellung"]},
        "schutzbedarfItSysteme": {"source_categories": ["Schutzbedarfsfeststellung"]},
        "schutzbedarfRaeume": {"source_categories": ["Schutzbedarfsfeststellung"]},
        "schutzbedarfKommunikationsverbindungen": {"source_categories": ["Schutzbedarfsfeststellung"]},
        "stichprobenDokuSchutzbedarf": {"source_categories": ["Strukturanalyse", "Schutzbedarfsfeststellung"]},
        "modellierungsdetails": {"source_categories": ["Modellierung", "Grundschutz-Check"]},
        # "detailsZumItGrundschutzCheck" is now handled deterministically
        "benutzerdefinierteBausteine": {"source_categories": ["Grundschutz-Check", "Modellierung"]},
        "risikoanalyse": {"source_categories": ["Risikoanalyse"]},
        "realisierungsplan": {"source_categories": ["Realisierungsplan"]},
    }

    def __init__(self, config: AppConfig, ai_client: AiClient, rag_client: RagClient):
        self.config = config
        self.ai_client = ai_client
        self.rag_client = rag_client
        self.execution_plan = self._build_execution_plan_from_template()
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

    def _build_execution_plan_from_template(self) -> List[Dict[str, Any]]:
        """
        Parses the master_report_template.json to build a dynamic list of
        tasks for Chapter 3, establishing the template as the single source of truth.
        """
        plan = []
        template = self._load_asset_json(self.TEMPLATE_PATH)
        ch3_template = template.get("bsiAuditReport", {}).get("dokumentenpruefung", {})
        
        # Iterate through the template in a structured way to maintain order
        for subchapter in ch3_template.values():
            if not isinstance(subchapter, dict): continue
            
            # This handles both direct subchapters and nested ones (like in 3.3)
            sections_to_process = {subchapter.get('title', ''): subchapter}
            for key, value in subchapter.items():
                if isinstance(value, dict) and 'title' in value:
                     sections_to_process[value.get('title', key)] = value

            for section_data in sections_to_process.values():
                 # Find the key that maps to this data block
                 section_key = next((k for k, v in self._DOC_CATEGORY_MAP.items() if k in section_data.get('subchapterNumber', '') or k in str(section_data)), None)
                 
                 # This logic is complex; let's simplify by iterating keys directly from the template
        
        # Simpler, more direct iteration
        for subchapter_name, subchapter_data in ch3_template.items():
             if not isinstance(subchapter_data, dict): continue
             
             # Check for tasks at the top level of the subchapter (e.g., 3.1)
             task = self._create_task_from_section(subchapter_name, subchapter_data)
             if task:
                plan.append(task)
             
             # Check for tasks in nested sections (e.g., 3.3.1)
             for section_key, section_data in subchapter_data.items():
                if isinstance(section_data, dict):
                    task = self._create_task_from_section(section_key, section_data)
                    if task:
                        plan.append(task)
        return plan

    def _create_task_from_section(self, key: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Creates a single task dictionary for the execution plan."""
        # STUB for 3.6.1 as requested
        if key == "detailsZumItGrundschutzCheck":
            return {
                "key": key,
                "type": "deterministic",
                "result": {
                    "answers": [True, True, True, True, True],
                    "finding": {
                        "category": "OK",
                        "description": "Prüfung des IT-Grundschutz-Checks wurde zurückgestellt und wird in einer späteren Phase detailliert durchgeführt."
                    }
                }
            }

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
        
        task["type"] = "ai_driven"
        task["questions_formatted"] = "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))
        task["prompt_path"] = "assets/prompts/generic_question_prompt.txt"
        
        metadata = self._DOC_CATEGORY_MAP.get(key, {})
        task["source_categories"] = metadata.get("source_categories")

        num_questions = len(questions)
        # Simplified schema mapping
        schema_map = {1: "generic_1_question_schema.json", 2: "generic_2_question_schema.json",
                      3: "generic_3_question_schema.json", 4: "generic_4_question_schema.json",
                      5: "generic_5_question_schema.json"}
        
        # Override for specific cases
        if key == "risikoanalyse": schema_map[4] = "stage_3_7_risikoanalyse_schema.json"
        
        schema_file = schema_map.get(num_questions)
        if not schema_file:
            logging.error(f"No generic schema for {num_questions} questions in section '{key}'.")
            return None
        task["schema_path"] = f"assets/schemas/{schema_file}"
        
        return task

    async def _process_ai_subchapter(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Generates content for a single AI-driven subchapter."""
        key = task["key"]
        logging.info(f"Starting AI generation for subchapter: {key}")
        
        prompt_template_str = self._load_asset_text(task["prompt_path"])
        schema = self._load_asset_json(task["schema_path"])

        gcs_uris = self.rag_client.get_gcs_uris_for_categories(
            source_categories=task.get("source_categories")
        )

        if not gcs_uris and task.get("source_categories") is not None:
             logging.warning(f"No documents found for {key}. Returning error structure.")
             return {key: {"error": f"No source documents found for categories: {task.get('source_categories')}"}}

        prompt = prompt_template_str.format(
            questions=task["questions_formatted"]
        )

        try:
            generated_data = await self.ai_client.generate_json_response(prompt, schema, gcs_uris)
            
            # Inject findings from business logic checks into specific sections
            if key == "aktualitaetDerReferenzdokumente":
                coverage_finding = self._check_document_coverage()
                if coverage_finding['category'] != 'OK':
                    generated_data['finding'] = coverage_finding
            
            return {key: generated_data}
        except Exception as e:
            logging.error(f"Failed to generate data for subchapter {key}: {e}", exc_info=True)
            return {key: {"error": str(e)}}

    async def _process_summary_subchapter(self, task: Dict[str, Any], previous_findings: str) -> Dict[str, Any]:
        """Generates a summary/verdict for a subchapter."""
        key = task["key"]
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
        
        return "\n".join(findings_for_summary) if findings_for_summary else "No specific findings or deviations were generated in the preceding sections."

    async def run(self) -> dict:
        """Executes the dynamically generated plan for Chapter 3."""
        logging.info(f"Executing dynamically generated plan for stage: {self.STAGE_NAME}")
        
        aggregated_results = {}
        processed_ai_results = []
        
        ai_tasks = [task for task in self.execution_plan if task and task.get("type") == "ai_driven"]
        summary_tasks = [task for task in self.execution_plan if task and task.get("type") == "summary"]
        deterministic_tasks = [task for task in self.execution_plan if task and task.get("type") == "deterministic"]
        
        # Process deterministic tasks first (synchronously)
        for task in deterministic_tasks:
             logging.info(f"Processing deterministic task: {task['key']}")
             result = {task['key']: task['result']}
             processed_ai_results.append(result) # Add to list for summary step
             aggregated_results.update(result)

        # Process AI-driven tasks
        if ai_tasks:
            logging.info(f"--- Processing {len(ai_tasks)} AI-driven subchapters ---")
            ai_coroutines = [self._process_ai_subchapter(task) for task in ai_tasks]
            results_this_batch = await asyncio.gather(*ai_coroutines)
            processed_ai_results.extend(results_this_batch)
            for res in results_this_batch:
                aggregated_results.update(res)

        # Process summary tasks
        if summary_tasks:
            logging.info(f"--- Processing {len(summary_tasks)} summary subchapters ---")
            all_findings_text = self._get_findings_from_results(processed_ai_results)
            
            summary_coroutines = [self._process_summary_subchapter(task, all_findings_text) for task in summary_tasks]
            summary_results_list = await asyncio.gather(*summary_coroutines)

            for res in summary_results_list:
                aggregated_results.update(res)

        logging.info(f"Successfully aggregated results for all of stage {self.STAGE_NAME}")
        return aggregated_results