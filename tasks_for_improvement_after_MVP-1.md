### **Project Improvement Roadmap**

This document tracks completed enhancements and the prioritized backlog of features and technical debt to address.

---

### **Completed Milestones (as of this review)**

*   **[✅] Implemented Full RAG Pipeline:** The `RagClient` is functional and integrated into all relevant stages (Chapters 1 & 3) to provide evidence-based context to prompts.
*   **[✅] Implemented Centralized Finding Collection:** The `AuditController` now systematically collects structured findings from all stages, and the `ReportGenerator` populates them into Chapter 7.2.
*   **[✅] Implemented Conditional Audit Planning (Stage 4):** The `Chapter4Runner` now correctly loads different prompts and schemas based on the `AUDIT_TYPE`, supporting both "Zertifizierungsaudit" and "Überwachungsaudit".
*   **[✅] Refactored Chapter 5 for Manual Audit:** The `Chapter5Runner` no longer simulates the audit for 5.5.2. Instead, it deterministically generates a control checklist for the human auditor using the `ControlCatalog`. Automated generation for 5.1 has been removed.
*   **[✅] Refactored Chapter 1 for Accuracy:** The `Chapter1Runner` now uses a stricter prompt for 1.2 to prevent hallucination and includes finding-generation logic. Processing for 1.4 has been correctly removed.
*   **[✅] Bugfix - Chapter 3 Aggregation:** Fixed the aggregation logic in the `Chapter3Runner` to correctly parse the new structured `finding` object.
*   **[✅] Bugfix - Report Editor Table Functionality:** Fixed the JavaScript logic to allow adding/deleting rows in all tables, including the nested findings tables in Chapter 7.
*   **[✅] Implemented Idempotent & Robust ETL:** The ETL processor in `src/etl/processor.py` now creates `.success` and `.failed` status markers, ensuring resilience and preventing reprocessing of files.

---

### **New Prioritized TODO List**

*   **[ ] TODO 1 (CRITICAL-BUG): Fix `KeyError` in ReportGenerator.**
    *   **Files:** `src/audit/report_generator.py`, `src/main.py`
    *   **Action:** The pipeline is crashing during the report generation step with a `KeyError: 'geltungsbereichDerZertifizierung'`. This indicates a structural mismatch between the data being written and the master report template. The `_populate_chapter_1` function is attempting to access a key that does not exist in the report structure it's populating.
    *   **Solution:** Make the population logic in `report_generator.py` more defensive. Before attempting to write data (e.g., `target_chapter[key] = ...`), the code must first verify that `key` exists in `target_chapter`. If not, it should log a detailed warning and retry that specific population step, preventing a crash.

*   **[ ] TODO 2: Optimize RagClient Memory Usage.**
    *   **File:** `src/clients/rag_client.py`
    *   **Action:** Currently, the `RagClient` loads the full text of every chunk from every document into memory at startup. This could exhaust the Cloud Run instance's memory on very large audits. Refactor `_load_chunk_lookup_map` to be more memory-efficient. For example, it could map `chunk_id -> source_document_name` only. When a lookup is needed, it would then open only the required source document's embedding file to retrieve the text, drastically reducing the initial memory footprint.

*   **[ ] TODO 3: Implement "Two-Plus-One" Verification.**
    *   **File:** `src/clients/ai_client.py`
    *   **Action:** Refactor the `generate_json_response` function. Instead of making a single AI call, it should make two parallel calls for the initial prompt, then construct a new "synthesis prompt" to have the AI review and merge the two initial results into a final, higher-quality response. This enhances accuracy and reliability.

*   **[ ] TODO 4: Implement Location Audit Planning (Standortauswahl).**
    *   **File:** `src/audit/stages/stage_4_pruefplan.py`
    *   **Action:** Add a new subchapter processor within the `Chapter4Runner` to handle section 4.1.4 ("Auswahl Standorte"). This function will need to use RAG to list available locations and then use the formulas in the BSI `Auditierungsschema` (e.g., `sqrt(n)`) to instruct the AI to select a risk-based sample of sites to audit.

*   **[ ] TODO 5: Automate Check of "Entbehrliche Bausteine" & Justifications.**
    *   **Action:** Develop a logic that generates a list of all controls marked as "Entbehrlich" (dispensable) from the "A.4 Grundschutz-Check" document. For each item, use a focused RAG prompt to ask the AI if the provided justification ("Begründung") is sufficient and plausible according to BSI standards. This also covers checking all justifications in A.4.

*   **[ ] TODO 6: Automate Check of Strukturanalyse vs. Netzplan.**
    *   **Action:** For checks if all Objects mentioned in the Strukturanalyse and the Netzplan are present in the other document, develop a logic that generates a list of all objects in each file and then compares the lists to find discrepancies.

*   **[ ] TODO 7: Automate Check of Modellierung (Baustein Application).**
    *   **Action:** Check if the correct and all required Bausteine are listed for each object in the A.4 Grundschutz-Check, cross-referencing against the BSI modeling requirements.

*   **[ ] TODO 8: Automate Check of Risk Analysis & Additional Controls.**
    *   **Action:** Check that objects identified with a 'high' or 'very high' protection requirement in the Strukturanalyse have corresponding additional security controls defined. Verify that these additional controls are correctly included in the A.4 Grundschutz-Check.

*   **[ ] TODO 9: Automate Check of Risk Treatment for Unimplemented Controls (A.6).**
    *   **Action:** For all controls listed in A.6 (Realisierungsplan) that are not yet fully implemented, verify that they are listed and that the risk treatment plan is well-explained and plausible.

*   **[ ] TODO 10: Implement a Comprehensive Test Suite.**
    *   **Directory:** `tests/`
    *   **Action:** The project currently lacks automated tests. Create a testing suite using a framework like `pytest`. Add unit tests for individual functions (e.g., in `etl/processor.py`). Add integration tests for client interactions, using mock objects (`unittest.mock`) to simulate GCS and AI API calls. This is also a way to "deterministically check results" by providing fixed inputs and asserting expected outputs from business logic.

*   **[ ] TODO 11: Improve Prompts and Schemas.**
    *   **Directory:** `assets/`
    *   **Action:** Continuously refine the prompts in `assets/prompts/` and schemas in `assets/schemas/` to improve the quality, accuracy, and consistency of the AI-generated results.

*   **[ ] TODO 12: Implement Language Selection for AI Output.**
    *   **Action:** Add an environment variable (e.g., `OUTPUT_LANGUAGE`) to allow the user to select the language for the AI's narrative output (e.g., 'German', 'English'). Update all prompts to include a placeholder for the language, which will be formatted by the stage runners.

*   **[ ] TODO 13: Fix bug A**
    *   **Directory:** ``
    *   **Action:** Running report generation gives this error:
```
    Traceback (most recent call last):
  File "/app/src/main.py", line 66, in main
    generator.assemble_report()
  File "/app/src/audit/report_generator.py", line 116, in assemble_report
    self._populate_report(report, stage_name, stage_data)
  File "/app/src/audit/report_generator.py", line 189, in _populate_report
    self._populate_chapter_1(report, stage_data)
  File "/app/src/audit/report_generator.py", line 57, in _populate_chapter_1
    target_chapter['geltungsbereichDerZertifizierung']['content'][0]['text'] = final_text
```

*   **[ ] TODO 14 (Code Quality): Implement Comprehensive Docstrings.**
    *   **Files:** `src/etl/processor.py`, `src/audit/controller.py`, `src/audit/report_generator.py`, all files in `src/audit/stages/`.
    *   **Action:** Systematically review and add structured docstrings to all public classes and functions that are currently missing them. Ensure every docstring includes the purpose, an `Args:` section, and a `Returns:` section.
    *   **Example:**
        *   **Before:**
            ```python
            def _sanitize_filename(self, filename: str) -> str:
                # ...
            ```
        *   **After:**
            ```python
            def _sanitize_filename(self, filename: str) -> str:
                """Removes special characters to create a valid GCS object name.

                Args:
                    filename: The original filename, which may contain paths and special characters.

                Returns:
                    A sanitized string suitable for use as a GCS object name.
                """
                # ...
            ```

*   **[ ] TODO 15 (Code Quality): Enforce Strict Type Hinting.**
    *   **Files:** `src/etl/processor.py`, `src/audit/controller.py`, `src/audit/report_generator.py`, all files in `src/audit/stages/`.
    *   **Action:** Add missing type hints to function arguments and return values across the identified modules. Pay special attention to function return types, such as `-> None`, `-> dict`, or `-> str`.
    *   **Example:**
        *   **Before:**
            ```python
            async def run(self):
                # ...
            ```
        *   **After:**
            ```python
            async def run(self) -> Dict[str, Any]:
                # ...
            ```
        *   **Or for methods with no return:**
            ```python
            def _save_all_findings(self) -> None:
                # ...
            ```