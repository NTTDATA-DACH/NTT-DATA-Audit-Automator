#!/bin/bash
set -euo pipefail

# ===================================================================
# INTERACTIVE SCRIPT TO EXECUTE ANY BSI AUDIT TASK
# ===================================================================
# NOTE: This script ASSUMES it is being run from the project root directory.

# --- Script Usage ---
usage() {
  echo "Usage: $0"
  echo "Interactively selects and executes a BSI audit task. Must be run"
  echo "from the project root ('bsi-audit-automator')."
  exit 1
}

# --- Argument Validation ---
if [[ $# -ne 0 ]]; then
  usage
fi

# --- Configuration ---
# Set to "true" to run in test mode (processes fewer files/items)
TEST_MODE="false"
MAX_CONCURRENT_AI_REQUESTS=5

# --- Dynamic Values from Terraform ---
echo "🔹 Fetching infrastructure details from Terraform..."
TERRAFORM_DIR="../terraform"
if [ ! -d "$TERRAFORM_DIR" ]; then
    echo "❌ Error: Terraform directory not found at '$TERRAFORM_DIR'."
    echo "   Please run this script from the project root ('bsi-audit-automator')."
    exit 1
fi
GCP_PROJECT_ID="$(terraform -chdir=${TERRAFORM_DIR} output -raw project_id)"
VERTEX_AI_REGION="$(terraform -chdir=${TERRAFORM_DIR} output -raw region)"
BUCKET_NAME="$(terraform -chdir=${TERRAFORM_DIR} output -raw vector_index_data_gcs_path | cut -d'/' -f3)"

# --- INTERACTIVE SELECTION: Audit Type ---
echo "🔹 Please select the audit type."
audit_types=("Zertifizierungsaudit" "1. Überwachungsaudit" "2. Überwachungsaudit")
PS3="Select audit type number: "
select AUDIT_TYPE in "${audit_types[@]}"; do
  if [[ -n "$AUDIT_TYPE" ]]; then
    echo "Selected audit type: $AUDIT_TYPE"
    break
  else
    echo "Invalid selection. Try again."
  fi
done

# --- INTERACTIVE SELECTION: Task/Stage ---
echo "🔹 Please select the task to execute."
tasks=("Run Grundschutz-Check Extraction (Prerequisite)" "Scan Previous Audit Report" "Run Single Audit Stage" "Run All Audit Stages" "Generate Final Report" "Quit")
PS3="Select task number: "
declare TASK_ARGS=""
declare FORCE_FLAG=""

select task in "${tasks[@]}"; do
  case $task in
    "Scan Previous Audit Report")
      TASK_ARGS="--run-stage,Scan-Report"
      FORCE_FLAG=",--force" # Scanning should always be forced to get latest
      break
      ;;
    "Run Grundschutz-Check Extraction (Prerequisite)")
      TASK_ARGS="--run-gs-check-extraction"
      read -p "Force re-run? (Overwrites existing extracted data) [y/N]: " force_choice
      if [[ "$force_choice" =~ ^[Yy]$ ]]; then
          FORCE_FLAG=",--force"
      fi
      break
      ;;
    "Run Single Audit Stage")
      echo "🔹 Please select the stage to run."
      stages=("Chapter-1" "Chapter-3" "Chapter-4" "Chapter-5" "Chapter-7")
      PS3_STAGE="Select stage number: "
      select STAGE_NAME in "${stages[@]}"; do
        if [[ -n "$STAGE_NAME" ]]; then
          TASK_ARGS="--run-stage,${STAGE_NAME}"
          break
        else
          echo "Invalid stage selection. Try again."
        fi
      done
      # Single stage runs are forced by default in main.py
      # read -p "Force re-run? (Re-classifies documents & overwrites stage results) [y/N]: " force_choice
      # if [[ "$force_choice" =~ ^[Yy]$ ]]; then
      #     FORCE_FLAG=",--force"
      # fi
      break
      ;;
    "Run All Audit Stages")
      TASK_ARGS="--run-all-stages"
      read -p "Force re-run? (Re-classifies documents & overwrites all stage results) [y/N]: " force_choice
       if [[ "$force_choice" =~ ^[Yy]$ ]]; then
          FORCE_FLAG=",--force"
      fi
      break
      ;;
    "Generate Final Report")
      TASK_ARGS="--generate-report"
      break
      ;;
    "Quit")
      echo "Exiting."
      exit 0
      ;;
    *)
      echo "Invalid option $REPLY. Please try again."
      ;;
  esac
done

# Combine the main task arguments with the force flag
FULL_TASK_ARGS="${TASK_ARGS}${FORCE_FLAG}"

# --- Final gcloud Execution ---
# Using //,/ / to replace commas with spaces for a more readable display.
echo "🚀 Executing task with args: [main.py ${FULL_TASK_ARGS//,/ }]"

# The '--args' flag on 'gcloud run jobs execute' overrides the default command arguments.
gcloud run jobs execute "bsi-audit-automator-job" \
  --region "${VERTEX_AI_REGION}" \
  --project "${GCP_PROJECT_ID}" \
  --wait \
  --update-env-vars="GCP_PROJECT_ID=${GCP_PROJECT_ID},BUCKET_NAME=${BUCKET_NAME},VERTEX_AI_REGION=${VERTEX_AI_REGION},SOURCE_PREFIX=source_documents/,OUTPUT_PREFIX=output/,AUDIT_TYPE=${AUDIT_TYPE},TEST=${TEST_MODE},MAX_CONCURRENT_AI_REQUESTS=${MAX_CONCURRENT_AI_REQUESTS}" \
  --args="${FULL_TASK_ARGS}"

echo "✅ Job execution finished."