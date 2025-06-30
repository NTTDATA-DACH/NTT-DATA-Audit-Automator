# BSI Grundschutz Audit Automator

This project automates BSI Grundschutz security audits by transforming customer documentation into a structured report using a Retrieval-Augmented Generation (RAG) AI pipeline on Google Cloud.

## The "How": End-to-End Workflow

This project is a multi-stage pipeline. Follow these steps in order to get from source documents to a final report.

  <!-- Placeholder for a real diagram URL -->
**Diagram:**
`[Customer PDFs]` -> **(Step 3: Upload)** -> `[GCS Bucket]` -> **(Step 4: Python ETL)** -> `[Populated Vector DB]` -> **(Step 5: gcloud Deploy)** -> `[Queryable Endpoint]` -> **(Step 6: Python RAG)** -> `[Final Report]`

---

### **SETUP (DO THIS ONCE)**

#### **Step 1: Prerequisites**
1.  **Google Cloud SDK (`gcloud`):** [Install and initialize](https://cloud.google.com/sdk/docs/install). Run `gcloud auth login` and `gcloud config set project [your-project-id]`.
2.  **Terraform:** [Install Terraform](https://learn.hashicorp.com/tutorials/terraform/install-cli) (version 1.0.0 or higher).
3.  **Python:** Python 3.9 or higher.
4.  **Clone the Repository:**
    ```bash
    git clone <your-repo-url>
    cd <repository-directory>
    ```
5.  **Enable Required APIs:** Run the provided shell script.
    ```bash
    bash ./enable_apis.sh
    ```

---

### **EXECUTION (RUN FOR EACH CUSTOMER AUDIT)**

#### **Step 2: Deploy the Infrastructure**
This Terraform script creates all the necessary cloud resources for a specific customer.
1.  Navigate to the `terraform/` directory.
2.  Edit the `terraform.tfvars` file to set your `project_id`, `project_number`, and a unique `customer_id`.
3.  Deploy the infrastructure:
    ```bash
    terraform init
    terraform apply
    ```
    This creates a unique GCS Bucket, a VPC Network, and the Vertex AI Vector Search Index and Endpoint. Note the output values, especially the `bucket_name`.

#### **Step 3: Upload Customer Source Documents**
This is the **critical first data step**.
1.  After `terraform apply` succeeds, it will have created a new GCS bucket (e.g., `bsi-audit-kunde-x-kunde-x-audit-data`).
2.  Create a specific subdirectory for the customer's source files and upload them there. **This is where you upload the customer's reference documents (PDFs).**

    **Example using `gsutil`:**
    ```bash
    # Get the bucket name from Terraform output
    BUCKET_NAME=$(terraform output -raw bsi_audit_bucket_name)

    # Define the source and destination paths
    LOCAL_SOURCE_FOLDER="./customer_documents/"
    GCS_DESTINATION_PATH="gs://${BUCKET_NAME}/kunde-x/source_documents/"

    # Upload the files
    gsutil rsync -r "${LOCAL_SOURCE_FOLDER}" "${GCS_DESTINATION_PATH}"

    echo "Customer documents uploaded to: ${GCS_DESTINATION_PATH}"
    ```

#### **Step 4: Process Documents & Populate the Index**
This step uses our Python application in "ETL mode" to prepare the data for the RAG pipeline.
1.  Configure your environment variables (see Configuration section below). Ensure `SOURCE_PREFIX` points to the path from Step 3.
2.  Run the data ingestion script (this part of the Python app needs to be built):
    ```bash
    # Placeholder for the future Python command
    python main.py --run-etl
    ```
    This script will:
    *   Read the documents from the `source_documents` GCS path.
    *   Chunk and embed them.
    *   Upload the final `index_data.jsonl` file to the GCS path the index is watching (e.g., `gs://[bucket-name]/kunde-x/vector_index_data/`).
    *   The Vertex AI Index will begin updating automatically. This can take 30-60+ minutes.

#### **Step 5: Deploy the Populated Index to the Endpoint**
After the index has finished updating, you must **manually deploy it to the endpoint** to make it queryable.
1.  Get the exact command from your Terraform output:
    ```bash
    terraform output next_step_gcloud_command
    ```
2.  Copy and run the `gcloud ai index-endpoints deploy-index ...` command provided. This can also take 30+ minutes.

#### **Step 6: Generate the Final Audit Report**
Once the index is deployed, you can run the main application in "generation mode".
1.  Run the report generation script (this part of the Python app needs to be built):
    ```bash
    # Placeholder for the future Python command
    python main.py --generate-report
    ```
    This script will:
    *   Iterate through the `master_report_template.json`.
    *   For each section, query the deployed Vertex AI Index Endpoint to get relevant context.
    *   Execute the "Two-Plus-One" generation pattern to create a validated JSON stub.
    *   Save the stub to GCS.
    *   Finally, assemble all stubs into the final report.

## The "Why": Architectural Decisions

This project uses a **Retrieval-Augmented Generation (RAG)** architecture for **Accuracy and Auditability**. A "Two-Plus-One" verification pattern is used for each finding to minimize errors and ensure consistency. This robust, multi-step process is more reliable for formal audit scenarios than using a single large-context prompt.

## Configuration

The Python application is configured entirely via environment variables.

| Variable | Required? | Description |
| :--- | :---: | :--- |
| `GCP_PROJECT_ID` | Yes | The Google Cloud Project ID. |
| `CUSTOMER_ID` | Yes | A unique identifier for the customer (e.g., `kunde-x`). |
| `SOURCE_PREFIX` | Yes | GCS prefix for source documents (e.g., `kunde-x/source_documents/`). |
| `OUTPUT_PREFIX` | Yes | GCS prefix for generated files (e.g., `kunde-x/output/`). |
| `AUDIT_TYPE` | Yes | Specifies the audit type (e.g., "Zertifizierungsaudit"). |
| `VERTEX_AI_REGION`| Yes | The region where Vertex AI resources are deployed (e.g., `europe-west4`). |
| `INDEX_ENDPOINT_ID`| Yes | The numeric ID of the deployed Vertex AI Index Endpoint (from `terraform output`). |
| `TEST` | No | Set to `"true"` to enable test mode. Defaults to `false`. |