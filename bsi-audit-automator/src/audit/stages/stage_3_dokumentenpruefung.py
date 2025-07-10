# src/audit/stages/stage_3_dokumentenpruefung.py
import logging
import json
import asyncio
from typing import Dict, Any, List, Tuple
from datetime import datetime, timedelta
import fitz # PyMuPDF
from google.cloud.exceptions import NotFound

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.ai_client import AiClient
from src.clients.rag_client import RagClient
from src.audit.stages.control_catalog import ControlCatalog

class Chapter3Runner:
    """
    Handles generating content for Chapter 3 "Dokumentenprüfung" by dynamically
    parsing the master report template and using the central prompt configuration.
    """
    STAGE_NAME = "Chapter-3"
    TEMPLATE_PATH = "assets/json/master_report_template.json"
    PROMPT_CONFIG_PATH = "assets/json/prompt_config.json"
    INTERMEDIATE_CHECK_RESULTS_PATH = "output/results/intermediate/extracted_grundschutz_check_merged.json"

    def __init__(self, config: AppConfig, gcs_client: GcsClient, ai_client: AiClient, rag_client: RagClient):
        self.config = config
        self.gcs_client = gcs_client
        self.ai_client = ai_client
        self.rag_client = rag_client
        self.control_catalog = ControlCatalog()
        self.prompt_config = self._load_asset_json(self.PROMPT_CONFIG_PATH)
        self.execution_plan = self._build_execution_plan_from_template()
        self._doc_map = self.rag_client._document_category_map
        logging.info(f"Initialized runner for stage: {self.STAGE_NAME} with dynamic execution plan.")

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
        """Creates a single task dictionary for the execution plan using the central prompt config."""
        task_config = self.prompt_config["stages"]["Chapter-3"].get(key)
        if not task_config:
            return None

        task = {"key": key}
        task_type = task_config.get("type", "ai_driven")
        task["type"] = task_type
        
        if task_type == "custom_logic":
            return task

        task["schema_path"] = task_config["schema_path"]
        task["source_categories"] = task_config.get("source_categories")

        if task_type == "ai_driven":
            generic_prompt = self.prompt_config["stages"]["Chapter-3"]["generic_question"]["prompt"]
            content = data.get("content", [])
            questions = [item["questionText"] for item in content if item.get("type") == "question"]
            task["questions_formatted"] = "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))
            task["prompt"] = generic_prompt
        
        elif task_type == "summary":
            task["prompt"] = self.prompt_config["stages"]["Chapter-3"]["generic_summary"]["prompt"]
            task["summary_topic"] = data.get("title", key)
        
        return task

    def _deduplicate_and_select_best(self, requirements: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """
        De-duplicates a list of requirements from a single pass based on their ID.
        If duplicates are found, it keeps the one with the most text content.
        """
        best_versions: Dict[str, Dict[str, Any]] = {}
        for req in requirements:
            req_id = req.get("id")
            if not req_id:
                continue

            if req_id not in best_versions:
                best_versions[req_id] = req
            else:
                current_best = best_versions[req_id]
                # Compare and update if the new one is better
                len_current = len(current_best.get('umsetzungserlaeuterung', '')) + len(current_best.get('titel', ''))
                len_new = len(req.get('umsetzungserlaeuterung', '')) + len(req.get('titel', ''))
                if len_new > len_current:
                    best_versions[req_id] = req
        
        logging.info(f"De-duplicated list of {len(requirements)} raw items down to {len(best_versions)} unique, best-of-pass items.")
        return best_versions

    async def _run_extraction_pass(self, doc: fitz.Document, chunk_size: int, prompt_template: str, schema: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Runs a single data extraction pass over the document with a specific chunk size.
        """
        logging.info(f"Starting extraction pass with chunk size: {chunk_size} pages.")
        total_pages = len(doc)
        if total_pages == 0:
            logging.warning("PDF document has 0 pages. Skipping extraction pass.")
            return []
            
        tasks = []
        temp_blob_names = []

        for chunk_index, i in enumerate(range(0, total_pages, chunk_size)):
            if self.config.is_test_mode and chunk_index >= 2:
                logging.info(f"TEST MODE: Limiting extraction pass (chunk size {chunk_size}) to 2 chunks.")
                break
            chunk_doc = fitz.open()
            chunk_doc.insert_pdf(doc, from_page=i, to_page=min(i + chunk_size - 1, total_pages - 1))
            chunk_bytes = chunk_doc.write()
            
            chunk_blob_name = f"output/results/intermediate/temp_chunk_{chunk_size}_{i}.pdf"
            self.gcs_client.bucket.blob(chunk_blob_name).upload_from_string(chunk_bytes, content_type="application/pdf")
            chunk_uri = f"gs://{self.config.bucket_name}/{chunk_blob_name}"
            temp_blob_names.append(chunk_blob_name)

            task = self.ai_client.generate_json_response(
                prompt=prompt_template,
                json_schema=schema,
                gcs_uris=[chunk_uri],
                request_context_log=f"Chapter-3.6.1 Extraction (ChunkSize: {chunk_size}, Pages: {i}-{i+chunk_size-1})"
            )
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for blob_name in temp_blob_names:
            try:
                self.gcs_client.bucket.blob(blob_name).delete()
            except Exception as e:
                logging.warning(f"Could not delete temporary blob {blob_name}: {e}")
        
        pass_anforderungen = []
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logging.error(f"Extraction pass (chunk size {chunk_size}, page {i*chunk_size}) failed: {res}")
                continue
            # Filter for items with valid, non-empty IDs at the source
            for anforderung in res.get("anforderungen", []):
                if anforderung.get("id"):
                    pass_anforderungen.append(anforderung)
        
        logging.info(f"Extraction pass with chunk size {chunk_size} completed, found {len(pass_anforderungen)} valid requirements.")
        return pass_anforderungen

    def _merge_extraction_results(self, pass1_results: List[Dict[str, Any]], pass2_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Merges two lists of extracted requirements using a refined UNION strategy.
        It first de-duplicates each pass individually, keeping the best version of
        each requirement. Then, it merges the two de-duplicated lists, again
        keeping the best version if a requirement is present in both.
        """
        logging.info(f"De-duplicating and merging results from two extraction passes (Pass 1: {len(pass1_results)} items, Pass 2: {len(pass2_results)} items).")
        
        # Step 1: De-duplicate each pass, selecting the best version for each ID within that pass.
        best_of_pass1 = self._deduplicate_and_select_best(pass1_results)
        best_of_pass2 = self._deduplicate_and_select_best(pass2_results)

        # Step 2: Merge the two de-duplicated lists. Start with pass 1 as the base.
        merged_data = best_of_pass1.copy()

        # Iterate through the second pass to merge
        for req_id, req_pass2 in best_of_pass2.items():
            if req_id in merged_data:
                # If item exists, compare the best of pass 1 with the best of pass 2
                req_pass1 = merged_data[req_id]
                len_pass1_text = len(req_pass1.get('umsetzungserlaeuterung', '')) + len(req_pass1.get('titel', ''))
                len_pass2_text = len(req_pass2.get('umsetzungserlaeuterung', '')) + len(req_pass2.get('titel', ''))
                if len_pass2_text > len_pass1_text:
                    merged_data[req_id] = req_pass2 # Overwrite with the better version from pass 2
            else:
                # If item is new to the merged set, just add it
                merged_data[req_id] = req_pass2

        final_list = list(merged_data.values())
        logging.info(f"Merging complete. Final union result contains {len(final_list)} unique, high-quality requirements.")
        return final_list

    async def _extract_data_from_grundschutz_check(self) -> dict:
        """
        Phase 1 of 3.6.1 processing. Extracts all requirement data from the large
        Grundschutz-Check PDF by processing it in two separate passes with different
        chunk sizes (50 and 60 pages), then merges the results for completeness.
        """
        try:
            json_data = self.gcs_client.read_json(self.INTERMEDIATE_CHECK_RESULTS_PATH)
            logging.info(f"Found existing merged/extracted data at '{self.INTERMEDIATE_CHECK_RESULTS_PATH}'. Skipping extraction.")
            return json_data
        except NotFound:
            logging.info("No merged intermediate data found. Starting two-pass extraction from Grundschutz-Check PDF.")

        uris = self.rag_client.get_gcs_uris_for_categories(["Grundschutz-Check"])
        if not uris:
            raise FileNotFoundError("Could not find document with category 'Grundschutz-Check'.")
        
        gcs_uri = uris[0]
        blob_name = gcs_uri.replace(f"gs://{self.config.bucket_name}/", "")
        pdf_bytes = self.gcs_client.download_blob_as_bytes(self.gcs_client.bucket.blob(blob_name))
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        
        extraction_config = self.prompt_config["stages"]["Chapter-3"]["detailsZumItGrundschutzCheck_extraction"]
        prompt_template = extraction_config["prompt"]
        schema = self._load_asset_json(extraction_config["schema_path"])

        pass_50_task = self._run_extraction_pass(doc, 50, prompt_template, schema)
        pass_60_task = self._run_extraction_pass(doc, 60, prompt_template, schema)
        results_50, results_60 = await asyncio.gather(pass_50_task, pass_60_task)

        merged_anforderungen = self._merge_extraction_results(results_50, results_60)
        
        final_data = {"anforderungen": merged_anforderungen}
        self.gcs_client.upload_from_string(
            json.dumps(final_data, indent=2, ensure_ascii=False),
            self.INTERMEDIATE_CHECK_RESULTS_PATH
        )
        logging.info(f"Saved merged Grundschutz-Check data with {len(merged_anforderungen)} items.")
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

        # Q5: Sind alle Anforderungen innerhalb der letzten 12 Monate überprüft worden? (Robust Deterministic)
        one_year_ago = datetime.now() - timedelta(days=365)
        nineteen_seventy = datetime(1970, 1, 1)
        outdated_items = []
        for a in anforderungen:
            date_str = a.get("datumLetztePruefung")
            try:
                check_date = None
                if date_str:
                    if "." in str(date_str):
                        try:
                            check_date = datetime.strptime(str(date_str), "%d.%m.%Y")
                        except ValueError:
                            check_date = datetime.strptime(str(date_str), "%d.%m.%y")
                    else: # Assumes ISO format
                        check_date = datetime.fromisoformat(str(date_str).split("T")[0])

                if not check_date or check_date.year <= 1970: # Check for missing or fallback date
                    outdated_items.append(a.get("id", "Unknown ID"))
                    continue
                
                if check_date < one_year_ago:
                    outdated_items.append(a.get("id", "Unknown ID"))
            except (ValueError, TypeError) as e:
                logging.warning(f"Could not parse date '{date_str}' for item '{a.get('id', 'N/A')}'. Error: {e}. Counting as outdated.")
                outdated_items.append(a.get("id", "Unknown ID")) # Count as outdated if date is invalid

        answers[4] = not bool(outdated_items)
        if outdated_items:
            findings.append({"category": "AG", "description": f"Die Prüfung von {len(outdated_items)} Anforderungen (z.B. {outdated_items[0]}) liegt mehr als 12 Monate zurück, das Datum fehlt oder ist ungültig."})

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
        
        prompt = task["prompt"].format(questions=task["questions_formatted"])
        schema = self._load_asset_json(task["schema_path"])
        gcs_uris = self.rag_client.get_gcs_uris_for_categories(
            source_categories=task.get("source_categories")
        )
        if not gcs_uris and task.get("source_categories") is not None:
             logging.warning(f"No documents found for {key}. Returning error structure.")
             return {key: {"error": f"No source documents found for categories: {task.get('source_categories')}"}}
        try:
            generated_data = await self.ai_client.generate_json_response(
                prompt=prompt, json_schema=schema, gcs_uris=gcs_uris, request_context_log=f"Chapter-3: {key}"
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

        prompt = task["prompt"].format(summary_topic=task["summary_topic"], previous_findings=previous_findings)
        schema = self._load_asset_json(task["schema_path"])
        try:
            generated_data = await self.ai_client.generate_json_response(
                prompt=prompt, json_schema=schema, request_context_log=f"Chapter-3 Summary: {key}"
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
        
        # Isolate custom logic, AI, and summary tasks
        custom_logic_tasks = [t for t in self.execution_plan if t and t.get("type") == "custom_logic"]
        ai_tasks = [t for t in self.execution_plan if t and t.get("type") == "ai_driven"]
        summary_tasks = [t for t in self.execution_plan if t and t.get("type") == "summary"]

        # Run custom logic first, as it might be a dependency for others (like extraction)
        for task in custom_logic_tasks:
            key = task['key']
            logging.info(f"--- Processing custom logic task: {key} ---")
            result = None
            if key == 'detailsZumItGrundschutzCheck':
                result = await self._process_details_zum_it_grundschutz_check()
            if result:
                processed_results.append(result)
                aggregated_results.update(result)

        # Batch process all standard AI tasks in parallel
        if ai_tasks:
            logging.info(f"--- Processing {len(ai_tasks)} AI-driven subchapters ---")
            ai_coroutines = [self._process_ai_subchapter(task) for task in ai_tasks]
            ai_results_batch = await asyncio.gather(*ai_coroutines)
            processed_results.extend(ai_results_batch)
            for res in ai_results_batch:
                aggregated_results.update(res)

        # Process summary tasks last, using all previously generated results
        if summary_tasks:
            logging.info(f"--- Processing {len(summary_tasks)} summary subchapters ---")
            all_findings_text = self._get_findings_from_results(processed_results)
            
            summary_coroutines = [self._process_summary_subchapter(task, all_findings_text) for task in summary_tasks]
            summary_results_list = await asyncio.gather(*summary_coroutines)
            for res in summary_results_list:
                aggregated_results.update(res)

        logging.info(f"Successfully aggregated results for all of stage {self.STAGE_NAME}")
        return aggregated_results