### **Project Brief: BSI Grundschutz Audit Automation with Vertex AI (Revised)**

This document outlines the requirements and development protocol for a Python-based application designed to automate BSI Grundschutz audits using the Vertex AI Gemini API.

**1. Project Overview & Objective**

The primary goal is to develop a cloud-native application that performs a security audit based on the German BSI Grundschutz framework. The application will run as a batch job on Google Cloud Platform (GCP), processing customer-provided documents against BSI standards and generating a structured audit report.

The core of the application is a **Retrieval-Augmented Generation (RAG)** pipeline. Source documents are first indexed into a Vertex AI Vector Search database. Then, for each section of the audit, the application retrieves the most relevant document excerpts to provide as context to the Gemini model, ensuring accurate, evidence-based findings.

The audit process and resulting report must be based on two key documents:
*   The relevant **BSI Grundschutz Standards** (BSI 200-1, BSI 200-2 and BSI 200-3 and the BSI Grundschutz Kompendium 2023 with its Auditierungsschema and Zertifizierungsschem).
*   The **`bsi-audit-automator/assets/json/master_report_template.json`** file, which serves as the structural and content template for the final audit report.

**2. Core Functional Requirements**

*   **Idempotent ETL Process:** A resilient ETL job processes source documents, creates vector embeddings, and uploads them for indexing. It uses status markers (`.success`, `.failed`) in GCS to prevent reprocessing and ensure robustness.
*   **Staged Audit Process:** The audit is conducted in discrete stages corresponding to the report chapters. The application supports running all stages or single stages. Some sections (e.g., 1.4, 5.1) are intentionally placeholders for manual auditor input.
*   **State Management & Resumability:** The application saves the results of each stage to Google Cloud Storage (GCS). For full audit runs (`--run-all-stages`), it checks for and skips previously completed stages. For single-stage runs, it overwrites existing results by default.
*   **Centralized Finding Collection:** The application systematically collects all structured findings (deviations and recommendations with categories 'AG', 'AS', 'E') from all stages into a central `all_findings.json` file.
*   **Audit Type Configuration:** The application is configurable for "Überwachungsaudit" or "Zertifizierungsaudit" via an environment variable, which drives different logic in the audit planning stage (Chapter 4).
*   **Deterministic and AI-Driven Logic:** The pipeline intelligently combines AI-driven analysis (e.g., Chapter 3 document review) with deterministic, rule-based logic (e.g., Chapter 5 control checklist generation from the BSI catalog).
*   **Comprehensive Reporting:** The final output is a comprehensive JSON audit report, populated from individual stage results and the central findings file, ready for review and finalization in the `report_editor.html` tool.

**3. Gemini Model and API Interaction**
*   **Model Configuration (Imperative):**
    *   **Generative Model:** `gemini-2.5-pro`
    *   **Embedding Model:** `gemini-embedding-001` (for `3072` dimension vectors, as configured in Terraform).
*   **Robustness:** All API calls use an asynchronous, parallel-limited (`Semaphore`), and robust error-handling wrapper with an exponential backoff retry loop.

**4. AI Collaboration & Development Protocol**

**4.1. Communication Protocol**
*   **Commit Message Format:** Start every response with a summary formatted as follows:
    `Case: (A brief summary of my request, word as if cases was speaking)`
    `---`
    `Dixie: (A brief summary of your solution and key details)`
*   **How to test this change:** CLI or similar to test.
*   **Explain Your Reasoning:** Briefly explain the "why" behind your code and architectural decisions.
*   **Track Changes:** For minor code changes (under 20 lines), present them in a `diff` format. For larger changes, provide the full file content.
*   **No Silent Changes:** Never alter code or logic without explicitly stating the change. **Only return files that have been changed.**
*   **Statefulness and Conciseness:** You are expected to be stateful. Review the existing project context, especially `tasks_for_improvement_after_MVP-1.md`, before formulating a response. If your analysis reveals no new bugs, required refactorings, or actionable improvements beyond what is already listed, you MUST respond concisely with the message: `Dixie: I have analyzed the current project state and have no new code changes or tasks to recommend at this time.` Do not re-list or re-phrase existing TODOs.

**4.2. Code Quality and Development Standards**
*   **Modularity and Single Responsibility:** The existing architecture (clients, processors, controllers, stages) must be maintained. Each module and class should have a single, well-defined purpose. For example, `GcsClient` should only ever contain GCS-related logic.
*   **Clarity and Naming:** Code should be as self-documenting as possible. Use descriptive, unambiguous names for variables, functions, and classes (e.g., `_process_single_document` is clearer than `_process`).
*   **Docstrings and Comments:**
    *   All public modules, classes, and functions MUST have Python docstrings explaining their purpose, arguments (`Args:`), and return values (`Returns:`).
    *   Use inline comments not to explain *what* the code is doing (which should be obvious from the code itself), but to explain *why* a particular implementation choice was made, especially for complex logic, business rules, or workarounds.
*   **Strict Type Hinting:** All new functions and methods MUST include full type hints for arguments and return values. This is essential for static analysis, readability, and preventing runtime errors.
*   **Consistent Formatting:** Code MUST adhere to the PEP 8 style guide. It is recommended to use an automated formatter like `black` to ensure consistency across the project.
*   **Robust Error Handling:** Avoid broad `except Exception:` clauses. Catch specific, anticipated exceptions and log them with informative messages. The robust retry logic in `AiClient` serves as a model for handling external API calls.

**4.3. Architecture: "Schema-Stub" Generation**
This is a critical architectural pattern for ensuring reliability.
*   **JSON-Based Communication:** All data sent to and received from the Gemini model must be in JSON format. This must be configured in the `generation_config`.
*   **Schema-Driven Prompts:** For each model interaction, generate a JSON schema "stub" that defines the expected output format. This schema should be included in the prompt.
*   **Minimal Data Subsets:** Pre-filter and structure data sent to the model into a minimal, easy-to-understand JSON object.
*   **Schema as a Quality Gate:** Use the generated schemas to validate the model's JSON output. All validation errors must be caught and logged.
*   **Deterministic Assembly:** The Python script is responsible for the final, deterministic assembly of the complete report from the validated JSON stubs generated by the model.


**4.5. Code and Asset Management**
*   **Externalized Assets:** All prompts must be stored in external `.txt` files and all JSON schemas in external `.json` files. This separation of logic and assets is mandatory.
*   **Customer Data:** The customer documents are located in one directory the following GCS URI: [GCS_DATA_URI]

**4.6. Testing and Logging**
*   **Test Mode (`TEST="true"`):**
    *   Limit processing to a small number of source files (e.g., the first 3).
    *   Within each file, limit the data sent for generation (e.g., 10% of discovered items).
*   **Conditional Logging:**
    *   The root logger level should be `INFO`.
    *   **In Test Mode:** Log detailed, step-by-step messages at the `INFO` level.
    *   **In Production Mode (`TEST="false"`):** Log verbose messages at the `DEBUG` level. Log only high-level status updates at the `INFO` level.
    *   **Suppress Library Noise:** In production, set the logging level for third-party libraries like `google.auth` and `urllib3` to `WARNING` to maintain clean logs.


**4.7. Environment and Configuration**

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