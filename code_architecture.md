### Why This Approach?

The proposed architecture is designed to be **modular, scalable, and testable**. It directly implements the key requirements from the brief:
*   **Separation of Concerns:** Each module has a single responsibility (e.g., talking to GCS, talking to the AI, running the audit). This makes the code easier to understand, maintain, and test.
*   **Reliability:** The "Schema-Stub" pattern is at the core of the `ai_client`, ensuring that we get predictable, validated data from the model at every step.
*   **Resumability:** The `AuditController`'s state management logic makes the batch job resilient to interruptions, saving time and cost.
*   **Flexibility:** Externalizing prompts and schemas in the `assets/` directory allows for easy updates and tuning of the AI's behavior without changing the core Python code.

---

### 1. Code Architecture Proposal

I propose the following high-level directory and module structure. This structure organizes the code logically and prepares it for future testing and expansion.

```
bsi-audit-automator/
│
├── main.py                     # Entry point of the application
│
├── src/                        # Main source code directory
│   ├── __init__.py
│   ├── config.py                 # Load and validate environment variables
│   ├── logging_setup.py          # Configure logging based on TEST mode
│   │
│   ├── clients/                  # Clients for external services
│   │   ├── __init__.py
│   │   ├── gcs_client.py         # Handles all GCS read/write operations
│   │   └── ai_client.py          # Handles all Gemini API interactions
│   │
│   └── audit/                    # Core business logic for the audit
│       ├── __init__.py
│       ├── controller.py         # Orchestrates the audit stages and state
│       ├── report_generator.py   # Assembles the final report from stage outputs
│       │
│       └── stages/                 # Directory for individual stage logic
│           ├── __init__.py
│           ├── stage_1_general.py
│           ├── stage_3_document_review.py
│           └── ... (one module per major chapter) ...
│
├── assets/                     # External, non-code assets
│   ├── prompts/                  # Prompt templates (.txt files)
│   │   ├── initial_extraction.txt
│   │   └── stage_3_1_actuality.txt
│   │   └── ...
│   └── schemas/                  # JSON schemas for model output validation
│       ├── initial_extraction_schema.json
│       └── stage_3_1_actuality_schema.json
│       └── ...
│
├── tests/                      # Directory for unit and integration tests
│
└── requirements.txt            # Python package dependencies
```

**Module Descriptions:**

*   **`main.py`**: The script's entry point. It initializes logging and configuration, then instantiates and runs the `AuditController`.
*   **`src/config.py`**: Defines a class or dataclass to load all environment variables listed in the brief. It will perform validation at startup to ensure all required variables are present.
*   **`src/logging_setup.py`**: Contains a function to set up the root logger based on the `TEST` environment variable, including suppressing noisy third-party library logs in production.
*   **`src/clients/gcs_client.py`**: Encapsulates all interactions with Google Cloud Storage. It will have functions like `list_source_files()`, `read_json()`, `write_json()`, and `read_file_bytes()` for PDFs.
*   **`src/clients/ai_client.py`**: A crucial module responsible for all communication with the Gemini API.
    *   It will initialize the Vertex AI client with the non-negotiable model settings.
    *   It will contain the semaphore to limit concurrent requests.
    *   It will implement the retry loop with exponential backoff and `finish_reason` checking.
    *   Its primary function will take a prompt file, a schema file, and context data (e.g., customer info) as input. It will read the files, construct the final prompt with the schema stub, call the API, and validate the JSON output before returning it.
*   **`src/audit/controller.py`**: The main orchestrator.
    *   It will define the list of audit stages, mirroring the `Muster_Auditbericht` chapters (e.g., `['1.1', '1.2', ..., '5.6.3']`).
    *   It will loop through these stages. For each stage, it will first check GCS for a pre-existing result file. If found, it loads it and skips to the next stage. If not, it calls the corresponding module in the `src/audit/stages/` directory to execute the logic and then saves the result.
*   **`src/audit/stages/*.py`**: Each module corresponds to a part of the audit. It contains the specific logic for that stage, including which prompts/schemas to use and how to prepare data for the `ai_client`.
*   **`src/audit/report_generator.py`**: This module is called after all stages are successfully completed. It loads all the intermediate JSON results from GCS and assembles them into a single, comprehensive report file (e.g., Markdown).

### 2. AI Data Processing Strategy (Initial Analysis)

To effectively audit the customer's environment, we first need to understand it. The initial phase will focus on intelligently parsing the customer's provided documents (PDFs, etc.) into a structured format that subsequent AI prompts can use.

1.  **Multi-modal Ingestion**: We will leverage Gemini 1.5 Pro's large context window and multi-modal capabilities. The `gcs_client` will provide the GCS URIs of the customer's source documents. These URIs will be passed directly to the model in the API call.
2.  **Initial Extraction Prompt**: A dedicated "initial extraction" stage will be the first step. The `ai_client` will be called with a specialized prompt (e.g., `assets/prompts/initial_extraction.txt`) that instructs the model to act as a BSI security expert. The prompt will ask it to read all provided documents and extract key entities relevant to the audit, such as:
    *   Security policies and guidelines mentioned.
    *   Defined business processes.
    *   Lists of IT systems, applications, and network components.
    *   Physical locations and data centers.
    *   Information about external service providers.
3.  **Schema-Driven Structuring**: This extraction prompt will be paired with a comprehensive JSON schema (`assets/schemas/initial_extraction_schema.json`). This schema will force the model to structure its findings into a clean, predictable JSON object. This object becomes our "Customer Knowledge Base".
4.  **Knowledge Base as Context**: The generated and validated "Customer Knowledge Base" JSON will be saved to GCS. For all subsequent audit stages, this JSON object will be passed to the model as the primary context, ensuring every audit step works from the same foundational understanding of the customer's environment.

### 3. Report Generation Plan

The report generation will be a deterministic assembly process, ensuring the output always matches the structure of the `Muster_Auditbericht_Kompendium_V3.0`.

1.  **Template Creation (One-time Task)**: The `Muster_Auditbericht_Kompendium_V3.0` will be analyzed to create a master JSON object structure that represents the complete, empty report. This includes all chapters, sub-chapters, tables, and specific questions as keys. This becomes our internal report template.
2.  **Staged Content Generation**: As the `AuditController` executes each stage, it will invoke the `ai_client` with prompts specifically designed to generate the content for that section of the report. For example, for section "3.2 Sicherheitsleitlinie und -richtlinien", the prompt will be:
    > "Based on the provided BSI standards and the extracted Customer Knowledge Base [JSON data], analyze the customer's security policies. Generate the findings for section 3.2 of the audit report, answering all required questions. Your output must conform to the following JSON schema: [schema for section 3.2]"
3.  **Validated JSON Stubs**: The AI's JSON output for each stage is validated against its corresponding schema and saved as a separate file in GCS (e.g., `output/stage_3_2_result.json`). This ensures that every piece of the final report is pre-validated and correctly structured.
4.  **Deterministic Assembly**: The `report_generator.py` script executes after all stages are complete. It will:
    a. Load the master report template (from step 1).
    b. Load all the individual stage result JSON files from GCS.
    c. Systematically traverse the master template and populate it with the content from the corresponding JSON stubs.
5.  **Final Rendering**: Once the master template is fully populated with data, the `report_generator` will render it into a human-readable format. I propose starting with **Markdown** as it is programmatically simple to create and can be easily converted to PDF or other formats as a final step.

This approach cleanly separates the AI's creative/analytical task (generating content for a small section) from the application's deterministic logic (assembling the full report), adhering perfectly to the "Schema-Stub" architectural pattern.