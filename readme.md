# BSI Grundschutz Audit Automator

This project automates BSI Grundschutz security audits by transforming customer documentation into a structured report using a cloud-native, multi-stage pipeline on Google Cloud. It leverages the Vertex AI Gemini API with a "Document Finder" model to ensure audit findings are contextually relevant, evidence-based, and accurate.

## End-to-End Workflow

### Managing Audit Data: Refresh vs. Reset
Before starting, choose the correct method to prepare your environment.

#### **Option 1: Fast Refresh (Recommended for Data Updates)**
Use this when you get new source files for an audit and want to start over without touching the cloud infrastructure.

1.  **Run the Refresh Script:** This moves old data to an archive.
    ```bash
    bash ./scripts/refresh_audit_data.sh
    ```
2.  **Confirm the Action:** Type `y` to proceed.
3.  **Proceed to the Standard Workflow below.**

#### **Option 2: Full Reset (For Infrastructure Changes)**
Use this only if you need to start from a "scorched-earth" state, for example, for a new customer or if you've changed the Terraform configuration.

1.  **Run the Reset Script:** This wipes all GCS data.
    ```bash
    bash ./scripts/reset_audit.sh
    ```
2.  **Recreate Infrastructure (if needed):** Follow the script's instructions to run `terraform apply`.
    ```bash
    cd ../terraform
    terraform apply -auto-approve
    cd ..
    ```
3.  **Proceed to the Standard Workflow below.**

### Standard Workflow

1.  **Infrastructure Deployment:** If this is the very first run, use Terraform in the `terraform/` directory to create the GCS Bucket, VPC Network, and all necessary Vertex AI and IAM resources.
2.  **Upload Customer Documents:** Upload the customer's documentation (PDFs), including any previous audit reports, to the `source_documents/` path in the GCS bucket.
3.  **Deploy the Job Container:** Build and deploy the application container to Cloud Run Jobs.
    ```bash
    # Run from the project root
    bash ./scripts/deploy-audit-job.sh
    ```
4.  **Execute Audit Tasks:** Run the desired audit task using the interactive execution script. The first time a task is run, the system will **automatically classify all source documents** if a classification map doesn't already exist.
    ```bash
    # This script provides an interactive menu for all tasks.
    bash ./scripts/execute-audit-job.sh
    ```
    *   To run the entire pipeline, select **"Run All Audit Stages"**. This will execute all steps in the correct prerequisite order, starting with scanning the previous report.
    *   To run only the report scanning feature, select **"Scan Previous Audit Report"**.
5.  **Generate the Final Report:** After the stages are complete, this task assembles the final report from all generated components.
    ```bash
    # Select "Generate Final Report" from the menu
    bash ./scripts/execute-audit-job.sh
    ```
6.  **Manual Review and Finalization:** Open the generated `report-YYMMDD.json` from the `output/` GCS prefix in the `report_editor.html` tool to perform the final manual review and make any necessary adjustments.

## The Audit Stages Explained

The audit pipeline runs in a strict, dependency-aware order.

*   **Phase 0: Document Classification (On-Demand)** (`src/clients/rag_client.py`): This is an automated, on-demand first step that is triggered by other stages. If the `output/document_map.json` file does not exist, the `RagClient` (acting as a "Document Finder") will use an AI call to classify all source document *filenames* into BSI-specific categories (e.g., "Strukturanalyse", "Vorheriger-Auditbericht"). This map is then saved and used by all subsequent stages.

*   **Stage: Scan Previous Report (Prerequisite)** (`audit/stages/stage_previous_report_scan.py`): This is the first operational stage in a full run. It finds the document classified as `Vorheriger-Auditbericht` and runs three parallel AI extractions to pull structured data for Chapters 1.1-1.3 (General Info), 4.1.1-4.1.2 (Previous Audit Scope), and 7.2 (Previous Findings) into `output/results/Scan-Report.json`.

*   **Stage: Chapter 4 - Audit Plan Creation (Prerequisite)** (`audit/stages/stage_4_pruefplan.py`): This stage runs next to generate the audit plan. It is **conditional** on the `AUDIT_TYPE` environment variable, using different prompts and rules for a "Zertifizierungsaudit" vs. a "Überwachungsaudit".

*   **Stage: Chapter 1 - General Information** (`audit/stages/stage_1_general.py`): Generates introductory content for the report.
    *   **1.4 (Informationsverbund):** Uses the "Document Finder" to retrieve relevant documents and generate a description of the audit scope.
    *   Other sections are intentionally left as placeholders for manual input.

*   **Stage: Chapter 3 - Document Review** (`audit/stages/stage_3_dokumentenpruefung.py`): Performs a deep analysis of core documents. For most subchapters, it uses the Document Finder to retrieve a small, relevant set of documents for the AI to analyze. For the critical `Grundschutz-Check` analysis (3.6.1), it employs a sophisticated **Ground-Truth-Driven Semantic Chunking** strategy to ensure maximum accuracy.

*   **Stage: Chapter 5 - On-Site Audit Preparation** (`audit/stages/stage_5_vor_ort_audit.py`): Prepares materials for the human auditor.
    *   **5.5.2 (Control Verification):** This task is **deterministic**. It reads the audit plan from Chapter 4's output, looks up all required controls from the BSI OSCAL catalog, and enriches this list with the customer's implementation details from the data generated by Chapter 3. This generates a structured checklist for the auditor and does **not** use AI.

*   **Stage: Chapter 7 - Appendix** (`audit/stages/stage_7_anhang.py`): Generates content for the report's appendix.
    *   **7.1 (Reference Documents):** A **deterministic** task that lists all files found in the source GCS folder.
    *   **7.2 (Abweichungen und Empfehlungen):** This section is populated by the separate `ReportGenerator` task, which reads the centrally collected `all_findings.json` file.