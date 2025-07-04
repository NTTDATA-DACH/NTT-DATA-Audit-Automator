# BSI Grundschutz Audit Automator

This project automates BSI Grundschutz security audits by transforming customer documentation into a structured report using a cloud-native, multi-stage pipeline on Google Cloud. It leverages a Retrieval-Augmented Generation (RAG) pattern with the Vertex AI Gemini API to ensure audit findings are contextually relevant and accurate.

## End-to-End Workflow

1.  **Infrastructure Deployment:** Use Terraform in the `terraform/` directory to create the GCS Bucket, VPC Network, and Vertex AI resources.
2.  **Upload Customer Documents:** Upload the customer's documentation (PDFs, etc.) to the `source_documents/` path in the GCS bucket created by Terraform.
3.  **Execute the ETL Job:** Run the ETL task first. This is a prerequisite for all other stages. It processes the source documents and populates the Vector Search Index.
    ```bash
    # Use the interactive script and select "Run ETL (Embedding)"
    bash ./scripts/execute-audit-job.sh
    ```
4.  **Execute Audit Stages:** Once the ETL is complete, run the audit stages. You can run them all at once or one by one. The controller collects all findings (deviations/recommendations) as it goes.
    ```bash
    # To run all stages sequentially
    bash ./scripts/execute-audit-job.sh # Select "Run All Audit Stages"
    ```
5.  **Generate the Final Report:** After the stages are complete, this task assembles the final report, populating the content from each stage's output and filling the findings tables in Chapter 7.2 from the centrally collected `all_findings.json` file.
    ```bash
    bash ./scripts/execute-audit-job.sh # Select "Generate Final Report"
    ```
6.  **Manual Review:** Open the generated `final_audit_report.json` in the `report_editor.html` tool to perform the final manual review, fill in placeholder sections, and make any necessary adjustments.

## The Audit Stages Explained

*   **Phase 0: ETL (Embedding)** (`etl/processor.py`): The mandatory first step. Extracts text from source PDFs, chunks it, generates vector embeddings, and uploads the data for the Vector Index to ingest. It's idempotent and robust, skipping already processed or failed files.

*   **Chapter 1: General Information** (`audit/stages/stage_1_general.py`): Generates the introductory content.
    *   **1.2 (Scope):** Uses RAG to generate a description of the audit scope and a structured finding on its quality.
    *   **1.4 (Audit Team):** Intentionally left as a placeholder for manual input in the report editor.

*   **Chapter 3: Document Review** (`audit/stages/stage_3_dokumentenpruefung.py`): Performs a deep analysis of core documents. Each subchapter (3.1, 3.2, etc.) runs as a parallel RAG task, generating answers to specific questions and a structured finding. A final summary (3.9) is generated based on the findings from the preceding subchapters.

*   **Chapter 4: Audit Plan Creation** (`audit/stages/stage_4_pruefplan.py`): Generates the audit plan. This stage is **conditional** on the `AUDIT_TYPE` environment variable, using different prompts and rules for a certification vs. a surveillance audit.

*   **Chapter 5: On-Site Audit** (`audit/stages/stage_5_vor_ort_audit.py`): Prepares materials for the human auditor.
    *   **5.1 (ISMS Effectiveness):** Intentionally left as a placeholder for manual input based on interviews.
    *   **5.5.2 (Control Verification):** This task is **deterministic**. It reads the audit plan from Chapter 4, looks up all required controls from the BSI OSCAL catalog, and generates a structured checklist for the auditor to use on-site. It does **not** use AI.

*   **Chapter 7: Appendix** (`audit/stages/stage_7_anhang.py`): Generates the report's appendix.
    *   **7.1 (Reference Documents):** A **deterministic** task that lists all files found in the source GCS folder.
    *   **7.2 (Deviations & Recommendations):** This section is **populated by the Report Generator**, not the Chapter 7 runner. It uses the `all_findings.json` file (collected by the `AuditController` across all stages) to build the final, categorized tables of findings.