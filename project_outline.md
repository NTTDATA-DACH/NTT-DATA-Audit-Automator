### **Project Brief: BSI Grundschutz Audit Automation with Vertex AI**

This document outlines the requirements and development protocol for a Python-based application designed to automate BSI Grundschutz audits using the Vertex AI Gemini API.

**1. Project Overview & Objective**

The primary goal is to develop a cloud-native application that performs a security audit based on the German BSI Grundschutz framework. The application will run as a batch job on Google Cloud Platform (GCP), processing customer-provided documents against BSI standards and generating a structured audit report.

The audit process and resulting report must be based on two key documents, which will be provided as source files:
*   The relevant **BSI Grundschutz Standards**.
*   The **"Muster_Auditbericht_Kompendium_V3.0"**, which serves as the structural and content template for the final audit report.

**2. Core Functional Requirements**

*   **Staged Audit Process:** The audit must be conducted in discrete stages that directly correspond to the chapters and subchapters of the `Muster_Auditbericht_Kompendium_V3.0`.
*   **State Management:** The application must save the results of each stage to Google Cloud Storage (GCS). Before starting a new stage, it must check for and load any previously saved state for that stage, ensuring the process is resumable.
*   **Audit Type Configuration:** The application must be configurable to perform different types of audits, specifically distinguishing between an "Überwachungsaudit" (surveillance audit) and a "Zertifizierungsaudit" (certification audit). This will be managed via an environment variable.
*   **Data Storage:**
    *   All intermediate and final data must be stored in JSON format.
    *   Customer data must be stored in a designated GCS bucket located in a German region to comply with data residency requirements.
    *   A clean, hierarchical file structure must be used, with a dedicated subdirectory for each customer within the main bucket.
*   **Reporting:** The final output will be a comprehensive audit report. While the structure and content must mirror the `Muster_Auditbericht_Kompendium_V3.0`, the final file format (e.g., PDF, Markdown, JSON) is flexible.

**3. Initial Tasks**

To begin this project, I need you to propose the following:

1.  **Code Architecture:** Outline a high-level structure for the Python application, including key modules, classes, and their interactions.
2.  **AI Data Processing Strategy:** Describe an approach for using the Gemini model to intelligently read, parse, and analyze the customer's source documents (e.g., PDFs) in preparation for the audit.
3.  **Report Generation Plan:** Detail the process for creating the final report, ensuring it programmatically adopts the headings, structure, and content style of the `Muster_Auditbericht_Kompendium_V3.0` template.

**4. AI Collaboration & Development Protocol**

To ensure a smooth and efficient development process, please adhere to the following guidelines in all your responses.

**4.1. Communication Protocol**
*   **Commit Message Format:** Start every response with a summary formatted as follows:
    `Case: (A brief summary of my request)`
    `---`
    `Dixie: (A brief summary of your proposed solution and key details)`
*   **Explain Your Reasoning:** Briefly explain the "why" behind your code and architectural decisions.
*   **Track Changes:** For minor code changes (under 20 lines), please present them in a `diff` format. For larger changes, provide a clear explanation of what was modified.
*   **No Silent Changes:** Never alter code or logic without explicitly stating the change. Focus only on implementing what the current prompt requests.

**4.2. Environment and Configuration**
*   **Cloud-Native I/O:** All file operations must use the Google Cloud Storage (GCS) client library. The script is intended to run on GCP.
*   **Configuration via Environment Variables:** All configuration must be loaded from environment variables. The script must validate the presence of all required variables at startup.

| Variable | Required? | Description |
| :--- | :---: | :--- |
| `GCP_PROJECT_ID` | Yes | The Google Cloud Project ID. |
| `BUCKET_NAME` | Yes | The GCS bucket for all I/O operations. |
| `CUSTOMER_ID` | Yes | A unique identifier for the customer, used as the subdirectory name. |
| `SOURCE_PREFIX` | Yes | GCS prefix within the customer's directory for source files. |
| `OUTPUT_PREFIX` | Yes | GCS prefix within the customer's directory for generated files. |
| `AUDIT_TYPE` | Yes | Specifies the audit type (e.g., "Zertifizierungsaudit"). |
| `STATE_GCS_PATH` | No | Full GCS path to an existing state file to resume from. |
| `TEST` | No | Set to `"true"` to enable test mode. Defaults to `false`. |

**4.3. Architecture: "Schema-Stub" Generation**
This is a critical architectural pattern for ensuring reliability.
*   **JSON-Based Communication:** All data sent to and received from the Gemini model must be in JSON format. This must be configured in the `generation_config`.
*   **Schema-Driven Prompts:** For each model interaction, generate a JSON schema "stub" that defines the expected output format. This schema should be included in the prompt.
*   **Minimal Data Subsets:** Pre-filter and structure data sent to the model into a minimal, easy-to-understand JSON object.
*   **Schema as a Quality Gate:** Use the generated schemas to validate the model's JSON output. All validation errors must be caught and logged.
*   **Deterministic Assembly:** The Python script is responsible for the final, deterministic assembly of the complete report from the validated JSON stubs generated by the model.

**4.4. Gemini Model and API Interaction**
*   **Model Configuration (Non-Negotiable):**
    *   **Model:** `gemini-1.5-pro`
    *   **Max Output Tokens:** `8192`
    *   ***Note:*** *The original prompt mentioned `gemini-2.5-pro` and `65536` tokens, which have been corrected to reflect available models and their limits.*
*   **Python SDK:** Use the official Google Cloud AI Platform Python library (`google-cloud-aiplatform`).
    *   ***Note:*** *The original prompt mentioned the "genau library 1.40," which appears to be a typo. We will proceed with the official Vertex AI SDK.*
*   **Parallelism:** Where possible, execute calls to the model concurrently using a semaphore to limit connections (e.g., max 10).
*   **Robust Error Handling:** Implement a retry loop (e.g., 5 attempts with exponential backoff) for model requests. Explicitly check the model's `finish_reason` and log any non-`OK` statuses with verbose error details.
*   **Grounding:** Grounding with Google Search is optional and should only be used for creative text generation tasks where factual enhancement is needed.

**4.5. Code and Asset Management**
*   **Externalized Assets:** All prompts must be stored in external `.txt` files and all JSON schemas in external `.json` files. This separation of logic and assets is mandatory.

**4.6. Testing and Logging**
*   **Test Mode (`TEST="true"`):**
    *   Limit processing to a small number of source files (e.g., the first 3).
    *   Within each file, limit the data sent for generation (e.g., 10% of discovered items).
*   **Conditional Logging:**
    *   The root logger level should be `INFO`.
    *   **In Test Mode:** Log detailed, step-by-step messages at the `INFO` level.
    *   **In Production Mode (`TEST="false"`):** Log verbose messages at the `DEBUG` level. Log only high-level status updates at the `INFO` level.
    *   **Suppress Library Noise:** In production, set the logging level for third-party libraries like `google.auth` and `urllib3` to `WARNING` to maintain clean logs.

**4.7. Code Style**
*   **Readability:** Code must be clean, well-formatted, and conform to PEP 8.
*   **Documentation:** All functions must have clear docstrings (e.g., Google-style) explaining their purpose, arguments, and return values. Use inline comments to clarify the *intent* behind complex logic.