#!/bin/bash
set -euo pipefail

# ===================================================================
# SCRIPT TO COMPLETELY RESET THE AUDIT ENVIRONMENT
# ===================================================================
#
# WHAT IT DOES:
# This is a DESTRUCTIVE script that prepares the environment for a
# completely new audit. It performs two main actions:
# 1. Deletes all generated data from the GCS bucket, including:
#    - All source documents (`source_documents/`)
#    - All generated embeddings (`vector_index_data/`)
#    - All ETL status markers and results (`output/`)
# 2. Marks the Vertex AI Index resource for recreation in Terraform.
#
# WHEN TO USE IT:
# Run this script BEFORE starting a new audit for a new customer or
# when you have a significantly changed set of source documents and
# want to ensure no old data remains.
#
# PREREQUISITES:
#   - Must be run from the project root ('bsi-audit-automator/').
#   - 'gcloud', 'gsutil', and 'terraform' CLIs must be installed and authenticated.
#   - `terraform apply` must have been run successfully at least once.
#

echo "🚨 WARNING: This is a destructive operation! 🚨"
echo "This script will delete ALL audit data from GCS and mark the"
echo "Vertex AI Index for recreation."

# --- Configuration & Validation ---
TERRAFORM_DIR="../terraform"

if [ ! -d "$TERRAFORM_DIR" ]; then
    echo "❌ Error: Terraform directory not found at '$TERRAFORM_DIR'. Please run this from the project root."
    exit 1
fi

echo "🔹 Fetching infrastructure details from Terraform state..."

# --- Dynamic Values from Terraform ---
BUCKET_NAME="$(terraform -chdir=${TERRAFORM_DIR} output -raw vector_index_data_gcs_path | cut -d'/' -f3)"
INDEX_RESOURCE_NAME="google_vertex_ai_index.bsi_audit_index"

# --- User Confirmation ---
echo "-----------------------------------------------------"
echo "The following actions will be performed:"
echo "  1. DELETE all objects in gs://${BUCKET_NAME}/source_documents/"
echo "  2. DELETE all objects in gs://${BUCKET_NAME}/vector_index_data/"
echo "  3. DELETE all objects in gs://${BUCKET_NAME}/output/"
echo "  4. TAINT the Terraform resource '${INDEX_RESOURCE_NAME}', forcing it to be recreated on next 'apply'."
echo "-----------------------------------------------------"

read -p "Are you absolutely sure you want to proceed? (y/n) " -n 1 -r
echo # move to a new line
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted by user."
    exit 1
fi

# --- Execute GCS Cleanup ---
echo "🗑️  Deleting existing data from GCS bucket..."
# Use gsutil with -m for parallel deletion, which is faster.
# The `|| true` prevents the script from exiting if a folder is already empty.
gsutil -m rm -r "gs://${BUCKET_NAME}/source_documents/*" || true
gsutil -m rm -r "gs://${BUCKET_NAME}/vector_index_data/*" || true
gsutil -m rm -r "gs://${BUCKET_NAME}/output/*" || true
echo "✅ GCS data deleted."

# --- Execute Terraform Taint ---
echo "🎯 Marking Vertex AI Index for recreation..."
terraform -chdir=${TERRAFORM_DIR} taint "${INDEX_RESOURCE_NAME}"
echo "✅ Index resource tainted successfully."

# --- Final Instructions ---
echo ""
echo "✅ Reset complete. The environment is now clean."
echo "   Next Steps:"
echo "   1. Navigate to the terraform directory: cd ../terraform"
echo "   2. Apply the changes to recreate the index: terraform apply -auto-approve"
echo "   3. Navigate back to the project root: cd .."
echo "   4. Upload the NEW set of source documents to gs://${BUCKET_NAME}/source_documents/"
echo "   5. You can now start the audit pipeline with '--run-etl'."