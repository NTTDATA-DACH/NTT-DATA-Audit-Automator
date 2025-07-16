### **Code Architecture (Revised)**

The application is designed to be **modular, scalable, and auditable**, separating concerns into distinct clients, processors, and controllers. This architecture promotes clarity and maintainability.

```
bsi-audit-automator/
│
├── src/
│   ├── clients/
│   │   ├── gcs_client.py       # Handles all Google Cloud Storage interactions.
│   │   ├── rag_client.py       # Manages the document category map (Document Finder).
│   │   └── ai_client.py        # Handles all Vertex AI Gemini API interactions.
│   │
│   ├── audit/
│   │   ├── controller.py       # Orchestrates all audit stages, manages state, and collects findings.
│   │   ├── report_generator.py # Assembles the final report from stage outputs and collected findings.
│   │   └── stages/             # Contains the specific business logic for each audit stage/chapter.
│   │       ├── control_catalog.py # Helper class to load and parse BSI controls from the OSCAL JSON file.
│   │       ├── stage_gs_check_extraction.py # New: Dedicated stage for Grundschutz-Check processing.
│   │       ├── stage_previous_report_scan.py
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
│
└── ... (Other project files: Dockerfile, requirements.txt, scripts/, terraform/, etc.)
```

**Module Descriptions:**

*   **`src/main.py`**: The application's entry point. It parses command-line arguments (`--run-gs-check-extraction`, `--run-all-stages`, etc.) to determine which part of the pipeline to execute.
*   **`src/clients/`**: This directory contains thin clients responsible for communicating with external GCP services, encapsulating all API-specific logic.
    *   **`gcs_client.py`**: Handles all Google Cloud Storage I/O.
    *   **`ai_client.py`**: A robust wrapper for the Gemini API. It handles model configuration, asynchronous parallel requests, retries, and schema-enforced JSON generation.
    *   **`rag_client.py`**: The "Document Finder". It manages a map of document filenames to BSI categories, creating this map on-demand if it doesn't exist, and providing GCS URIs of relevant documents for analysis tasks.
*   **`src/audit/controller.py`**: The main orchestrator of the audit. It defines the sequence of stages to run (starting with the extraction stage), manages resumability, and acts as the **central collector for all audit findings**.
*   **`src/audit/stages/`**: Each module in this directory contains the business logic for a specific part of the audit.
    *   `stage_gs_check_extraction.py`: A dedicated pre-processing stage. It implements the "Ground-Truth-Driven Semantic Chunking" strategy to create authoritative intermediate files (`system_structure_map.json` and `extracted_grundschutz_check_merged.json`) that are consumed by other stages.
    *   `control_catalog.py`: A helper utility that loads the `BSI_GS_OSCAL...json` file and provides methods to query control details.
    *   The other `stage_X_...py` modules define the AI prompts, select appropriate schemas, call the `ai_client`, and return structured results. They rely on the `rag_client` for document context and, where applicable, the outputs from the extraction stage.
*   **`src/audit/report_generator.py`**: This module is responsible for the final, deterministic assembly of the report. It populates the `master_report_template.json` by merging in the JSON results from each stage file and, critically, by reading the `all_findings.json` file to populate the categorized tables in Chapter 7.2.