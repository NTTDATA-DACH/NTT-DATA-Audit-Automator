# BSI Grundschutz Audit Automator

This project automates BSI Grundschutz security audits by transforming customer documentation into a structured report using a cloud-native, multi-stage pipeline on Google Cloud. It leverages a Retrieval-Augmented Generation (RAG) pattern with the Vertex AI Gemini API to ensure audit findings are contextually relevant, evidence-based, and accurate.

## End-to-End Workflow

### Starting a New Audit or Resetting Data
**Use this process when starting with a new customer or when source documents have changed significantly.** This ensures no data from a previous audit contaminates the new one.

1.  **Run the Reset Script:** From the project root (`bsi-audit-automator/`), execute the reset script. This will wipe all data from GCS and mark the Vertex AI index for recreation.
    ```bash
    bash ./scripts/reset_audit.sh
    ```
2.  **Confirm the Action:** The script will show you what it's about to delete and ask for confirmation. Type `y` and press Enter.
3.  **Recreate the Index:** Follow the script's instructions by navigating to the Terraform directory and applying the change. This destroys the old index and creates a new, empty one.
    ```bash
    cd ../terraform
    terraform apply -auto-approve
    cd ..
    ```
4.  **Proceed with the standard workflow below.** The system is now in a clean state.

### Standard Workflow

1.  **Infrastructure Deployment:** If this is the first run, use Terraform in the `terraform/` directory to create the GCS Bucket, VPC Network, and all necessary Vertex AI and IAM resources.
2.  **Upload Customer Documents:** Upload the customer's documentation (PDFs) to the `source_documents/` path in the GCS bucket created by Terraform.
3.  **Deploy the Job Container:** Build and deploy the application container to Cloud Run Jobs.
    ```bash
    # Run from the project root
    bash ./scripts/deploy-audit-job.sh
    ```
4.  **Execute the ETL Job:** Run the ETL task first using the interactive execution script. This is a **mandatory prerequisite** for all other stages. It processes the source documents and populates the Vector Search Index.
    ```bash
    # Select "Run ETL (Embedding)" from the menu
    bash ./scripts/execute-audit-job.sh
    ```
5.  **Wait for Indexing:** The Vertex AI Index automatically ingests the new embedding files from GCS. This process can take 5-20 minutes. You can monitor the "Dense vector count" on the Matching Engine page in the GCP Console to see when it's ready.
6.  **Execute Audit Stages:** Once the ETL is complete, run the audit stages. The controller collects all findings (deviations/recommendations) as it proceeds. The process is resumable; it will skip already completed stages.
    ```bash
    # To run all stages sequentially, select "Run All Audit Stages"
    bash ./scripts/execute-audit-job.sh
    ```
7.  **Generate the Final Report:** After the stages are complete, this task assembles the final report. It populates the content from each stage's output and fills the findings tables in Chapter 7.2 from the centrally collected `all_findings.json` file.
    ```bash
    # Select "Generate Final Report" from the menu
    bash ./scripts/execute-audit-job.sh
    ```
8.  **Manual Review and Finalization:** Open the generated `final_audit_report.json` located in the `output/` GCS prefix. Load it into the `report_editor.html` tool to perform the final manual review, fill in placeholder sections (like 1.4 Audit-Team), and make any necessary adjustments before exporting the final version.

## The Audit Stages Explained

*   **Phase 0: ETL (Embedding)** (`etl/processor.py`): The mandatory first step. It begins by classifying all source documents into BSI-specific categories. It then extracts text, chunks it, generates vector embeddings, and uploads the data for the Vector Index to ingest. It's idempotent and robust, using `.success` and `.failed` markers in GCS to skip already processed or failed files.

*   **Chapter 1: General Information** (`audit/stages/stage_1_general.py`): Generates introductory content.
    *   **1.2 (Scope):** Uses RAG to generate a description of the audit scope and a structured finding on its quality.
    *   **1.4 (Audit Team):** Intentionally left as a placeholder for manual input in the report editor.

*   **Chapter 3: Document Review** (`audit/stages/stage_3_dokumentenpruefung.py`): Performs a deep analysis of core documents. Each subchapter (3.1, 3.2, etc.) runs as a parallel, high-precision RAG task, filtering its search to only the relevant document categories. It generates answers to specific questions and a structured finding. A final summary (3.9) is generated based on the findings from the preceding subchapters.

*   **Chapter 4: Audit Plan Creation** (`audit/stages/stage_4_pruefplan.py`): Generates the audit plan. This stage is **conditional** on the `AUDIT_TYPE` environment variable, using different prompts and rules for a "Zertifizierungsaudit" vs. a "Überwachungsaudit".

*   **Chapter 5: On-Site Audit Preparation** (`audit/stages/stage_5_vor_ort_audit.py`): Prepares materials for the human auditor.
    *   **5.1 (ISMS Effectiveness):** Intentionally left as a placeholder for manual input.
    *   **5.5.2 (Control Verification):** This task is **deterministic**. It reads the audit plan from Chapter 4's output, looks up all required controls from the BSI OSCAL catalog (`control_catalog.py`), and generates a structured checklist for the auditor to use on-site. It does **not** use AI.

*   **Chapter 7: Appendix** (`audit/stages/stage_7_anhang.py`): Generates content for the report's appendix.
    *   **7.1 (Reference Documents):** A **deterministic** task that lists all files found in the source GCS folder.
    *   **7.2 (Deviations & Recommendations):** This section is **not** populated by the Chapter 7 runner. It is populated by the **`ReportGenerator`** at the very end, using the `all_findings.json` file (collected by the `AuditController` across all stages) to build the final, categorized tables of findings.