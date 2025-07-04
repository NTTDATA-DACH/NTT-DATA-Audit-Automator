### **Project Brief: BSI Grundschutz Audit Automation with Vertex AI**

This document outlines the requirements and development protocol for a Python-based application designed to automate BSI Grundschutz audits using the Vertex AI Gemini API.

**1. Project Overview & Objective**

The primary goal is to develop a cloud-native application that performs a security audit based on the German BSI Grundschutz framework. The application will run as a batch job on Google Cloud Platform (GCP), processing customer-provided documents against BSI standards and generating a structured audit report.

The core of the application is a **Retrieval-Augmented Generation (RAG)** pipeline. Source documents are first indexed into a Vertex AI Vector Search database. Then, for each section of the audit, the application retrieves the most relevant document excerpts to provide as context to the Gemini model, ensuring accurate, evidence-based findings.

The audit process and resulting report must be based on two key documents:
*   The relevant **BSI Grundschutz Standards** (as context for the AI).
*   The **`bsi-audit-automator/assets/json/master_report_template.json`** file, which serves as the structural and content template for the final audit report.

**2. Core Functional Requirements**

*   **Staged Audit Process:** The audit is conducted in discrete stages corresponding to the report chapters. Some sections (e.g., 1.4, 5.1) are intentionally placeholders for manual auditor input.
*   **State Management:** The application saves the results of each stage to Google Cloud Storage (GCS). For full audit runs (`--run-all-stages`), it checks for and skips previously completed stages. For single-stage runs, it overwrites existing results.
*   **Finding Collection:** The application systematically collects all structured findings (deviations and recommendations) from all stages into a central `all_findings.json` file.
*   **Audit Type Configuration:** The application is configurable for "Überwachungsaudit" or "Zertifizierungsaudit" via an environment variable, which drives different logic in the audit planning stage (Chapter 4).
*   **Reporting:** The final output is a comprehensive JSON audit report, populated from stage results and the central findings file, ready for review in the `report_editor.html` tool.

**3. Gemini Model and API Interaction**
*   **Model Configuration (Imperative):**
    *   **Generative Model:** `gemini-2.5-pro`
    *   **Embedding Model:** `gemini-embedding-001` (for `3072` dimension vectors, as configured in Terraform).
*   **Robustness:** All API calls use an asynchronous, parallel-limited (`Semaphore`), and robust error-handling wrapper with an exponential backoff retry loop.

**4. AI Collaboration & Development Protocol**

**4.1. Communication Protocol**
*   **Commit Message Format:** Start every response with a summary formatted as follows:
    `Case: (A brief summary of my request)`
    `---`
    `Dixie: (A brief summary of your proposed solution and key details)`
*   **How to test this change:** CLI or similar to test.
*   **Explain Your Reasoning:** Briefly explain the "why" behind your code and architectural decisions.
*   **Track Changes:** For minor code changes (under 20 lines), present them in a `diff` format. For larger changes, provide the full file content.
*   **No Silent Changes:** Never alter code or logic without explicitly stating the change. **Only return files that have been changed.**

**4.2. Environment and Configuration**

| Variable | Required? | Source | Description |
| :--- | :---: | :--- | :--- |
| `GCP_PROJECT_ID` | Yes | Terraform | The Google Cloud Project ID. |
| `BUCKET_NAME` | Yes | Terraform | The GCS bucket for all I/O operations. |
| `AUDIT_TYPE` | Yes | User Input | Specifies the audit type (e.g., "Zertifizierungsaudit"). |
| `SOURCE_PREFIX` | Yes | Script | GCS prefix for source documents (e.g., `source_documents/`). |
| `OUTPUT_PREFIX` | Yes | Script | GCS prefix for generated files (e.g., `output/`). |
| `ETL_STATUS_PREFIX`| Yes | Script | GCS prefix for ETL status markers (e.g., `output/etl_status/`). |
| `INDEX_ENDPOINT_ID`| Yes | Terraform | The numeric ID of the deployed Vertex AI Index Endpoint. |
| `TEST` | No | User Input | Set to `"true"` to enable test mode. Defaults to `false`. |
