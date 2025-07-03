# BSI Grundschutz Audit Automator

This project automates BSI Grundschutz security audits by transforming customer documentation into a structured report using a cloud-native, multi-stage pipeline on Google Cloud. It leverages a Retrieval-Augmented Generation (RAG) pattern with the Vertex AI Gemini API to ensure audit findings are contextually relevant and accurate.

## End-to-End Workflow

Follow these steps in order to get from a new customer to a generated audit report.

### Step 1: Infrastructure Deployment (One-Time per Customer)

The Terraform configuration in the `terraform/` directory creates all necessary cloud resources for a specific customer audit.

1.  Navigate to the `terraform/` directory.
2.  Edit the `terraform.tfvars` file to set your `project_id`, and `project_number`.
3.  Deploy the infrastructure:
    ```bash
    terraform init
    terraform apply
    ```
    This creates a unique GCS Bucket, a VPC Network, and the Vertex AI Vector Search Index and Endpoint required for the RAG pipeline.

### Step 2: Deploy the Cloud Run Job (One-Time per Project)

The application code is deployed as a generic, reusable Cloud Run Job. This job contains all the logic for every stage of the audit.

```bash
# From the project src/ directory:
bash ../scripts/deploy-audit-job.sh
```
This script deploys the application container as a job named `bsi-audit-automator-job`, which can then be executed on demand to perform specific tasks.

### Step 3: Upload Customer Source Documents

Upload the customer's documentation (PDFs, etc.) to the GCS bucket created by Terraform. The application's ETL process will read from this location.

1.  Get the bucket name from Terraform: `terraform -chdir=./terraform output -raw bsi_audit_bucket_name`
2.  Upload the files to the correct path:
    ```bash
    # Example using gsutil
    BUCKET_NAME="<name-from-terraform-output>"
    LOCAL_DOCS_FOLDER="./customer_docs/"

    gsutil rsync -r "${LOCAL_DOCS_FOLDER}" "gs://${BUCKET_NAME}/source_documents/"
    ```

### Step 4: Execute an Audit Task

All pipeline tasks are run using the interactive `execute-audit-job.sh` script. This is the primary script you will use for day-to-day operations.

```bash
# From the projects src/ directory:
bash ../scripts/execute-audit-job.sh
```

This script will prompt you to select the task to execute. The correct workflow is:
1.  First, run the **"Run ETL (Embedding)"** task. This processes the source documents and populates the Vector Search Index. You must wait for this to complete before proceeding (monitor in the GCP Console).
2.  Once the ETL is done and the index is updated, you can run any audit stage (e.g., **"Run Single Audit Stage"** -> **"Chapter-1"**), run all stages, or generate the final report.

The script dynamically passes the correct arguments and environment variables to the Cloud Run Job.

---

## Code Architecture Deep Dive

The application is structured for modularity, testability, and scalability.

```
bsi-audit-automator/
│
├── main.py                     # Main entry point, argument parsing
│
├── src/
│   ├── clients/
│   │   ├── gcs_client.py       # Handles all GCS interactions
│   │   ├── rag_client.py       # Handles Vector Search (RAG) queries
│   │   └── ai_client.py        # Handles all Vertex AI Gemini interactions
│   │
│   ├── etl/
│   │   └── processor.py        # The ETL processor for the RAG pipeline
│   │
│   ├── audit/
│   │   ├── controller.py       # Orchestrates all audit stages
│   │   ├── report_generator.py # Assembles the final report
│   │   └── stages/             # Logic for each specific audit stage
│   │       ├── control_catalog.py # Helper to parse BSI controls
│   │       └── ... (and so on for each chapter)
│   │
│   ├── config.py               # Loads configuration from environment variables
│   └── logging_setup.py        # Configures application logging
│
├── assets/                     # Non-code assets
│   ├── json/                   # JSON assets (schemas, data)
│   └── prompts/                # .txt prompt templates
│
└── scripts/                    # Helper shell scripts for deployment & execution
```

### Core Application Modules (`src/`)

*   **`main.py`**: The application's entrypoint. It uses `argparse` to determine which high-level task to perform (e.g., `--run-etl`, `--run-stage Chapter-3`, `--generate-report`). It initializes all necessary clients and then calls the appropriate controller or processor to execute the task. It handles the top-level `asyncio` event loop for asynchronous audit stages.

*   **`config.py`**: Defines and loads all application configuration from environment variables into a clean, immutable `AppConfig` dataclass. It validates that all required variables are present at startup, preventing configuration errors.

*   **`logging_setup.py`**: Configures the root logger. It adjusts log levels based on whether `TEST` mode is enabled, ensuring verbose output for development and cleaner, high-level logs for production.

*   **`clients/gcs_client.py`**: Encapsulates all interactions with Google Cloud Storage. It provides methods for listing files, downloading file bytes, and reading/writing JSON objects, ensuring all I/O is cloud-native.

*   **`clients/ai_client.py`**: A critical module that abstracts all communication with the Vertex AI Gemini API. It handles client initialization, embedding generation, and schema-driven JSON generation. All the rules for retries, error handling, and model configuration are contained here.

*   **`clients/rag_client.py`**: A dedicated client for the "Retrieval" part of RAG. It connects to the deployed Vertex AI Vector Search endpoint and provides a simple method (`get_context_for_query`) to find and retrieve the text of the most relevant document chunks for any given question.

*   **`etl/processor.py`**: This module contains the logic for the **Extract, Transform, Load (ETL)** process, which is the first and most critical step of the RAG pipeline. It finds all customer source documents, breaks them down into searchable chunks, generates vector embeddings for each chunk, and uploads them in the correct format for Vertex AI Vector Search to ingest.

*   **`audit/controller.py`**: The "brain" of the audit process. It maintains a dictionary of all available audit stages (e.g., "Chapter-1", "Chapter-3"). When called, it orchestrates the execution of these stages, handling state management by checking GCS for existing results before running a stage, making the entire process resumable.

*   **`audit/report_generator.py`**: Responsible for the final, deterministic assembly of the report. It implements the "copy-on-write" logic for the master report template—loading it from a local asset and saving it to a customer-specific GCS path on the first run. For subsequent runs, it reads the template from GCS, populates it with the results from all completed stage stubs, and saves the final report.

---

## The Audit Stages Explained

Each "Chapter" is an independent stage orchestrated by the `AuditController`.

### **Phase 0: ETL (Embedding)** (`etl/processor.py`)
*   **Purpose:** To process the customer's unstructured source documents (PDFs) and prepare them for efficient, semantic searching. This is the "indexing" step of the RAG pipeline and must be run before any RAG-based stages (like Chapter 1).
*   **Logic:** This processor executes a five-step pipeline:
    1.  **Extract:** Lists all source document blobs from the customer's GCS directory.
    2.  **Chunk:** Reads each PDF, extracts the text, and uses a text splitter (from `PyMuPDF` and `langchain`) to break the content into small, semantically coherent chunks.
    3.  **Embed:** Sends the text of every chunk to the Vertex AI embedding model (`gemini-embedding-001`) to get a vector representation.
    4.  **Format:** Structures the chunk metadata and its corresponding embedding vector into the specific JSON format required by Vertex AI Vector Search.
    5.  **Load:** Uploads the final `embeddings.json` file to the GCS path that the Vector Index is configured to monitor.

### **Chapter 1: General Information** (`audit/stages/stage_1_general.py`)
*   **Purpose:** To generate the high-level, introductory content of the audit report.
*   **Logic:** This stage now uses the RAG pipeline. It formulates specific queries (e.g., "scope of the information network," "members of the audit team"), retrieves relevant context from the customer documents using the `RagClient`, and passes that evidence to the AI to generate a grounded, factual response for each subchapter.

### **Chapter 3: Document Review** (`audit/stages/stage_3_dokumentenpruefung.py`)
*   **Purpose:** To perform the initial review of the customer's core security documents.
*   **Logic:** This stage runs a separate, concurrent AI request for each subchapter (3.1, 3.2, 3.3.1, etc.). Each request will be enhanced with RAG to provide specific document evidence to the AI, which then answers the questions for that section. The results are aggregated into a single JSON file for the stage.

### **Chapter 4: Audit Plan Creation** (`audit/stages/stage_4_pruefplan.py`)
*   **Purpose:** To generate a compliant and plausible audit plan.
*   **Logic:** This stage's prompts are uniquely focused on **planning** rather than analysis. They are enriched with the rules from the BSI `Auditierungsschema` (e.g., "select at least 6 Bausteine," "ISMS.1 is mandatory"), guiding the AI to produce a valid plan.

### **Chapter 5: On-Site Audit Verification** (`audit/stages/stage_5_vor_ort_audit.py`)
*   **Purpose:** To simulate the on-site audit by verifying the implementation of controls selected in Chapter 4.
*   **Logic:**
    1.  **Dependency:** It first loads the `Chapter-4.json` results from GCS to know which `Bausteine` were selected for the audit.
    2.  **Control Lookup:** It uses the `ControlCatalog` helper to parse the `BSI_GS_OSCAL...json` file and retrieve the full list of controls for the selected Bausteine.
    3.  **Prompting:** For subchapter 5.5.2, it constructs a detailed prompt containing the list of controls to be verified. The AI is asked to return a structured list of findings, one for each control.

### **Chapter 7: Appendix** (`audit/stages/stage_7_anhang.py`)
*   **Purpose:** To generate the report's appendix.
*   **Logic:** This is a hybrid stage combining deterministic logic and AI summarization.
    1.  **Subchapter 7.1 (Reference Documents):** This part is **deterministic**. It calls the `gcs_client` to list all files in the customer's source GCS folder and formats them into a table.
    2.  **Subchapter 7.2 (Deviations):** This part **uses AI**. It loads the results from both `Chapter-3.json` and `Chapter-5.json`, extracts all noted deviations and negative findings, and passes this raw list to the AI to be summarized into a formal table.

---

## Scripts Explained

### `scripts/deploy-audit-job.sh`
*   **When to use:** Run this once per project, or whenever you change the `Dockerfile` or core Python dependencies in `requirements.txt`.
*   **What it does:** This script builds your Python application into a Docker container, pushes it to Google Artifact Registry, and deploys it as a generic Cloud Run Job named `bsi-audit-automator-job`.

### `scripts/execute-audit-job.sh`
*   **When to use:** This is your primary script for running any part of the audit pipeline.
*   **What it does:** It's an interactive script that:
    1.  Fetches the required cloud resource details from your Terraform state.
    2.  Prompts you to select the specific **task** you want to run (e.g., ETL, Chapter-5, Generate Report).
    3.  Constructs the appropriate `gcloud run jobs execute` command, passing the correct environment variables and command-line arguments to `main.py`.

### `scripts/redeploy-index.sh`
*   **When to use:** Use this script if you need to manually force the Vertex AI Index to be deployed to the Index Endpoint. This is typically only needed for troubleshooting, as the initial deployment is handled automatically by Terraform. It does **not** re-process your data.
*   **What it does:** It runs the `gcloud ai index-endpoints deploy-index` command with the correct resource IDs fetched from your Terraform state.

### `scripts/envs.sh`
*   **When to use:** For local development and debugging only.
*   **What it does:** A convenience script to set up your local shell with the same environment variables that the cloud job uses. It dynamically pulls values from your Terraform state. You must run `source ./envs.sh` from the project root to use it.

---

## Manual Review & Editing: The Report Editor (`report_editor.html`)

The `report_editor.html` file is a standalone, browser-based utility designed for the final, manual review and editing of the generated audit report. While the pipeline automates the data gathering and initial drafting, this tool empowers a human auditor to make final adjustments, correct nuances, and sign off on the content before submission.

### How to Use
1.  **Open:** Open the `report_editor.html` file in a modern web browser (e.g., Chrome, Firefox).
2.  **Load:** Click the **"Choose File"** button and select the JSON report you want to edit (e.g., `final_audit_report.json` generated by the pipeline).
3.  **Filter & Edit:** Use the chapter filter buttons to navigate. Edit the content directly in the form fields.
4.  **Export:** Once finished, click the **"Export to JSON"** button to save a new file containing your changes.

### Place in Workflow
This tool is intended to be used **after** the `--generate-report` task has been successfully run. The auditor takes the machine-generated `final_audit_report.json`, loads it into this editor for the final human touch, and exports the result.

---

## Configuration

The application is configured entirely via environment variables passed by the `execute-audit-job.sh` script or loaded locally by `envs.sh`.

| Variable | Required? | Description |
| :--- | :---: | :--- |
| `GCP_PROJECT_ID` | Yes | The Google Cloud Project ID. |
| `BUCKET_NAME` | Yes | The GCS bucket for all I/O operations. |
| `CUSTOMER_ID` | Yes | A unique identifier for the customer, used as the subdirectory name. |
| `SOURCE_PREFIX` | Yes | GCS prefix within the customer's directory for source files. |
| `OUTPUT_PREFIX` | Yes | GCS prefix within the customer's directory for generated files. |
| `AUDIT_TYPE` | Yes | Specifies the audit type (e.g., "Zertifizierungsaudit"). |
| `INDEX_ENDPOINT_ID`| Yes | The numeric ID of the deployed Vertex AI Index Endpoint. |
| `MAX_CONCURRENT_AI_REQUESTS` | No | Max parallel requests to the Gemini API. Defaults to `5`. |
| `VERTEX_AI_REGION`| Yes | The region where Vertex AI resources are deployed. |
| `TEST` | No | Set to `"true"` to enable test mode. Defaults to `false`. |
```