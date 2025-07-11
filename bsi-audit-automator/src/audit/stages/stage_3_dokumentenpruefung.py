# file: src/audit/stages/stage_3_dokumentenpruefung.py
import logging
import json
import asyncio
from typing import Dict, Any, List, Tuple
from datetime import datetime, timedelta
import fitz # PyMuPDF
from google.cloud.exceptions import NotFound
from collections import defaultdict
import re

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.ai_client import AiClient
from src.clients.rag_client import RagClient
from src.audit.stages.control_catalog import ControlCatalog

class Chapter3Runner:
    """
    Handles generating content for Chapter 3 "Dokumentenprüfung" by dynamically
    parsing the master report template and using the central prompt configuration.
    Implements the Ground-Truth-Driven Semantic Chunking strategy for 3.6.1.
    """
    STAGE_NAME = "Chapter-3"
    TEMPLATE_PATH = "assets/json/master_report_template.json"
    PROMPT_CONFIG_PATH = "assets/json/prompt_config.json"
    
    # New paths for the ground-truth strategy
    GROUND_TRUTH_MAP_PATH = "output/results/intermediate/system_structure_map.json"
    INTERMEDIATE_CHECK_RESULTS_PATH = "output/results/intermediate/extracted_grundschutz_check_merged.json"
    CHUNK_SIZES = [19, 21]

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

    # region: Ground Truth Map Creation (Step 1)
    async def _build_system_structure_map(self, force_remap: bool) -> Dict[str, Any]:
        """
        Orchestrates the creation of the ground truth map. Caches the result in GCS
        but respects the 'force_remap' flag.
        """
        if not force_remap:
            try:
                map_data = self.gcs_client.read_json(self.GROUND_TRUTH_MAP_PATH)
                logging.info(f"Using cached ground truth map from: {self.GROUND_TRUTH_MAP_PATH}")
                return map_data
            except NotFound:
                logging.info("Ground truth map not found. Generating...")
        else:
            logging.info(f"Force remapping enabled. Generating new ground truth map.")

        # 1.1 Extract Zielobjekte from Strukturanalyse (A.1)
        zielobjekte_uris = self.rag_client.get_gcs_uris_for_categories(["Strukturanalyse"])
        zielobjekte_config = self.prompt_config["Chapter-3-Ground-Truth"]["extract_zielobjekte"]
        zielobjekte_list = []
        if zielobjekte_uris:
            zielobjekte_res = await self.ai_client.generate_json_response(
                prompt=zielobjekte_config["prompt"],
                json_schema=self._load_asset_json(zielobjekte_config["schema_path"]),
                gcs_uris=zielobjekte_uris,
                request_context_log="GT: Extract Zielobjekte"
            )
            zielobjekte_list = zielobjekte_res.get("zielobjekte", [])
        
        # 1.2 Extract Baustein mappings from Modellierung (A.3)
        modellierung_uris = self.rag_client.get_gcs_uris_for_categories(["Modellierung"])
        mappings_config = self.prompt_config["Chapter-3-Ground-Truth"]["extract_baustein_mappings"]
        baustein_mappings = {}
        if modellierung_uris:
            mappings_res = await self.ai_client.generate_json_response(
                prompt=mappings_config["prompt"],
                json_schema=self._load_asset_json(mappings_config["schema_path"]),
                gcs_uris=modellierung_uris,
                request_context_log="GT: Extract Baustein Mappings"
            )
            for mapping in mappings_res.get("mappings", []):
                baustein_mappings[mapping["baustein_id"]] = mapping["zielobjekt_kuerzel"]
        
        # 1.2.1 Apply deterministic rules for ISMS etc.
        DETERMINISTIC_PREFIXES = ("ISMS", "ORP", "CON", "OPS", "DER")
        for layer in self.control_catalog._baustein_map.keys():
             if layer.startswith(DETERMINISTIC_PREFIXES):
                 baustein_mappings[layer] = "Informationsverbund"
        
        # Add the special Zielobjekt if it was used
        if "Informationsverbund" not in [z['kuerzel'] for z in zielobjekte_list]:
            zielobjekte_list.append({"kuerzel": "Informationsverbund", "name": "Gesamter Informationsverbund"})
            
        # 1.3 Consolidate and Save
        final_map = {
            "zielobjekte": zielobjekte_list,
            "baustein_to_zielobjekt_mapping": baustein_mappings
        }
        self.gcs_client.upload_from_string(json.dumps(final_map, indent=2, ensure_ascii=False), self.GROUND_TRUTH_MAP_PATH)
        logging.info(f"Successfully created and saved ground truth map to {self.GROUND_TRUTH_MAP_PATH}")
        return final_map
    # endregion

    # region: Context-Aware Extraction and Merging (Steps 2 & 3)
    async def _run_extraction_pass(self, doc: fitz.Document, chunk_size: int, prompt_template: str, schema: Dict[str, Any]) -> Tuple[List, List]:
        """Runs a single data extraction pass, returning raw anforderungen and headings."""
        logging.info(f"Starting extraction pass with chunk size: {chunk_size} pages.")
        total_pages = len(doc)
        if total_pages == 0: return [], []
            
        tasks, temp_blob_names = [], []
        for chunk_index, i in enumerate(range(0, total_pages, chunk_size)):
            if self.config.is_test_mode and chunk_index >= 2: break
            
            chunk_doc = fitz.open()
            chunk_doc.insert_pdf(doc, from_page=i, to_page=min(i + chunk_size - 1, total_pages - 1))
            
            chunk_blob_name = f"output/results/intermediate/temp_chunk_{chunk_size}_{i}.pdf"
            self.gcs_client.bucket.blob(chunk_blob_name).upload_from_string(chunk_doc.write(), content_type="application/pdf")
            temp_blob_names.append(chunk_blob_name)

            task = self.ai_client.generate_json_response(
                prompt=prompt_template, json_schema=schema, gcs_uris=[f"gs://{self.config.bucket_name}/{chunk_blob_name}"],
                request_context_log=f"Chapter-3.6.1 Extraction (ChunkSize: {chunk_size}, Pages: {i}-{i+chunk_size-1})"
            )
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # Cleanup
        for blob_name in temp_blob_names:
            try: self.gcs_client.bucket.blob(blob_name).delete()
            except Exception as e: logging.warning(f"Could not delete temp blob {blob_name}: {e}")

        all_anforderungen, all_headings = [], []
        for res in results:
            if isinstance(res, Exception): continue
            all_anforderungen.extend(res.get("anforderungen", []))
            all_headings.extend(res.get("chapter_headings", []))
        return all_anforderungen, all_headings

    async def _run_context_aware_extraction(self, force_remap: bool) -> List[Dict[str, Any]]:
        """
        Orchestrates the two-pass extraction and the final merge-and-refine step.
        The result of this function is immune to the 'force_remap' flag.
        """
        try:
            # This check is independent of force_remap, per user request.
            data = self.gcs_client.read_json(self.INTERMEDIATE_CHECK_RESULTS_PATH)
            logging.info(f"Found existing merged/extracted data at '{self.INTERMEDIATE_CHECK_RESULTS_PATH}'. Skipping extraction.")
            return data["anforderungen"]
        except NotFound:
            logging.info("No merged intermediate data found. Starting two-pass extraction from Grundschutz-Check PDF.")

        ground_truth_map = await self._build_system_structure_map(force_remap=force_remap)
        
        uris = self.rag_client.get_gcs_uris_for_categories(["Grundschutz-Check"])
        if not uris: raise FileNotFoundError("Could not find document with category 'Grundschutz-Check'.")
        
        blob_name = uris[0].replace(f"gs://{self.config.bucket_name}/", "")
        pdf_bytes = self.gcs_client.download_blob_as_bytes(self.gcs_client.bucket.blob(blob_name))
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        
        config = self.prompt_config["stages"]["Chapter-3"]["detailsZumItGrundschutzCheck_extraction"]
        prompt = config["prompt"]
        schema = self._load_asset_json(config["schema_path"])

        pass_tasks = [self._run_extraction_pass(doc, cs, prompt, schema) for cs in self.CHUNK_SIZES]
        pass_results = await asyncio.gather(*pass_tasks)
        
        all_anforderungen = [item for res_tuple in pass_results for item in res_tuple[0]]
        all_headings = [item for res_tuple in pass_results for item in res_tuple[1]]

        final_anforderungen = self._merge_and_refine_results(all_anforderungen, all_headings, ground_truth_map)
        
        self.gcs_client.upload_from_string(
            json.dumps({"anforderungen": final_anforderungen}, indent=2, ensure_ascii=False),
            self.INTERMEDIATE_CHECK_RESULTS_PATH
        )
        return final_anforderungen

    def _merge_and_refine_results(self, all_anforderungen: List, all_headings: List, ground_truth_map: Dict) -> List[Dict[str, Any]]:
        """Merges and refines extracted data into a clean, de-duplicated list."""
        zielobjekte_map = {z['kuerzel']: z['name'] for z in ground_truth_map.get('zielobjekte', [])}
        
        # 1. Assign by Page Number
        unique_headings = {f"{h.get('kuerzel', '')}-{h.get('pagenumber', 0)}": h for h in all_headings if h.get('kuerzel')}.values()
        sorted_headings = sorted(list(unique_headings), key=lambda x: x.get('pagenumber', 0))

        for anforderung in all_anforderungen:
            page = anforderung.get('pagenumber', 0)
            assigned_kuerzel = "Unassigned"
            for heading in reversed(sorted_headings):
                if page >= heading.get('pagenumber', 0):
                    assigned_kuerzel = heading['kuerzel']
                    break
            anforderung['zielobjekt_kuerzel'] = assigned_kuerzel
        
        # 2. Merge Duplicates per Zielobjekt
        grouped_anforderungen = defaultdict(list)
        for a in all_anforderungen:
            if not a.get('id'): continue
            key = (a['zielobjekt_kuerzel'], a['id'])
            grouped_anforderungen[key].append(a)
        
        final_list = []
        STATUS_PRIORITY = {'Nein': 4, 'teilweise': 3, 'Ja': 2, 'entbehrlich': 1, 'N/A': 0}

        for (kuerzel, anforderung_id), items in grouped_anforderungen.items():
            best_item = {}
            best_item['id'] = anforderung_id
            best_item['zielobjekt_kuerzel'] = kuerzel
            best_item['zielobjekt_name'] = zielobjekte_map.get(kuerzel, "Unbekanntes Zielobjekt")
            
            # Merge logic
            best_item['titel'] = max(items, key=lambda x: len(x.get('titel', ''))).get('titel', '')
            
            all_erlaeuterungen = " ".join([i.get('umsetzungserlaeuterung', '') for i in items])
            unique_sentences = list(dict.fromkeys(re.split(r'(?<=[.!?])\s+', all_erlaeuterungen)))
            best_item['umsetzungserlaeuterung'] = " ".join(filter(None, unique_sentences)).strip()

            best_status = max(items, key=lambda x: STATUS_PRIORITY.get(x.get('umsetzungsstatus'), 0)).get('umsetzungsstatus')
            best_item['umsetzungsstatus'] = best_status

            latest_date = datetime(1970, 1, 1)
            for item in items:
                date_str = item.get("datumLetztePruefung")
                try:
                    parsed_date = datetime.fromisoformat(str(date_str).split("T")[0]) if date_str and '-' in str(date_str) else datetime.strptime(str(date_str), "%d.%m.%Y")
                    if parsed_date > latest_date: latest_date = parsed_date
                except (ValueError, TypeError): continue
            best_item['datumLetztePruefung'] = latest_date.strftime("%Y-%m-%d") if latest_date.year > 1970 else "1970-01-01"

            final_list.append(best_item)
            
        logging.info(f"Merge & Refine complete. Final list has {len(final_list)} unique requirements.")
        return final_list

    # endregion

    # region: Hybrid Analysis (Sub-Phase 2.B)
    async def _process_details_zum_it_grundschutz_check(self, force_remap: bool) -> Dict[str, Any]:
        """
        Uses the merged/refined data to answer the five questions with a mix of
        deterministic and targeted AI-driven logic.
        """
        anforderungen = await self._run_context_aware_extraction(force_remap=force_remap)
        answers = [None] * 5
        findings = []

        # Q1: Status erhoben? (Deterministic)
        answers[0] = all(a.get("umsetzungsstatus") for a in anforderungen)
        if not answers[0]:
            findings.append({"category": "AG", "description": "Nicht für alle Anforderungen wurde ein Umsetzungsstatus erhoben."})

        # Q5: Prüfung < 12 Monate? (Deterministic)
        one_year_ago = datetime.now() - timedelta(days=365)
        outdated = [a for a in anforderungen if datetime.strptime(a.get("datumLetztePruefung", "1970-01-01"), "%Y-%m-%d") < one_year_ago]
        answers[4] = not bool(outdated)
        if outdated:
            findings.append({"category": "AG", "description": f"Die Prüfung von {len(outdated)} Anforderungen liegt mehr als 12 Monate zurück."})
            
        targeted_prompt_config = self.prompt_config["stages"]["Chapter-3"]["targeted_question"]
        targeted_prompt_template = targeted_prompt_config["prompt"]

        # Q2: "entbehrlich" plausibel? (Targeted AI)
        entbehrlich_items = [a for a in anforderungen if a.get("umsetzungsstatus") == "entbehrlich"]
        if entbehrlich_items:
            prompt = targeted_prompt_template.format(
                question="Sind die Begründungen für 'entbehrlich' plausibel?",
                json_data=json.dumps(entbehrlich_items, indent=2, ensure_ascii=False)
            )
            res = await self.ai_client.generate_json_response(prompt, self._load_asset_json("assets/schemas/generic_1_question_schema.json"), request_context_log="3.6.1-Q2")
            answers[1], findings = (res['answers'][0], findings + [res['finding']] if res['finding']['category'] != 'OK' else findings)
        else:
            answers[1] = True

        # Q3: MUSS-Anforderungen erfüllt? (Targeted AI)
        level_1_ids = self.control_catalog.get_level_1_control_ids()
        muss_anforderungen = [a for a in anforderungen if a.get("id") in level_1_ids]
        if muss_anforderungen:
            prompt = targeted_prompt_template.format(
                question="Sind alle diese MUSS-Anforderungen mit Status 'Ja' umgesetzt?",
                json_data=json.dumps(muss_anforderungen, indent=2, ensure_ascii=False)
            )
            res = await self.ai_client.generate_json_response(prompt, self._load_asset_json("assets/schemas/generic_1_question_schema.json"), request_context_log="3.6.1-Q3")
            answers[2], findings = (res['answers'][0], findings + [res['finding']] if res['finding']['category'] != 'OK' else findings)
        else:
            answers[2] = True

        # Q4: Nicht/teilweise umgesetzte in A.6? (Targeted AI)
        unmet_items = [a for a in anforderungen if a.get("umsetzungsstatus") in ["Nein", "teilweise"]]
        realisierungsplan_uris = self.rag_client.get_gcs_uris_for_categories(["Realisierungsplan"])
        if unmet_items and realisierungsplan_uris:
            prompt = targeted_prompt_template.format(
                question="Sind diese nicht oder teilweise umgesetzten Anforderungen im angehängten Realisierungsplan (A.6) dokumentiert?",
                json_data=json.dumps(unmet_items, indent=2, ensure_ascii=False)
            )
            res = await self.ai_client.generate_json_response(prompt, self._load_asset_json("assets/schemas/generic_1_question_schema.json"), gcs_uris=realisierungsplan_uris, request_context_log="3.6.1-Q4")
            answers[3], findings = (res['answers'][0], findings + [res['finding']] if res['finding']['category'] != 'OK' else findings)
        else:
            answers[3] = True
            
        # Consolidate findings
        final_finding = {"category": "OK", "description": "Alle Prüfungen für den IT-Grundschutz-Check waren erfolgreich."}
        if findings:
            final_finding["category"] = "AS" if any(f['category'] == 'AS' for f in findings) else "AG"
            final_finding["description"] = "Zusammenfassung: " + " | ".join([f['description'] for f in findings])

        return {"detailsZumItGrundschutzCheck": {"answers": answers, "finding": final_finding}}
    # endregion

    # region: Main Runner Logic
    def _check_document_coverage(self) -> Dict[str, Any]:
        """Checks if all critical BSI document types are present."""
        REQUIRED_CATEGORIES = {
            "Sicherheitsleitlinie", "Strukturanalyse", "Schutzbedarfsfeststellung",
            "Modellierung", "Grundschutz-Check", "Risikoanalyse", "Realisierungsplan"
        }
        present_categories = set(self._doc_map.keys())
        missing_categories = REQUIRED_CATEGORIES - present_categories

        if not missing_categories:
            return {"category": "OK", "description": "Alle kritischen Dokumententypen sind vorhanden."}
        else:
            desc = f"Kritische Dokumente fehlen: {', '.join(sorted(list(missing_categories)))}. Dies ist eine schwerwiegende Abweichung."
            logging.warning(f"Document coverage check failed. Missing: {missing_categories}")
            return {"category": "AS", "description": desc}

    def _build_execution_plan_from_template(self) -> List[Dict[str, Any]]:
        """Parses master_report_template.json to build a dynamic list of tasks."""
        plan = []
        template = self._load_asset_json(self.TEMPLATE_PATH)
        ch3_template = template.get("bsiAuditReport", {}).get("dokumentenpruefung", {})
        
        for subchapter_name, subchapter_data in ch3_template.items():
             if not isinstance(subchapter_data, dict): continue
             task = self._create_task_from_section(subchapter_name, subchapter_data)
             if task: plan.append(task)
             for section_key, section_data in subchapter_data.items():
                if isinstance(section_data, dict):
                    task = self._create_task_from_section(section_key, section_data)
                    if task: plan.append(task)
        return plan

    def _create_task_from_section(self, key: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Creates a single task dictionary for the execution plan."""
        task_config = self.prompt_config["stages"]["Chapter-3"].get(key)
        if not task_config: return None

        task = {"key": key, "type": task_config.get("type", "ai_driven")}
        if task["type"] == "custom_logic": return task

        task["schema_path"] = task_config["schema_path"]
        task["source_categories"] = task_config.get("source_categories")

        if task["type"] == "ai_driven":
            generic_prompt = self.prompt_config["stages"]["Chapter-3"]["generic_question"]["prompt"]
            questions = [item["questionText"] for item in data.get("content", []) if item.get("type") == "question"]
            task["questions_formatted"] = "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))
            task["prompt"] = generic_prompt
        elif task["type"] == "summary":
            task["prompt"] = self.prompt_config["stages"]["Chapter-3"]["generic_summary"]["prompt"]
            task["summary_topic"] = data.get("title", key)
        return task

    async def _process_ai_subchapter(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Generates content for a single AI-driven subchapter."""
        key, schema_path = task["key"], task["schema_path"]
        prompt = task["prompt"].format(questions=task["questions_formatted"])
        uris = self.rag_client.get_gcs_uris_for_categories(task.get("source_categories"))
        
        if not uris and task.get("source_categories") is not None:
             return {key: {"error": f"No source documents for categories: {task.get('source_categories')}"}}
        try:
            data = await self.ai_client.generate_json_response(prompt, self._load_asset_json(schema_path), uris, f"Chapter-3: {key}")
            if key == "aktualitaetDerReferenzdokumente":
                coverage_finding = self._check_document_coverage()
                if coverage_finding['category'] != 'OK': data['finding'] = coverage_finding
            return {key: data}
        except Exception as e:
            logging.error(f"Failed to generate for {key}: {e}", exc_info=True)
            return {key: {"error": str(e)}}

    async def _process_summary_subchapter(self, task: Dict[str, Any], previous_findings: str) -> Dict[str, Any]:
        """Generates a summary/verdict for a subchapter."""
        key = task["key"]
        prompt = task["prompt"].format(summary_topic=task["summary_topic"], previous_findings=previous_findings)
        try:
            return {key: await self.ai_client.generate_json_response(prompt, self._load_asset_json(task["schema_path"]), request_context_log=f"Chapter-3 Summary: {key}")}
        except Exception as e:
            return {key: {"error": str(e)}}

    def _get_findings_from_results(self, results_list: List[Dict]) -> str:
        """Extracts and formats findings from a list of results for summary prompts."""
        findings = []
        for res_dict in results_list:
            if not res_dict: continue
            result_data = list(res_dict.values())[0]
            if isinstance(result_data, dict) and isinstance(result_data.get('finding'), dict):
                finding = result_data['finding']
                if finding.get('category') != "OK":
                    findings.append(f"- [{finding.get('category')}]: {finding.get('description')}")
        return "\n".join(findings) if findings else "No specific findings were generated."

    async def run(self, force_overwrite: bool = False) -> dict:
        """Executes the dynamically generated plan for Chapter 3."""
        logging.info(f"Executing dynamically generated plan for stage: {self.STAGE_NAME}")
        
        aggregated_results, processed_results = {}, []
        custom_tasks = [t for t in self.execution_plan if t and t.get("type") == "custom_logic"]
        ai_tasks = [t for t in self.execution_plan if t and t.get("type") == "ai_driven"]
        summary_tasks = [t for t in self.execution_plan if t and t.get("type") == "summary"]

        for task in custom_tasks:
            key = task['key']
            logging.info(f"--- Processing custom logic task: {key} ---")
            if key == 'detailsZumItGrundschutzCheck':
                result = await self._process_details_zum_it_grundschutz_check(force_remap=force_overwrite)
                processed_results.append(result)
                aggregated_results.update(result)
        
        if ai_tasks:
            ai_coroutines = [self._process_ai_subchapter(task) for task in ai_tasks]
            ai_results = await asyncio.gather(*ai_coroutines)
            processed_results.extend(ai_results)
            for res in ai_results: aggregated_results.update(res)

        if summary_tasks:
            findings_text = self._get_findings_from_results(processed_results)
            summary_coroutines = [self._process_summary_subchapter(task, findings_text) for task in summary_tasks]
            for res in await asyncio.gather(*summary_coroutines): aggregated_results.update(res)

        logging.info(f"Successfully aggregated results for all of stage {self.STAGE_NAME}")
        return aggregated_results
    # endregion```