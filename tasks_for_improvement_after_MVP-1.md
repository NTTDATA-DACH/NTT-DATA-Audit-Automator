### **Project Improvement Roadmap**

This document tracks completed enhancements and the prioritized backlog of features and technical debt to address.

---

### **Recently Completed Milestones**

*   **[✅ COMPLETED] Implement Full RAG Pipeline:** The `RagClient` is functional and integrated into all relevant stages (Chapters 1 & 3) to provide evidence-based context to prompts.
*   **[✅ COMPLETED] Centralized Finding Collection:** The `AuditController` now systematically collects structured findings from all stages, and the `ReportGenerator` populates them into Chapter 7.2.
*   **[✅ COMPLETED] Conditional Audit Planning (Stage 4):** The `Chapter4Runner` now correctly loads different prompts and schemas based on the `AUDIT_TYPE`, supporting both "Zertifizierungsaudit" and "Überwachungsaudit".
*   **[✅ COMPLETED] Refactor Chapter 5 for Manual Audit:** The `Chapter5Runner` no longer simulates the audit for 5.5.2. Instead, it deterministically generates a control checklist for the human auditor. Automated generation for 5.1 has been removed.
*   **[✅ COMPLETED] Refactor Chapter 1 for Accuracy:** The `Chapter1Runner` now uses a much stricter prompt for 1.2 to prevent hallucination and includes finding-generation logic. Processing for 1.4 has been correctly removed.
*   **[✅ COMPLETED] Bugfix - Chapter 3 `null` Results:** Fixed the aggregation logic in the `Chapter3Runner` to correctly parse the new structured `finding` object.
*   **[✅ COMPLETED] Bugfix - Report Editor:** Fixed the JavaScript logic to allow adding/deleting rows in all tables, including the nested findings tables in Chapter 7.
*   **[✅ COMPLETED] Implement Idempotent & Robust ETL:** The ETL processor in `src/etl/processor.py` now creates `.success` and `.failed` status markers, ensuring resilience and preventing reprocessing of files.

---

### **New Prioritized TODO List**

*   **[ ] TODO 1: Optimize RagClient Memory Usage.**
    *   **File:** `src/clients/rag_client.py`
    *   **Action:** Currently, the `RagClient` loads the full text of every chunk from every document into memory at startup. This could exhaust the Cloud Run instance's memory on very large audits. Refactor `_load_chunk_lookup_map` to be more memory-efficient. For example, it could map `chunk_id -> source_document_name` only. When a lookup is needed, it would then open only the required source document's embedding file to retrieve the text, drastically reducing the initial memory footprint.

*   **[ ] TODO 2: Implement "Two-Plus-One" Verification.**
    *   **File:** `src/clients/ai_client.py`
    *   **Action:** Refactor the `generate_json_response` function. Instead of making a single AI call, it should make two parallel calls for the initial prompt, then construct a new "synthesis prompt" to have the AI review and merge the two initial results into a final, higher-quality response. This enhances accuracy and reliability as outlined in `AI_Data_Processing_Strategy.md`.

*   **[ ] TODO 3: Implement Location Audit Planning (Standortauswahl).**
    *   **File:** `src/audit/stages/stage_4_pruefplan.py`
    *   **Action:** Add a new subchapter processor within the `Chapter4Runner` to handle section 4.1.4 ("Auswahl Standorte"). This function will need to use RAG to list available locations and then use the formulas in the BSI `Auditierungsschema` (e.g., `sqrt(n)`) to instruct the AI to select a risk-based sample of sites to audit.

*   **[ ] TODO 4: Find ways to determinstic check results**
    *   **Directory:** `tests/`
    *   **Action:** Check all stages if we can check the results of the AI

*   **[ ] TODO 5: Improve the prompts and schemas.**
    *   **Directory:** `assets/`
    *   **Action:** Get better results

*   **[ ] TODO 6: Checking of Entbehrliche**
    *   **Directory:** ``
    *   **Action:** for checks of "Entbehrliche, develop a logic, that generates a list of all "Entbehrlich" from "A.4 Grundchutz Check" and goes through it to ask AI if the Begründung is sufficient

*   **[ ] TODO 7: Checking of Strukturanalyse and Netzplan**
    *   **Directory:** ``
    *   **Action:** for checks if all Objects mentioned in one of the two are in the other one as well, develop a logic, that generates a list of all objects in each file and checks if that matches

*   **[ ] TODO 8: Modellierung**
    *   **Directory:** ``
    *   **Action:** are the correct and all required bausteine listed for each object in A.4 and A.4?

*   **[ ] TODO 9: Implement Unit and Integration Tests.**
    *   **Directory:** `tests/`
    *   **Action:** The project currently lacks automated tests. Create a testing suite using a framework like `pytest`. Add unit tests for individual functions (e.g., in `etl/processor.py`) and integration tests for client interactions, using mock objects (`unittest.mock`) to simulate GCS and AI API calls.

*   **[ ] TODO 11: MAybe check all the "begründungen" in A.4**
    *   **Directory:** ``
    *   **Action:** Are they sensible and correct?

*   **[ ] TODO 12: Check Risk Analysis**
    *   **Directory:** ``
    *   **Action:** Check that objects that have a high or very high risk have additional controls
    *   **Action:** Check if those controls are included in the A.4
    
*   **[ ] TODO 13: All controls not implemented in A.6**
    *   **Directory:** ``
    *   **Action:** Are they listed and is the risk treatment well explained?

*   **[ ] TODO 14: Fix bug A**
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

*   **[ ] TODO 14: Fix bug B**
    *   **Directory:** ``
    *   **Action:** Running report generation gives this error:
```
CRITICAL - A critical error occurred in the pipeline: 'geltungsbereichDerZertifizierung'
KeyError: 'geltungsbereichDerZertifizierung'"
```

*   **[ ] TODO 14: language settings**
    *   **Directory:** ``
    *   **Action:** Select the language for the comments by AI










