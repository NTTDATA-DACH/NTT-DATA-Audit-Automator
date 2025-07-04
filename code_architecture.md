### **Code Architecture**

The application is designed to be **modular, scalable, and auditable**, separating concerns into distinct clients, processors, and controllers.

```
bsi-audit-automator/
│
├── src/
│   ├── clients/
│   │   ├── gcs_client.py       # Handles all GCS interactions.
│   │   ├── rag_client.py       # Handles Vector Search (RAG) queries.
│   │   └── ai_client.py        # Handles all Vertex AI Gemini interactions.
│   │
│   ├── etl/
│   │   └── processor.py        # The ETL processor for the RAG pipeline.
│   │
│   ├── audit/
│   │   ├── controller.py       # Orchestrates all audit stages and collects findings.
│   │   ├── report_generator.py # Assembles the final report from stage outputs.
│   │   └── stages/             # Logic for each specific audit stage.
│   │       ├── control_catalog.py # Helper to parse BSI controls from OSCAL.
│   │       ├── stage_1_general.py
│   │       └── ... (and so on for each automated chapter)
│   │
│   ├── config.py               # Loads/validates configuration from env variables.
│   └── logging_setup.py        # Configures application logging.
│
├── assets/                     # External, non-code assets for the AI.
│   ├── json/                   # Schemas, BSI catalog data.
│   └── prompts/                # .txt prompt templates.
│
└── ... (Other project files: Dockerfile, requirements.txt, etc.)
```

**Module Descriptions:**

*   **`src/etl/processor.py`**: Contains the logic for the **Extract, Transform, Load (ETL)** process. It finds source documents, chunks them, generates vector embeddings, and uploads them in the specific format required by Vertex AI Vector Search. It is idempotent, tracking processed files.
*   **`src/clients/`**: This directory contains thin clients responsible for communicating with external services, encapsulating all API-specific logic.
    *   **`gcs_client.py`**: Handles all Google Cloud Storage I/O.
    *   **`ai_client.py`**: A robust wrapper for the Gemini API, handling model configuration, retries, and schema-enforced JSON generation.
    *   **`rag_client.py`**: Connects to the Vector Search endpoint, providing a simple method to retrieve document context for a given query.
*   **`src/audit/controller.py`**: The main orchestrator of the audit. It defines the sequence of stages to run, manages resumability by checking for existing results, and acts as the **central collector for all audit findings**, inspecting the results of each stage.
*   **`src/audit/stages/`**: Each module in this directory contains the business logic for a specific chapter of the audit. It defines the RAG queries, selects the appropriate prompts and schemas, and prepares data for the `ai_client`. Some stages are deterministic (like Chapter 5's checklist generation).
*   **`src/audit/report_generator.py`**: This module is responsible for the final, deterministic assembly of the report. It populates the `master_report_template.json` by merging in the JSON results from each stage file and, critically, by reading the `all_findings.json` file to populate the categorized tables in Chapter 7.2.
