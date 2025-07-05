#!/bin/bash
set -euo pipefail

# ===================================================================
# SCRIPT TO REFRESH AUDIT DATA AND CLEAR THE INDEX
# ===================================================================
#
# WHAT IT DOES:
# This script provides a "fast refresh" for an audit by clearing out
# all existing data from GCS and triggering an update on the Vertex
# AI Index to remove the old embeddings. This is a DATA-LAYER
# operation and does NOT destroy the underlying cloud infrastructure.
#
# It performs the following actions:
# 1. Archives the old vector embeddings by moving them.
# 2. Deletes all previous outputs and status markers.
# 3. Triggers a manual update of the Vertex AI Index, which causes it
#    to re-scan the (now empty) source folder and remove all entries.
#
# WHEN TO USE IT:
# Use this script when you receive new source documents for an
# existing audit and want to start the ETL and analysis process
# from a clean slate without a full `terraform apply`.
#
# PREREQUISITES:
#   - Must be run from the project root ('bsi-audit-automator/').
#   - 'gcloud', 'gsutil', and 'terraform' CLIs must be installed.
#

echo "✅ This script will perform a 'fast refresh' of the audit data."
echo "   It will archive old embeddings and clear the Vertex AI Index."

# --- Cleanup handler: ensures the temporary metadata file is deleted on exit ---
cleanup() {
  rm -f index_metadata.yaml
  echo "🔹 Temporary metadata file cleaned up."
}
trap cleanup EXIT

# --- Configuration & Validation ---
TERRAFORM_DIR="../terraform"
METADATA_FILE="index_metadata.yaml"

if [ ! -d "$TERRAFORM_DIR" ]; then
    echo "❌ Error: Terraform directory not found at '$TERRAFORM_DIR'. Please run this from the project root."
    exit 1
fi

echo "🔹 Fetching infrastructure details from Terraform state..."

# --- Dynamic Values from Terraform ---
PROJECT_ID="$(terraform -chdir=${TERRAFORM_DIR} output -raw project_id)"
REGION="$(terraform -chdir=${TERRAFORM_DIR} output -raw region)"
BUCKET_NAME="$(terraform -chdir=${TERRAFORM_DIR} output -raw vector_index_data_gcs_path | cut -d'/' -f3)"
INDEX_ID="$(terraform -chdir=${TERRAFORM_DIR} output -raw vertex_ai_index_id | xargs basename)"

CONTENTS_DELTA_URI="gs://${BUCKET_NAME}/vector_index_data/"
ARCHIVE_TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
ARCHIVE_PATH="gs://${BUCKET_NAME}/vector_index_data_archive/${ARCHIVE_TIMESTAMP}/"

# --- User Confirmation ---
echo "-----------------------------------------------------"
echo "The following data-layer actions will be performed:"
echo "  1. MOVE all embedding files from:"
echo "     ${CONTENTS_DELTA_URI}"
echo "     TO (archive):"
echo "     ${ARCHIVE_PATH}"
echo "  2. DELETE all previous results and status markers from:"
echo "     gs://${BUCKET_NAME}/output/"
echo "  3. TRIGGER an update on Index '${INDEX_ID}' to remove all entries."
echo "-----------------------------------------------------"
read -p "Are you sure you want to refresh the audit data? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted by user."
    exit 1
fi

# --- Execute GCS Data Archival and Cleanup ---
echo "📦 Archiving old embedding data..."
# Use || true to prevent script failure if the source directory is empty
gsutil -m mv "${CONTENTS_DELTA_URI}*" "${ARCHIVE_PATH}" || true

echo "🗑️  Deleting old output files..."
gsutil -m rm -r "gs://${BUCKET_NAME}/output/*" || true
echo "✅ GCS data archival and cleanup complete."

# --- Trigger Index Update ---
echo "🔹 Generating temporary metadata file for index update..."
cat <<EOF > ${METADATA_FILE}
contentsDeltaUri: "${CONTENTS_DELTA_URI}"
config:
  dimensions: 3072
  approximateNeighborsCount: 150
  algorithmConfig:
    treeAhConfig:
      leafNodeEmbeddingCount: 500
EOF

echo "🚀 Sending update command to Vertex AI Index '${INDEX_ID}'..."
gcloud ai indexes update "${INDEX_ID}" \
  --metadata-file="./${METADATA_FILE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}"

echo ""
echo "✅ Data refresh process initiated."
echo "   The index will now update and remove the old embeddings."
echo "   You can monitor its 'Dense vector count' in the GCP Console."
echo "   Next Steps:"
echo "   1. Upload the NEW set of source documents to gs://${BUCKET_NAME}/source_documents/"
echo "   2. Run the pipeline starting with the ETL job: ./scripts/execute-audit-job.sh"