### **Project Improvement Roadmap**

This document tracks completed enhancements and the prioritized backlog of features and technical debt to address after the initial Minimum Viable Product (MVP) is stable.

---

### **Recently Completed Milestones**

*   **[✅ COMPLETED] Implement the Real AI Client:** The code in `src/clients/ai_client.py` now makes real calls to the Vertex AI Gemini API.
*   **[✅ COMPLETED] Implement RAG Context Retrieval:** The `RagClient` in `src/clients/rag_client.py` is fully functional and is correctly used by the stage runners to inject evidence into the prompts.
*   **[✅ COMPLETED] Implement Idempotent ETL:** The ETL processor in `src/etl/processor.py` now creates `.success` status markers, ensuring that re-running the ETL process does not re-process completed files.
*   **[✅ COMPLETED] Implement Private VPC Networking for Cloud Run:** The deployment scripts and Terraform configuration have been updated to correctly deploy the Cloud Run job into a private VPC and connect it to the Vector Search endpoint's private IP, resolving all gRPC connection errors.
*   **[✅ COMPLETED] Refactor State Management:** The `AuditController` now correctly overwrites results for single-stage runs (`--run-stage`) while preserving resumability for full runs (`--run-all-stages`).

---

### **New Prioritized TODO List**

*   **[ ] TODO 1: Optimize RagClient Memory Usage.**
    *   **File:** `src/clients/rag_client.py`
    *   **Action:** Currently, the `RagClient` loads the full text of every chunk from every document into memory at startup. This could exhaust the Cloud Run instance's memory on very large audits. Refactor `_load_chunk_lookup_map` to be more memory-efficient. For example, it could map `chunk_id -> source_document_name` only. When a lookup is needed, it would then open only the required source document's embedding file to retrieve the text, drastically reducing the initial memory footprint.

*   **[ ] TODO 2: Implement "Two-Plus-One" Verification.**
    *   **File:** `src/clients/ai_client.py`
    *   **Action:** Refactor the `generate_json_response` function. Instead of making a single AI call, it should make two parallel calls for the initial prompt, then construct a new "synthesis prompt" to have the AI review and merge the two initial results into a final, higher-quality response. This enhances accuracy and reliability as outlined in `AI_Data_Processing_Strategy.md`.

*   **[ ] TODO 3: Make Audit Planning (Stage 4) Conditional on Audit Type.**
    *   **File:** `src/audit/stages/stage_4_pruefplan.py`
    *   **Action:** Modify the `Chapter4Runner`. It must read the `self.config.audit_type` environment variable. Based on whether the type is "Zertifizierungsaudit" or "Überwachungsaudit", it must load different subchapter definitions and use different prompts to align with the distinct rules in the BSI `Auditierungsschema`.

*   **[ ] TODO 4: Create Prompts and Schemas for a Surveillance Audit Plan.**
    *   **Files:** `assets/prompts/`, `assets/schemas/`
    *   **Action:** Create the new asset files required for TODO 3. Specifically, create `stage_4_1_2_auswahl_bausteine_ueberwachung.txt` and its corresponding schema. This prompt must instruct the AI on the specific rules for a surveillance audit (e.g., "ISMS.1 is mandatory, plus at least two other Bausteine").

*   **[ ] TODO 5: Implement Location Audit Planning (Standortauswahl).**
    *   **File:** `src/audit/stages/stage_4_pruefplan.py`
    *   **Action:** Add a new subchapter processor within the `Chapter4Runner` to handle section 4.1.4 ("Auswahl Standorte"). This function will need to use RAG to list available locations and then use the formulas in the `Auditierungsschema` (e.g., `sqrt(n)`) to instruct the AI to select a risk-based sample of sites to audit.

*   **[ ] TODO 6: Implement Unit and Integration Tests.**
    *   **Directory:** `tests/`
    *   **Action:** The project currently lacks automated tests. Create a testing suite using a framework like `pytest`. Add unit tests for individual functions (e.g., in `etl/processor.py`) and integration tests for client interactions, using mock objects (`unittest.mock`) to simulate GCS and AI API calls.

*   **[ ] TODO 7: Improve ETL Error Handling with `.failed` Status Markers.**
    *   **File:** `src/etl/processor.py`
    *   **Action:** Enhance the idempotent ETL process. If a document consistently fails to be processed (e.g., it's corrupted), the processor should create a `.failed` status marker in the `output/etl_status/` directory. This prevents the pipeline from re-attempting a known-bad file on every run and provides a clear signal for manual intervention.