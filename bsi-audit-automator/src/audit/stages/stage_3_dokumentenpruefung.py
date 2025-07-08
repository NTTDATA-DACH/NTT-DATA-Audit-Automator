# src/audit/stages/stage_3_dokumentenpruefung.py
import logging
import json
import asyncio
from typing import Dict, Any, List, Tuple
from datetime import datetime, timedelta
import fitz # PyMuPDF
from google.cloud.exceptions import NotFound

from src.config import AppConfig
from src.clients.ai_client import AiClient
from src.clients.rag_client import RagClient
from src.audit.stages.control_catalog import ControlCatalog

class Chapter3Runner:
    """
    Handles generating content for Chapter 3 "Dokumentenprüfung" by dynamically
    parsing the master report template.
    """
    STAGE_NAME = "Chapter-3"
    TEMPLATE_PATH = "assets/json/master_report_template.json"
    INTERMEDIATE_CHECK_RESULTS_PATH = "output/results/intermediate/extracted_grundschutz_check.json"

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
        "modellierungsdetails": {"source_categories": ["Modellierung"]},
        "detailsZumItGrundschutzCheck": {"source_categories": ["Grundschutz-Check", "Realisierungsplan"]},
        "benutzerdefinierteBausteine": {"source_categories": ["Modellierung"]},
        "risikoanalyse": {"source_categories": ["Risikoanalyse"]},
        "realisierungsplan": {"source_categories": ["Realisierungsplan"]},
    }

    def __init__(self, config: AppConfig, ai_client: AiClient, rag_client: RagClient):
        self.config = config
        self.gcs_client = gcs_client
        self.ai_client = ai_client
        self.rag_client = rag_client
        self.control_catalog = ControlCatalog()
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
        
        # Simpler, more direct iteration
        for subchapter_name, subchapter_data in ch3_template.items():
             if not isinstance(subchapter_data, dict): continue
             
             task = self._create_task_from_section(subchapter_name, subchapter_data)
             if task:
                plan.append(task)
             
             for section_key, section_data in subchapter_data.items():
                if isinstance(section_data, dict):
                    task = self._create_task_from_section(section_key, section_data)
                    if task:
                        plan.append(task)
        return plan

    def _create_task_from_section(self, key: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Creates a single task dictionary for the execution plan."""
        if key == "detailsZumItGrundschutzCheck":
            return {"key": key, "type": "custom_logic"}

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
        schema_map = {1: "generic_1_question_schema.json", 2: "generic_2_question_schema.json",
                      3: "generic_3_question_schema.json", 4: "generic_4_question_schema.json",
                      5: "generic_5_question_schema.json"}
        
        if key == "risikoanalyse": schema_map[4] = "stage_3_7_risikoanalyse_schema.json"
        
        schema_file = schema_map.get(num_questions)
        if not schema_file:
            logging.error(f"No generic schema for {num_questions} questions in section '{key}'.")
            return None
        task["schema_path"] = f"assets/schemas/{schema_file}"
        
        return task

    async def _extract_data_from_grundschutz_check(self) -> dict:
        """
        Phase 1 of 3.6.1 processing. Extracts all requirement data from the large
        Grundschutz-Check PDF by processing it in 50-page chunks. The result is
        saved as an intermediate JSON file in GCS.
        """
        try:
            # Idempotency check: If the intermediate file exists, load and return it.
            json_data = self.gcs_client.read_json(self.INTERMEDIATE_CHECK_RESULTS_PATH)
            logging.info(f"Found existing extracted data at '{self.INTERMEDIATE_CHECK_RESULTS_PATH}'. Skipping extraction.")
            return json_data
        except NotFound:
            logging.info("No intermediate data found. Starting extraction from Grundschutz-Check PDF.")

        uris = self.rag_client.get_gcs_uris_for_categories(["Grundschutz-Check"])
        if not uris:
            raise FileNotFoundError("Could not find document with category 'Grundschutz-Check'.")
        
        # We process only the first document found.
        gcs_uri = uris[0]
        blob_name = gcs_uri.replace(f"gs://{self.config.bucket_name}/", "")
        pdf_bytes = self.gcs_client.download_blob_as_bytes(self.gcs_client.bucket.blob(blob_name))

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)
        chunk_size = 50
        tasks = []
        prompt_template = self._load_asset_text("assets/prompts/stage_3_6_1_extract_check_data.txt")
        schema = self._load_asset_json("assets/schemas/stage_3_6_1_extract_check_data_schema.json")

        for i in range(0, total_pages, chunk_size):
            chunk_doc = fitz.open()
            chunk_doc.insert_pdf(doc, from_page=i, to_page=min(i + chunk_size - 1, total_pages - 1))
            chunk_bytes = chunk_doc.write()
            
            # Create a temporary blob for the chunk to get a GCS URI for the API
            chunk_blob_name = f"output/results/intermediate/temp_chunk_{i}.pdf"
            self.gcs_client.bucket.blob(chunk_blob_name).upload_from_string(chunk_bytes, content_type="application/pdf")
            chunk_uri = f"gs://{self.config.bucket_name}/{chunk_blob_name}"

            task = self.ai_client.generate_json_response(
                prompt=prompt_template,
                json_schema=schema,
                gcs_uris=[chunk_uri],
                request_context_log=f"Chapter-3.6.1 Extraction (Pages {i}-{i+chunk_size-1})"
            )
            tasks.append(task)

        # Run all chunk processing tasks in parallel
        results = await asyncio.gather(*tasks)

        # Clean up temporary chunk files
        for i in range(0, total_pages, chunk_size):
            chunk_blob_name = f"output/results/intermediate/temp_chunk_{i}.pdf"
            self.gcs_client.bucket.blob(chunk_blob_name).delete()
        
        # Aggregate results
        all_anforderungen = []
        for res in results:
            all_anforderungen.extend(res.get("anforderungen", []))
        
        final_data = {"anforderungen": all_anforderungen}
        self.gcs_client.upload_from_string(
            json.dumps(final_data, indent=2, ensure_ascii=False),
            self.INTERMEDIATE_CHECK_RESULTS_PATH
        )
        logging.info(f"Saved extracted Grundschutz-Check data with {len(all_anforderungen)} items.")
        return final_data

    async def _process_details_zum_it_grundschutz_check(self) -> Dict[str, Any]:
        """
        Phase 2 of 3.6.1 processing. Uses the extracted data to answer the five
        questions with a mix of deterministic and AI-driven logic.
        """
        extracted_data = await self._extract_data_from_grundschutz_check()
        anforderungen = extracted_data.get("anforderungen", [])
        answers = [None] * 5
        findings = []

        # Q1: Wurde zu jeder Anforderung der Umsetzungsstatus erhoben? (Deterministic)
        missing_status = [a for a in anforderungen if not a.get("umsetzungsstatus")]
        answers[0] = not bool(missing_status)
        if missing_status:
            findings.append({"category": "AG", "description": f"{len(missing_status)} Anforderungen fehlte eine Angabe zum Umsetzungsstatus."})

        # Q2: Wurden alle Anforderungen mit Umsetzungsstatus „entbehrlich“ plausibel begründet? (AI)
        entbehrlich_items = [a for a in anforderungen if a.get("umsetzungsstatus") == "entbehrlich"]
        if entbehrlich_items:
            prompt = f"Basierend auf der folgenden Liste von als 'entbehrlich' deklarierten Anforderungen, beurteile, ob die Begründungen plausibel sind.\n\n{json.dumps(entbehrlich_items, indent=2, ensure_ascii=False)}"
            q2_res = await self.ai_client.generate_json_response(prompt, self._load_asset_json("assets/schemas/generic_1_question_schema.json"), request_context_log="Chapter-3.6.1-Q2")
            answers[1] = q2_res["answers"][0]
            if q2_res["finding"]["category"] != "OK": findings.append(q2_res["finding"])
        else:
            answers[1] = True # No 'entbehrlich' items to check

        # Q3: Sind alle MUSS-Teilanforderungen erfüllt? (AI + deterministic filter)
        level_1_ids = self.control_catalog.get_level_1_control_ids()
        muss_anforderungen = [a for a in anforderungen if a.get("id") in level_1_ids]
        if muss_anforderungen:
            prompt = f"Basierend auf der folgenden Liste von MUSS-Anforderungen, prüfe, ob alle den Umsetzungsstatus 'Ja' haben.\n\n{json.dumps(muss_anforderungen, indent=2, ensure_ascii=False)}"
            q3_res = await self.ai_client.generate_json_response(prompt, self._load_asset_json("assets/schemas/generic_1_question_schema.json"), request_context_log="Chapter-3.6.1-Q3")
            answers[2] = q3_res["answers"][0]
            if q3_res["finding"]["category"] != "OK": findings.append(q3_res["finding"])
        else:
            answers[2] = True # No Level 1 requirements found in the check

        # Q4: Wurden die nicht oder nur teilweise umgesetzten Anforderungen im Referenzdokument A.6 dokumentiert? (AI)
        unmet_items = [a for a in anforderungen if a.get("umsetzungsstatus") in ["nein", "teilweise"]]
        if unmet_items:
            realisierungsplan_uris = self.rag_client.get_gcs_uris_for_categories(["Realisierungsplan"])
            prompt = f"Die folgenden Anforderungen wurden als 'nein' oder 'teilweise' umgesetzt gemeldet. Überprüfe, ob diese im angehängten Realisierungsplan (Dokument A.6) dokumentiert sind.\n\n{json.dumps(unmet_items, indent=2, ensure_ascii=False)}"
            q4_res = await self.ai_client.generate_json_response(prompt, self._load_asset_json("assets/schemas/generic_1_question_schema.json"), gcs_uris=realisierungsplan_uris, request_context_log="Chapter-3.6.1-Q4")
            answers[3] = q4_res["answers"][0]
            if q4_res["finding"]["category"] != "OK": findings.append(q4_res["finding"])
        else:
            answers[3] = True # No unmet items to check

        # Q5: Sind alle Anforderungen innerhalb der letzten 12 Monate überprüft worden? (Deterministic)
        one_year_ago = datetime.now() - timedelta(days=365)
        outdated_items = []
        for a in anforderungen:
            date_str = a.get("datumLetztePruefung")
            try:
                # Handle different date formats
                if "." in date_str:
                    check_date = datetime.strptime(date_str, "%d.%m.%Y")
                else:
                    check_date = datetime.fromisoformat(date_str.split("T")[0])
                if check_date < one_year_ago:
                    outdated_items.append(a["id"])
            except (ValueError, TypeError):
                outdated_items.append(a["id"]) # Count as outdated if date is invalid
        answers[4] = not bool(outdated_items)
        if outdated_items:
            findings.append({"category": "AG", "description": f"Die Prüfung von {len(outdated_items)} Anforderungen (z.B. {outdated_items[0]}) liegt mehr als 12 Monate zurück oder das Datum ist ungültig."})

        # Consolidate findings
        final_finding = {"category": "OK", "description": "Alle Prüfungen für den IT-Grundschutz-Check waren erfolgreich."}
        if findings:
            final_finding["category"] = "AS" if any(f['category'] == 'AS' for f in findings) else "AG"
            final_finding["description"] = "Zusammenfassung der Feststellungen: " + " | ".join([f['description'] for f in findings])

        return {"detailsZumItGrundschutzCheck": {"answers": answers, "finding": final_finding}}


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
            generated_data = await self.ai_client.generate_json_response(
                prompt=prompt,
                json_schema=schema,
                gcs_uris=gcs_uris,
                request_context_log=f"Chapter-3: {key}"
            )
            
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
            generated_data = await self.ai_client.generate_json_response(
                prompt=prompt,
                json_schema=schema,
                request_context_log=f"Chapter-3 Summary: {key}"
            )
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
        processed_results = []
        
        tasks_to_run = self.execution_plan
        
        for task in tasks_to_run:
            if not task: continue
            key = task['key']
            task_type = task['type']
            
            result = None
            if task_type == 'custom_logic' and key == 'detailsZumItGrundschutzCheck':
                logging.info(f"--- Processing custom logic task: {key} ---")
                result = await self._process_details_zum_it_grundschutz_check()
            elif task_type == 'ai_driven':
                # Batch AI tasks together
                pass # This logic will be handled below
            
            if result:
                processed_results.append(result)
                aggregated_results.update(result)

        # Batch process all standard AI tasks
        ai_tasks = [task for task in tasks_to_run if task and task.get("type") == "ai_driven"]
        if ai_tasks:
            logging.info(f"--- Processing {len(ai_tasks)} AI-driven subchapters ---")
            ai_coroutines = [self._process_ai_subchapter(task) for task in ai_tasks]
            ai_results_batch = await asyncio.gather(*ai_coroutines)
            processed_results.extend(ai_results_batch)
            for res in ai_results_batch:
                aggregated_results.update(res)

        # Process summary tasks last, now that all other results are available
        summary_tasks = [task for task in tasks_to_run if task and task.get("type") == "summary"]
        if summary_tasks:
            logging.info(f"--- Processing {len(summary_tasks)} summary subchapters ---")
            all_findings_text = self._get_findings_from_results(processed_results)
            
            summary_coroutines = [self._process_summary_subchapter(task, all_findings_text) for task in summary_tasks]
            summary_results_list = await asyncio.gather(*summary_coroutines)

            for res in summary_results_list:
                aggregated_results.update(res)

        logging.info(f"Successfully aggregated results for all of stage {self.STAGE_NAME}")
        return aggregated_results