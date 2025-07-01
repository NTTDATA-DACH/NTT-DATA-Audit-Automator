#!/bin/bash
#
# DYNAMIC Environment variable setup for local BSI Audit Automator development.
#
# This script dynamically fetches configuration from your Terraform state,
# ensuring your local environment matches the cloud deployment.
#
# PREREQUISITES:
#   - You must have run 'terraform apply' in the ./terraform directory.
#   - You must have the 'terraform' CLI installed and in your PATH.
#
# USAGE:
#   Run this command from the project root (the 'bsi-audit-automator' directory):
#      source ./envs.sh
#
set -e # Exit on error

TERRAFORM_DIR="./terraform"

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
export GCP_PROJECT_NUMBER="$(terraform -chdir=${TERRAFORM_DIR} output -raw project_number)"
export CUSTOMER_ID="$(terraform -chdir=${TERRAFORM_DIR} output -raw customer_id)"
export VERTEX_AI_REGION="$(terraform -chdir=${TERRAFORM_DIR} output -raw region)"
export BUCKET_NAME="$(terraform -chdir=${TERRAFORM_DIR} output -raw vector_index_data_gcs_path | cut -d'/' -f3)"
INDEX_ENDPOINT_ID_FULL="$(terraform -chdir=${TERRAFORM_DIR} output -raw vertex_ai_index_endpoint_id)"
export INDEX_ENDPOINT_ID="$(basename "${INDEX_ENDPOINT_ID_FULL}")"

# --- Static Values for Local Development ---
# These prefixes now reflect the simpler GCS layout.
export SOURCE_PREFIX="source_documents/"
export OUTPUT_PREFIX="output/"

# Manually set the audit type and test mode for your local run
export AUDIT_TYPE="Zertifizierungsaudit"
export TEST="true"

set +e
echo "✅ Environment variables configured successfully for customer '${CUSTOMER_ID}'."
echo "   - GCP_PROJECT_ID: ${GCP_PROJECT_ID}"
echo "   - BUCKET_NAME:    ${BUCKET_NAME}"
echo "   - TEST mode:      ${TEST}"
echo "You can now run the Python application locally (e.g., 'python main.py --generate-report')."