#!/bin/bash
set -euo pipefail

# ===================================================================
# Manually triggers a deploy-index operation on the endpoint.
#
# This is useful if the initial deployment during `terraform apply`
# needs to be re-run or modified without destroying the endpoint.
# Note: This does NOT re-ingest data. Data ingestion is automatic
# when files in the GCS directory change.
# ===================================================================

echo "🔹 Fetching infrastructure details from Terraform..."
TERRAFORM_DIR="../terraform"
PROJECT_ID="$(terraform -chdir=${TERRAFORM_DIR} output -raw project_id)"
REGION="$(terraform -chdir=${TERRAFORM_DIR} output -raw region)"
ENDPOINT_ID="$(terraform -chdir=${TERRAFORM_DIR} output -raw vertex_ai_index_endpoint_id | xargs basename)"
INDEX_ID="$(terraform -chdir=${TERRAFORM_DIR} output -raw vertex_ai_index_id | xargs basename)"

# Use a consistent deployed index ID
DEPLOYED_INDEX_ID="bsi_deployed_index_kunde_x"
DISPLAY_NAME="BSI Deployed Index"

echo "🚀 Attempting to deploy index '${INDEX_ID}' to endpoint '${ENDPOINT_ID}'..."

# The gcloud command to deploy the index to the endpoint
gcloud ai index-endpoints deploy-index "${ENDPOINT_ID}" \
  --index="${INDEX_ID}" \
  --deployed-index-id="${DEPLOYED_INDEX_ID}" \
  --display-name="${DISPLAY_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}"

echo "✅ Index deployment command sent successfully."
echo "   Monitor the deployment status in the Google Cloud Console."