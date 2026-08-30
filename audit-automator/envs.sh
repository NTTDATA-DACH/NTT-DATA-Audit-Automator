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
#      auditor --run-gs-check-extraction
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
# Assign first, export second (SC2155): `export VAR="$(cmd)"` hides the command's exit
# status from `set -e`, so a failing terraform output would leave the variable empty and
# still print the success banner below.
GCP_PROJECT_ID="$(terraform -chdir=${TERRAFORM_DIR} output -raw project_id)"
BUCKET_NAME="$(terraform -chdir=${TERRAFORM_DIR} output -raw gcs_bucket_name)"
DOC_AI_PROCESSOR_NAME="$(terraform -chdir=${TERRAFORM_DIR} output -raw documentai_processor_name)"
export GCP_PROJECT_ID BUCKET_NAME DOC_AI_PROCESSOR_NAME

# --- Static Values for Local Development ---
# These prefixes now reflect the simpler GCS layout.
export SOURCE_PREFIX="source_documents/"
export OUTPUT_PREFIX="output/"

# Manually set the audit type and test mode for your local run
export AUDIT_TYPE="2. Überwachungsaudit"
export TEST="true"
export MAX_CONCURRENT_AI_REQUESTS=5 # New: Tunable concurrency limit

# --- AI behaviour (all optional; the defaults in src/constants.py apply if unset) ---
# export CHUNK_PROCESSING_MODEL="gemini-3.7-flash"
# export GROUND_TRUTH_MODEL="gemini-3.1-pro"
# export THINKING_LEVEL="minimal"          # minimal | low | medium | high
# export CHECKER_MODEL="gemini-3.1-pro"    # second opinion in the maker/checker pass
export ENABLE_MAKER_CHECKER="true"         # "false" halves the AI calls (no verification)
# Pins repeatedly-attached source PDFs server-side instead of re-sending them on every
# call (Strukturanalyse alone is context for 24 calls per run). "false" to disable.
export ENABLE_CONTEXT_CACHE="true"
# export CONTEXT_CACHE_TTL_SECONDS="9000"  # caches are deleted at the end of a run

# --- NEW: Helper function for correct execution ---
# This alias ensures we always run the application as a module,
# which correctly resolves the relative imports in src/main.py.
auditor() {
    python -m src.main "$@"
}


for required in GCP_PROJECT_ID BUCKET_NAME DOC_AI_PROCESSOR_NAME; do
    if [ -z "${!required}" ]; then
        echo "❌ Error: '${required}' is empty — the corresponding terraform output returned nothing."
        return 1
    fi
done

set +e
echo "✅ Environment variables configured successfully'."
echo "   - GCP_PROJECT_ID: ${GCP_PROJECT_ID}"
echo "   - BUCKET_NAME:    ${BUCKET_NAME}"
echo "   - DOC_AI_PROC:    ${DOC_AI_PROCESSOR_NAME}"
echo "   - AUDIT_TYPE:     ${AUDIT_TYPE}"
echo "   - TEST mode:      ${TEST}"
echo ""
echo "👉 A new command 'auditor' is now available in your shell."
echo "   Run the app with: auditor --run-stage Chapter-1"
