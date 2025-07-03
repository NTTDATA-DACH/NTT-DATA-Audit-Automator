### **`bsi-audit-automator/project_outline.md` (Full Recommended Update)**

### **Project Brief: BSI Grundschutz Audit Automation with Vertex AI**

This document outlines the requirements and development protocol for a Python-based application designed to automate BSI Grundschutz audits using the Vertex AI Gemini API.

**1. Project Overview & Objective**

The primary goal is to develop a cloud-native application that performs a security audit based on the German BSI Grundschutz framework. The application will run as a batch job on Google Cloud Platform (GCP), processing customer-provided documents against BSI standards and generating a structured audit report.

The core of the application is a **Retrieval-Augmented Generation (RAG)** pipeline. Source documents are first indexed into a Vertex AI Vector Search database. Then, for each section of the audit, the application retrieves the most relevant document excerpts to provide as context to the Gemini model, ensuring accurate, evidence-based findings.

The audit process and resulting report must be based on two key documents:
*   The relevant **BSI Grundschutz Standards** (as context for the AI).
*   The **`bsi-audit-automator/assets/json/master_report_template.json`** file, which serves as the structural and content template for the final audit report.

**2. Core Functional Requirements**

*   **Staged Audit Process:** The audit must be conducted in discrete stages that directly correspond to the chapters and subchapters of the master report template.
*   **State Management:** The application must save the results of each stage to Google Cloud Storage (GCS). For full audit runs (`--run-all-stages`), it must check for and skip previously completed stages, ensuring the process is resumable. For single-stage runs, it will overwrite existing results.
*   **Audit Type Configuration:** The application must be configurable to perform different types of audits, specifically distinguishing between an "Überwachungsaudit" and a "Zertifizierungsaudit". This is managed via an environment variable.
*   **Data Storage:** All intermediate and final data must be stored in JSON format in a designated GCS bucket.
*   **Reporting:** The final output will be a comprehensive audit report in JSON format, ready for review or further processing (e.g., via the `report_editor.html` tool).

**3. Gemini Model and API Interaction**
*   **Model Configuration (Imperative):**
    *   **Generative Model:** `gemini-2.5-pro`
    *   **Max Output Tokens:** `65536` (Reflects the current API default used in the code).
    *   **Embedding Model:** `gemini-embedding-001` (for `3072` dimension vectors, as configured in Terraform).
*   **Asynchronous Calls:** API calls are executed asynchronously for performance.
*   **Parallelism:** Concurrent calls to the model are managed using `asyncio` and a `Semaphore` to limit connections.
*   **Robust Error Handling:** A retry loop with exponential backoff is implemented for all model requests.

**4. AI Collaboration & Development Protocol**

**4.1. Communication Protocol**
*   **Commit Message Format:** Start every response with a summary formatted as follows:
    `Case: (A brief summary of my request)`
    `---`
    `Dixie: (A brief summary of your proposed solution and key details)`
*   **How to test this change:** CLI or similar to test.
*   **Explain Your Reasoning:** Briefly explain the "why" behind your code and architectural decisions.
*   **Track Changes:** For minor code changes (under 20 lines), please present them in a `diff` format. For larger changes, provide the full file content.
*   **No Silent Changes:** Never alter code or logic without explicitly stating the change. Focus only on implementing what the current prompt requests.
*   **Always all files:** With every change, check all files you know about if they need an adaption to the change as well.

**4.2. Environment and Configuration**
*   **Cloud-Native I/O:** All file operations must use the Google Cloud Storage (GCS) client library. The script is intended to run on GCP.
*   **Configuration via Environment Variables:** All configuration must be loaded from environment variables. The script must validate the presence of all required variables at startup.

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

**4.3. Architecture: RAG and Schema-Driven Generation**
*   **RAG Pipeline:** The core architecture follows a "retrieve-then-generate" pattern.
    1.  **ETL Phase:** An initial process chunks source documents, creates vector embeddings, and populates the Vertex AI Vector Search index.
    2.  **Retrieval:** For each audit task, a query is sent to the Vector Index, which returns the IDs of relevant document chunks.
    3.  **Context Building:** The application fetches the full text of these chunks from GCS.
    4.  **Generation:** This retrieved text is provided as grounded context in the prompt to the Gemini model.
*   **JSON-Based Communication:** All data sent to and received from the Gemini model must be in JSON format.
*   **Schema-Driven Prompts:** For each model interaction, a JSON schema is included in the prompt to define the exact expected output format. This schema is also used to validate the model's response.
*   **Deterministic Assembly:** The Python script is responsible for the final assembly of the complete report from the validated JSON stubs generated by the model for each stage.