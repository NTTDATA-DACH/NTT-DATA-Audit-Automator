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
# To get the bucket name, run: terraform output -raw bsi_audit_bucket_name
export BUCKET_NAME="" # e.g., bsi-audit-kunde-x-kunde-x-audit-data

# To get the index endpoint ID, run: terraform output -raw vertex_ai_index_endpoint_id
export INDEX_ENDPOINT_ID="8256523084039716864" # e.g., 8256523084039716864

# The region where you deployed your Terraform resources
export VERTEX_AI_REGION="europe-west4"

# --- Data Path & Audit Configuration ---
# These are constructed automatically from your CUSTOMER_ID
export SOURCE_PREFIX="${CUSTOMER_ID}/source_documents/"
export OUTPUT_PREFIX="${CUSTOMER_ID}/output/"

# Set the type of audit being performed
export AUDIT_TYPE="Zertifizierungsaudit"

# --- Development & Testing Configuration ---
# Set to "true" for development to limit data processing and get verbose logs.
# Set to "false" for production runs.
export TEST="true"

echo "✅ Environment variables configured successfully."
echo "You can now run the Python application."