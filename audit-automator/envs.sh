#!/bin/bash
#
# DYNAMIC Environment variable setup for local BSI Audit Automator development.
#
# This script dynamically fetches configuration from your Terraform state,
# ensuring your local environment matches the cloud deployment.
#
# It also defines a helper function `auditor` to simplify running the app.
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
#      auditor --run-etl
#      auditor --run-stage Chapter-1
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
export REGION="$(terraform -chdir=${TERRAFORM_DIR} output -raw region)"
export BUCKET_NAME="$(terraform -chdir=${TERRAFORM_DIR} output -raw gcs_bucket_name)"
export DOC_AI_PROCESSOR_NAME="$(terraform -chdir=${TERRAFORM_DIR} output -raw documentai_processor_name)"
# NEW: Fetch the public domain if it exists, otherwise set to empty string.

# --- Static Values for Local Development ---
# These prefixes now reflect the simpler GCS layout.
export SOURCE_PREFIX="source_documents/"
export OUTPUT_PREFIX="output/"

# Manually set the audit type and test mode for your local run
export AUDIT_TYPE="2. Überwachungsaudit"
export TEST="true"
export MAX_CONCURRENT_AI_REQUESTS=5 # New: Tunable concurrency limit

# --- NEW: Helper function for correct execution ---
# This alias ensures we always run the application as a module,
# which correctly resolves the relative imports in src/main.py.
auditor() {
    python -m src.main "$@"
}


set +e
echo "✅ Environment variables configured successfully'."
echo "   - GCP_PROJECT_ID: ${GCP_PROJECT_ID}"
echo "   - BUCKET_NAME:    ${BUCKET_NAME}"
echo "   - DOC_AI_PROC:    ${DOC_AI_PROCESSOR_NAME}"
echo "   - AUDIT_TYPE:     ${AUDIT_TYPE}"
echo "   - TEST mode:      ${TEST}"
echo ""
echo "👉 A new command 'bsi-auditor' is now available in your shell."
echo "   Run the app with: bsi-auditor --run-stage Chapter-1"
