### **Code Architecture (Revised)**

The application is designed to be **modular, scalable, and auditable**, separating concerns into distinct clients, processors, and controllers. This architecture promotes clarity and maintainability.

```
bsi-audit-automator/
│
├── src/
│   ├── clients/
│   │   ├── gcs_client.py       # Handles all Google Cloud Storage interactions.
│   │   ├── rag_client.py       # Handles Vector Search (RAG) queries and context retrieval.
│   │   └── ai_client.py        # Handles all Vertex AI Gemini API interactions (generation & embedding).
│   │
│   ├── etl/
│   │   └── processor.py        # The idempotent ETL processor for the RAG pipeline.
│   │
│   ├── audit/
│   │   ├── controller.py       # Orchestrates all audit stages, manages state, and collects findings.
│   │   ├── report_generator.py # Assembles the final report from stage outputs and collected findings.
│   │   └── stages/             # Contains the specific business logic for each audit stage/chapter.
│   │       ├── control_catalog.py # Helper class to load and parse BSI controls from the OSCAL JSON file.
│   │       ├── stage_1_general.py
│   │       ├── stage_3_dokumentenpruefung.py
│   │       ├── stage_4_pruefplan.py
│   │       ├── stage_5_vor_ort_audit.py
│   │       └── stage_7_anhang.py
│   │
│   ├── config.py               # Loads, validates, and provides application configuration from env variables.
│   ├── logging_setup.py        # Configures application-wide logging.
│   └── main.py                 # Main entry point with CLI argument parsing.
│
├── assets/                     # External, non-code assets for the AI.
│   ├── json/                   # JSON Schemas, BSI OSCAL catalog, and the master report template.
│   └── prompts/                # .txt prompt templates with placeholders for context.
│
└── ... (Other project files: Dockerfile, requirements.txt, scripts/, terraform/, etc.)
```

**Module Descriptions:**

*   **`src/main.py`**: The application's entry point. It parses command-line arguments (`--run-etl`, `--run-all-stages`, etc.) to determine which part of the pipeline to execute. It also performs a critical pre-flight check to ensure ETL has been run before any RAG-dependent audit stage.
*   **`src/etl/processor.py`**: Contains the logic for the **Extract, Transform, Load (ETL)** process. It finds source documents in GCS, extracts text, chunks it, generates vector embeddings via `ai_client`, and uploads the data as per-document JSONL files to GCS. It is idempotent, using `.success`/`.failed` markers to track processed files and ensure resilience.
*   **`src/clients/`**: This directory contains thin clients responsible for communicating with external GCP services, encapsulating all API-specific logic.
    *   **`gcs_client.py`**: Handles all Google Cloud Storage I/O, including listing files, reading/writing text and JSON, and checking for blob existence.
    *   **`ai_client.py`**: A robust wrapper for the Gemini API. It handles model configuration (`gemini-2.5-pro`, `gemini-embedding-001`), asynchronous parallel requests limited by a semaphore, automatic retries with exponential backoff, and schema-enforced JSON generation.
    *   **`rag_client.py`**: Connects to the Vertex AI Vector Search endpoint. Its primary method, `get_context_for_query`, encapsulates the RAG pattern: it takes a text query, uses the `ai_client` to embed it, queries the vector index for similar document chunks, and retrieves the full text for those chunks to be used as prompt context.
*   **`src/audit/controller.py`**: The main orchestrator of the audit. It defines the sequence of stages to run, manages resumability by checking for existing results in GCS, and acts as the **central collector for all audit findings**. It inspects the results of each stage for structured `finding` objects and appends them to a master list, which is saved at the end of the run.
*   **`src/audit/stages/`**: Each module in this directory contains the business logic for a specific chapter of the audit.
    *   `control_catalog.py`: A helper utility that loads the `BSI_GS_OSCAL...json` file and provides a simple method to retrieve all controls for a given Baustein ID. It is used for deterministic checklist generation.
    *   The `stage_X_...py` modules define the RAG queries, select the appropriate prompts and schemas from `/assets`, call the `ai_client` for generation, and return the structured result. Some stages are deterministic (like Chapter 5's checklist generation) or conditional (like Chapter 4's plan generation based on `AUDIT_TYPE`).
*   **`src/audit/report_generator.py`**: This module is responsible for the final, deterministic assembly of the report. It populates the `master_report_template.json` by merging in the JSON results from each stage file and, critically, by reading the `all_findings.json` file to populate the categorized tables in Chapter 7.2.
```
