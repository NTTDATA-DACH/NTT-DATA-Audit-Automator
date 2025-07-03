### **New Prioritized TODO List**

Here is the updated and re-prioritized list of tasks that are genuinely still outstanding.

*   **[✅ COMPLETED] Implement the Real AI Client.**
    *   **Status:** The code in `src/clients/ai_client.py` now makes real calls to the Vertex AI Gemini API. This task is done.

*   **[✅ COMPLETED] Implement RAG Context Retrieval.**
    *   **Status:** The `RagClient` in `src/clients/rag_client.py` is fully functional and is correctly used by the stage runners (e.g., `stage_1_general.py`) to inject evidence into the prompts. This task is done.

---
*   **[ ] TODO 1 (Previously TODO 2): Implement "Two-Plus-One" Verification.**
    *   **File:** `src/clients/ai_client.py`
    *   **Action:** Refactor the `generate_json_response` function. Instead of making a single AI call, it should make two parallel calls for the initial prompt, then construct a new "synthesis prompt" to have the AI review and merge the two initial results into a final, higher-quality response. This enhances accuracy and reliability.

*   **[ ] TODO 2 (Previously TODO 4): Make Audit Planning (Stage 4) Conditional on Audit Type.**
    *   **File:** `src/audit/stages/stage_4_pruefplan.py`
    *   **Action:** Modify the `Chapter4Runner`. It must read the `self.config.audit_type` environment variable. Based on whether the type is "Zertifizierungsaudit" or "Überwachungsaudit", it must load different subchapter definitions and use different prompts to align with the distinct rules in the `Auditierungsschema.pdf`.

*   **[ ] TODO 3 (Previously TODO 5): Create Prompts and Schemas for a Surveillance Audit Plan.**
    *   **Files:** `assets/prompts/`, `assets/schemas/`
    *   **Action:** Create the new asset files required for TODO 2. Specifically, create `stage_4_1_2_auswahl_bausteine_ueberwachung.txt` and its corresponding schema. This prompt must instruct the AI on the specific rules for a surveillance audit (e.g., "ISMS.1 is mandatory, plus at least two other Bausteine").

*   **[ ] TODO 4 (Previously TODO 6): Implement Location Audit Planning (Standortauswahl).**
    *   **File:** `src/audit/stages/stage_4_pruefplan.py`
    *   **Action:** Add a new subchapter processor within the `Chapter4Runner` to handle section 4.1.4 ("Auswahl Standorte"). This function will need to:
        1.  List the available locations (this may require a RAG query).
        2.  Calculate the required number of sites to audit based on the formulas in the `Auditierungsschema` (e.g., `sqrt(n)` for initial, `sqrt(n) * 0.6` for surveillance).
        3.  Instruct the AI via a new prompt to select a risk-based sample of the calculated size from the list of available locations.

*   **[ ] TODO 5 (Previously TODO 7): Implement Risk Measure Verification in Stage 5.**
    *   **File:** `src/audit/stages/stage_5_vor_ort_audit.py`
    *   **Action:** Add logic to the `Chapter5Runner` to handle the verification of the mandatory risk analysis measures. This will likely involve adding a new subchapter processor for section 5.6 of the report, which will use RAG to find evidence related to the specific risk measures selected in the audit plan (from Stage 4).