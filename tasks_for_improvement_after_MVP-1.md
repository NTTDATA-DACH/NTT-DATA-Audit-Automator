# Tasks for Improvement After MVP-1

This document lists prioritized tasks for improving the BSI Audit Automator codebase after its initial functional state.

---

### **High Priority (Bugs & Inconsistencies)**

1.  **[BUG] Re-enable Test Mode File Limiting in RagClient**
    *   **File:** `src/clients/rag_client.py`
    *   **Issue:** The logic to limit the number of GCS files sent to the AI model when `TEST="true"` is currently commented out. This violates the requirement in the project brief (Section 4.6) to limit processing in test mode.
    *   **Action:** Uncomment the block that limits the `uris` list based on `self.config.is_test_mode`. This is crucial for rapid, cost-effective testing.

---

### **Medium Priority (Maintainability & Clarity)**

2.  **[REFACTOR] Simplify CLI Arguments in `main.py`**
    *   **File:** `src/main.py`
    *   **Issue:** The CLI has redundant arguments: `--scan-previous-report` and `--run-gs-check-extraction`. Their functionality is already covered by the more generic `--run-stage` argument (e.g., `--run-stage Scan-Report`).
    *   **Action:** Remove the two redundant `group.add_argument` calls and update the `if/elif` block to only use `args.run_stage`. This will simplify the CLI, reduce code, and make the entry point easier to maintain.

3.  **[CLARITY] Clean Up Confusing Comment in `ai_client.py`**
    *   **File:** `src/clients/ai_client.py`
    *   **Issue:** The comment for `max_output_tokens` is self-contradictory and confusing (`Increased from 65k to 8k as 65k is invalid...`). The code now correctly uses the value from the brief (`65536`), but the comment creates confusion about the model's actual capabilities.
    *   **Action:** Replace the old, confusing comment with a clear and concise one explaining the parameter's purpose, aligning with the current code.

---

### **Low Priority (Robustness & Future-Proofing)**

4.  **[REFACTOR] Centralize Intermediate File Path Constants**
    *   **Files:** `src/audit/stages/stage_gs_check_extraction.py`, `src/audit/stages/stage_3_dokumentenpruefung.py`, `src/audit/stages/stage_5_vor_ort_audit.py`
    *   **Issue:** The GCS paths for intermediate files (`GROUND_TRUTH_MAP_PATH`, `INTERMEDIATE_CHECK_RESULTS_PATH`) are hardcoded as string literals in multiple files. If a path changes, it must be updated in several places, risking inconsistency.
    *   **Action:** Define these paths as constants in a central location, such as `src/config.py` or a new `src/audit/constants.py` file, and import them where needed. This improves maintainability and reduces "magic strings".

5.  **[REFACTOR] Encapsulate Finding Collection Logic**
    *   **File:** `src/audit/controller.py`
    *   **Issue:** The logic for managing findings (collecting, processing previous findings, assigning new IDs) is spread across several methods within the `AuditController` (`_process_previous_findings`, `_process_new_finding`, `_extract_and_store_findings`, `_save_all_findings`).
    *   **Action:** Consider creating a dedicated `FindingCollector` class. This class would encapsulate all state (the list of findings, the counters) and logic for adding, processing, and saving findings. The `AuditController` would then delegate all finding-related operations to an instance of this new class, improving separation of concerns.