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
│   │       ├── control_catalog.py # Query layer over the official BSI Kompendium catalog.
│   │       ├── stage_gs_check_extraction.py # Dedicated stage for Grundschutz-Check processing.
│   │       ├── gs_extraction/  # Sub-pipeline of that stage (ground truth, Document AI, grouping, refiner).
│   │       ├── stage_previous_report_scan.py
│   │       ├── stage_1_general.py
│   │       ├── stage_3_dokumentenpruefung.py
│   │       ├── stage_4_pruefplan.py
│   │       ├── stage_5_vor_ort_audit.py
│   │       └── stage_7_anhang.py
│   │   └── async_utils.py      # gather_resilient: concurrency that drops failures instead of the run.
│   │
│   ├── tools/                  # Build-time only, never imported at runtime.
│   │   ├── ed23_xml.py         # Pinned download + parser for the official BSI XML-Kompendium.
│   │   ├── sentence_split.py   # German sentence splitter used by that parser.
│   │   └── build_bsi_catalog.py # Converts the XML into the committed catalog JSON.
│   │
│   ├── config.py               # Loads, validates, and provides application configuration from env variables.
│   ├── constants.py            # Model IDs, AI switches and every GCS/asset path.
│   ├── logging_setup.py        # Configures application-wide logging.
│   └── main.py                 # Main entry point with CLI argument parsing.
│
├── assets/                     # External, non-code assets for the AI.
│   ├── json/                   # JSON Schemas, the BSI Kompendium catalog, and the master report template.
│
└── ... (Other project files: Dockerfile, requirements.txt, scripts/, terraform/, etc.)
```

**Module Descriptions:**

*   **`src/main.py`**: The application's entry point. It parses command-line arguments (`--run-gs-check-extraction`, `--run-all-stages`, etc.) to determine which part of the pipeline to execute.
*   **`src/clients/`**: This directory contains thin clients responsible for communicating with external GCP services, encapsulating all API-specific logic.
    *   **`gcs_client.py`**: Handles all Google Cloud Storage I/O.
    *   **`ai_client.py`**: A robust wrapper for the Gemini API. It handles model configuration (model ID, temperature, thinking level), asynchronous parallel requests, retries, and schema-enforced JSON generation. `generate_checked_json_response` adds the **maker/checker** pass: a second, independent call re-judges the answer against the same source documents and may replace it with a corrected one; every verdict is recorded for the controller to persist.
    *   **`rag_client.py`**: The "Document Finder". It manages a map of document filenames to BSI categories, creating this map on-demand if it doesn't exist, and providing GCS URIs of relevant documents for analysis tasks.
*   **`src/audit/controller.py`**: The main orchestrator of the audit. It defines the sequence of stages to run (starting with the extraction stage), manages resumability, and acts as the **central collector for all audit findings**.
*   **`src/audit/stages/`**: Each module in this directory contains the business logic for a specific part of the audit.
    *   `stage_gs_check_extraction.py`: A dedicated pre-processing stage. It implements the "Ground-Truth-Driven Semantic Chunking" strategy to create authoritative intermediate files (`system_structure_map.json` and `extracted_grundschutz_check_merged.json`) that are consumed by other stages.
    *   `control_catalog.py`: The query layer over `assets/json/bsi_kompendium_ed2023.json` — the official BSI IT-Grundschutz-Kompendium, Edition 2023, generated from the sha256-pinned BSI XML by `src/tools/build_bsi_catalog.py`. It answers "which requirements does this Baustein have", "what level (B/S/H) is this requirement" and "which are the Basis-Anforderungen (MUSS)". Requirements marked ENTFALLEN are never handed out, and Bausteine the institution defined itself are not in the catalog.
    *   The other `stage_X_...py` modules define the AI prompts, select appropriate schemas, call the `ai_client`, and return structured results. They rely on the `rag_client` for document context and, where applicable, the outputs from the extraction stage.
*   **`src/audit/report_generator.py`**: This module is responsible for the final, deterministic assembly of the report. It populates the `master_report_template.json` by merging in the JSON results from each stage file and, critically, by reading the `all_findings.json` file to populate the categorized tables in Chapter 7.2.