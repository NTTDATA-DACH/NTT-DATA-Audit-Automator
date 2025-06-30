# BSI Grundschutz Audit Automator

A cloud-native application designed to automate security audits based on the German BSI Grundschutz framework using Google Cloud Vertex AI.

## Project Overview

The primary goal of this project is to ingest customer security documentation, process it against BSI standards using a sophisticated AI pipeline, and generate a structured audit report that mirrors the official `Muster_Auditbericht_Kompendium_V3.0` template.

The application runs as a batch job on Google Cloud Platform (GCP) and leverages Infrastructure as Code (IaC) for reliable and repeatable deployments.

## Architectural Overview (The "Why")

This project's architecture is built on a core principle: **Accuracy and Auditability**. For a formal audit, every finding must be traceable and reliable. This has led to several key architectural decisions.

### Why RAG over a Large Context Window?

While modern Large Language Models (LLMs) have massive context windows, our initial analysis and prototyping showed that providing a large, complex set of documents in a single prompt can lead to inconsistent results and factual errors ("hallucinations").

This project has explicitly pivoted to a **Retrieval-Augmented Generation (RAG)** architecture using **Vertex AI Vector Search**. This approach is superior for our use case because:

1.  **Accuracy:** By retrieving only the most relevant text chunks for a specific query, we provide the model with a smaller, focused context, drastically reducing the risk of error.
2.  **Traceability:** For every generated finding, we can log exactly which chunks from which source documents were used as context. This is a critical auditability feature.
3.  **Scalability:** The architecture scales to hundreds of documents without increasing the complexity or cost of a single AI call.

### The "Two-Plus-One" Verification Pattern

To further enhance reliability, this project uses a three-step generation process for each finding:
1.  **Generate A:** The AI is given the context and asked to produce a finding.
2.  **Generate B:** The same request is run a second time in parallel.
3.  **Synthesize:** A third AI call is made, asking a "senior reviewer" persona to analyze results A and B and create a final, synthesized, and more accurate result.

## High-Level Workflow (The "How")

The end-to-end process is broken down into four distinct phases:

```
+--------------------------------+      +---------------------------+      +--------------------------+
| Phase 0:                       |      | Phase 1:                  |      | Phase 2:                 |
| Infrastructure Provisioning    |----->| Data Ingestion & Indexing |----->| Staged Audit Generation  |-----> [Final Report]
| (Terraform)                    |      | (Python ETL)              |      | (Python RAG)             |
+--------------------------------+      +---------------------------+      +--------------------------+
       |                                      |                                  |
       v                                      v                                  v
[VPC, GCS Bucket, Vector DB]           [Populated Vector Index]           [Validated JSON Stubs]
```

## Getting Started (Installation & Setup)

Follow these steps to deploy and run the application.

### Prerequisites

1.  **Google Cloud SDK (`gcloud`):** [Install and initialize](https://cloud.google.com/sdk/docs/install) the `gcloud` CLI. Authenticate with `gcloud auth login`.
2.  **Terraform:** [Install Terraform](https://learn.hashicorp.com/tutorials/terraform/install-cli) (version 1.0.0 or higher).
3.  **Python:** Python 3.9 or higher.

### Step 1: Clone the Repository
Clone this repository to your local machine.
```bash
git clone <your-repo-url>
cd <repository-directory>
```

### Step 2: Enable Required APIs
Run the provided shell script to enable all necessary Google Cloud APIs for your project.
```bash
# The script will prompt you to confirm your project ID.
bash ./enable_apis.sh
```

### Step 3: Deploy the Infrastructure
The Terraform configuration will provision the GCS bucket, VPC network, and Vertex AI Vector Search index.
1.  Navigate to the `terraform/` directory.
2.  Edit the `terraform.tfvars` file to set your `project_id` and `customer_id`.
3.  Deploy the infrastructure:
    ```bash
    terraform init
    terraform apply
    ```
This will take 10-15 minutes to provision the network and Vertex AI resources.

### Step 4: Upload Source Documents
After Terraform succeeds, a new GCS bucket will be created (e.g., `bsi-audit-kunde-x-audit-data`).
-   Upload all the customer's source documents (PDFs, etc.) into a subdirectory within this new bucket. For example: `gs://bsi-audit-kunde-x-audit-data/kunde-x/source_documents/`

### Step 5: Run the Application
The Python application orchestrates the data processing and report generation.

1.  **Set Environment Variables:** Configure the application by setting the following environment variables (a `.env` file is recommended for local development).
2.  **Run the Main Script:**
    ```bash
    # (Example command, will be finalized when the Python app is built)
    python main.py
    ```

## Configuration

The Python application is configured entirely via environment variables.

| Variable | Required? | Description |
| :--- | :---: | :--- |
| `GCP_PROJECT_ID` | Yes | The Google Cloud Project ID. |
| `BUCKET_NAME` | Yes | The GCS bucket created by Terraform. |
| `CUSTOMER_ID` | Yes | A unique identifier for the customer, used as the subdirectory name. |
| `SOURCE_PREFIX` | Yes | GCS prefix for source files (e.g., `kunde-x/source_documents/`). |
| `OUTPUT_PREFIX` | Yes | GCS prefix for generated files (e.g., `kunde-x/output/`). |
| `AUDIT_TYPE` | Yes | Specifies the audit type (e.g., "Zertifizierungsaudit"). |
| `VERTEX_AI_REGION` | Yes | The region where Vertex AI resources were deployed (e.g., `europe-west4`). |
| `INDEX_ENDPOINT_ID` | Yes | The numeric ID of the deployed Vertex AI Index Endpoint. |
| `TEST` | No | Set to `"true"` to enable test mode. Defaults to `false`. |

## Project Structure
```
.
├── main.py                     # Main application entrypoint
├── README.md                   # This file
├── requirements.txt            # Python dependencies
├── enable_apis.sh              # Script to enable GCP APIs
│
├── assets/
│   ├── prompts/
│   └── schemas/
│       └── master_report_template.json
│
├── src/                        # Python source code
│   ├── __init__.py
│   ├── config.py
│   └── ...
│
└── terraform/
    ├── main.tf
    ├── variables.tf
    └── outputs.tf
```