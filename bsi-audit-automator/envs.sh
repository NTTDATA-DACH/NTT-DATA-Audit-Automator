#!/bin/bash
#
# DYNAMIC Environment variable setup for local BSI Audit Automator development.
#
# This script dynamically fetches configuration from your Terraform state,
# ensuring your local environment matches the cloud deployment.
#
# It also defines a helper function `bsi-auditor` to simplify running the app.
#
# PREREQUISITES:
#   - You must have run 'terraform apply' in the ./terraform directory.
#   - You must have the 'terraform' CLI installed and in your PATH.
#
# USAGE:
#   Run this command from the project root (the 'bsi-audit-automator' directory):
#      source ./envs.sh
#
#   Then, you can run the application like this:
#      bsi-auditor --run-etl
#      bsi-auditor --run-stage Chapter-1
#
set -e # Exit on error

TERRAFORM_DIR="../terraform"

if [ ! -d "$TERRAFORM_DIR" ]; then
    echo "❌ Error: Terraform directory not found at '$TERRAFORM_DIR'. Please run this script from the project root."
    return 1
fi
if ! command -v terraform &> /dev/null; then
    echo "❌ Error: 'terraform' command not found. Please install Terraform."
    return 1
fi

echo "🔹 Fetching infrastructure details from Terraform..."

# --- Dynamic Values from Terraform ---
export GCP_PROJECT_ID="$(terraform -chdir=${TERRAFORM_DIR} output -raw project_id)"
export VERTEX_AI_REGION="$(terraform -chdir=${TERRAFORM_DIR} output -raw region)"
export BUCKET_NAME="$(terraform -chdir=${TERRAFORM_DIR} output -raw vector_index_data_gcs_path | cut -d'/' -f3)"
export INDEX_ENDPOINT_ID_FULL="$(terraform -chdir=${TERRAFORM_DIR} output -raw vertex_ai_index_endpoint_id)"
export GCP_PROJECT_NUMBER="$(echo "${INDEX_ENDPOINT_ID_FULL}" | cut -d'/' -f2)"
export INDEX_ENDPOINT_ID="$(basename "${INDEX_ENDPOINT_ID_FULL}")"
# NEW: Fetch the public domain if it exists, otherwise set to empty string.
export INDEX_ENDPOINT_PUBLIC_DOMAIN="$(terraform -chdir=${TERRAFORM_DIR} output -raw public_endpoint_domain_name 2>/dev/null || echo '')"


# --- Static Values for Local Development ---
# These prefixes now reflect the simpler GCS layout.
export SOURCE_PREFIX="source_documents/"
export OUTPUT_PREFIX="output/"
export ETL_STATUS_PREFIX="output/etl_status/"

# Manually set the audit type and test mode for your local run
export AUDIT_TYPE="Zertifizierungsaudit"
export TEST="true"
export MAX_CONCURRENT_AI_REQUESTS=5 # New: Tunable concurrency limit

# --- NEW: Helper function for correct execution ---
# This alias ensures we always run the application as a module,
# which correctly resolves the relative imports in src/main.py.
bsi-auditor() {
    python -m src.main "$@"
}


set +e
echo "✅ Environment variables configured successfully'."
echo "   - GCP_PROJECT_ID: ${GCP_PROJECT_ID}"
echo "   - BUCKET_NAME:    ${BUCKET_NAME}"
echo "   - TEST mode:      ${TEST}"
echo ""
echo "👉 A new command 'bsi-auditor' is now available in your shell."
echo "   Run the app with: bsi-auditor --run-stage Chapter-1"