#!/bin/bash
set -euo pipefail

# ===================================================================
# B: EXECUTE JOB FOR A CUSTOMER (Run this for each audit)
# ===================================================================

# --- Configuration ---
# Set customer-specific variables here. These are the only values you should need to change.
CUSTOMER_ID="kunde-x"
AUDIT_TYPE="Zertifizierungsaudit"
# Set to true for a dry run without calling the PaLM API.
TEST_MODE="false"

# --- Dynamic Values from Terraform ---
# Fetch infrastructure details from Terraform state to avoid hardcoding.
# The -chdir flag tells Terraform where to find the configuration files.
echo "🔹 Fetching infrastructure details from Terraform..."
TERRAFORM_DIR="../terraform"
GCP_PROJECT_ID="$(terraform -chdir=${TERRAFORM_DIR} output -raw project_id)"
VERTEX_AI_REGION="$(terraform -chdir=${TERRAFORM_DIR} output -raw region)"
# Extract the bucket name from the full GCS path output.
BUCKET_NAME="$(terraform -chdir=${TERRAFORM_DIR} output -raw vector_index_data_gcs_path | cut -d'/' -f3)"
# Extract the numeric ID from the full Vertex AI Index Endpoint resource name.
INDEX_ENDPOINT_ID_FULL="$(terraform -chdir=${TERRAFORM_DIR} output -raw vertex_ai_index_endpoint_id)"
INDEX_ENDPOINT_ID="$(basename "${INDEX_ENDPOINT_ID_FULL}")"

echo "🚀 Starting job execution for customer '${CUSTOMER_ID}'..."
gcloud run jobs execute "bsi-etl-job" \
  --region "${VERTEX_AI_REGION}" \
  --project "${GCP_PROJECT_ID}" \
  --wait \
  --update-env-vars="GCP_PROJECT_ID=${GCP_PROJECT_ID},CUSTOMER_ID=${CUSTOMER_ID},BUCKET_NAME=${BUCKET_NAME},INDEX_ENDPOINT_ID=${INDEX_ENDPOINT_ID},VERTEX_AI_REGION=${VERTEX_AI_REGION},SOURCE_PREFIX=${CUSTOMER_ID}/source_documents/,OUTPUT_PREFIX=${CUSTOMER_ID}/output/,AUDIT_TYPE=${AUDIT_TYPE},TEST=${TEST_MODE}"

echo "✅ Job execution for customer '${CUSTOMER_ID}' finished."