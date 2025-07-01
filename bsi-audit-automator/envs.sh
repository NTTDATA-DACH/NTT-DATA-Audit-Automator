#!/bin/bash
#
# Environment variable setup for the BSI Audit Automator project.
#
# IMPORTANT:
# This script must be "sourced" to set the variables in your current shell.
# Do NOT execute it directly.
#
# USAGE:
#   1. Fill in the placeholder values below.
#   2. Run the following command from your terminal:
#      source envs.sh
#

echo "Setting up environment variables for the BSI Audit project..."

# --- Core Project & Customer Configuration ---
export GCP_PROJECT_ID="bsi-audit-kunde-x"
export GCP_PROJECT_NUMBER="905207908720" # Required by some GCP APIs
export CUSTOMER_ID="kunde-x"

# --- Cloud Resource Configuration (Get these from Terraform output) ---
export BUCKET_NAME="bsi-audit-kunde-x-kunde-x-audit-data"
export INDEX_ENDPOINT_ID="8256523084039716864"
export VERTEX_AI_REGION="europe-west4"

# --- Data Path & Audit Configuration ---
# With the new simpler GCS layout, these prefixes no longer depend on CUSTOMER_ID
export SOURCE_PREFIX="source_documents/"
export OUTPUT_PREFIX="output/"

# Set the type of audit being performed
export AUDIT_TYPE="Zertifizierungsaudit"

# --- Development & Testing Configuration ---
export TEST="true"

echo "✅ Environment variables configured successfully."
echo "You can now run the Python application."