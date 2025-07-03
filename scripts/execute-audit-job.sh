#!/bin/bash
set -euo pipefail

# ===================================================================
# INTERACTIVE SCRIPT TO EXECUTE ANY BSI AUDIT TASK
# ===================================================================
# NOTE: This script ASSUMES it is being run from the project root directory.

# --- Script Usage ---
usage() {
  echo "Usage: $0"
  echo "Interactively selects and executes a BSI audit task for the customer"
  echo "defined in the Terraform configuration. Must be run from the project root."
  exit 1
}

# --- Argument Validation ---
if [[ $# -ne 0 ]]; then
  usage
fi
TEST_MODE="false"
MAX_CONCURRENT_AI_REQUESTS=5

# --- Dynamic Values from Terraform ---
echo "🔹 Fetching infrastructure details from Terraform..."
TERRAFORM_DIR="../terraform"
GCP_PROJECT_ID="$(terraform -chdir=${TERRAFORM_DIR} output -raw project_id)"
VERTEX_AI_REGION="$(terraform -chdir=${TERRAFORM_DIR} output -raw region)"
BUCKET_NAME="$(terraform -chdir=${TERRAFORM_DIR} output -raw vector_index_data_gcs_path | cut -d'/' -f3)"
INDEX_ENDPOINT_ID_FULL="$(terraform -chdir=${TERRAFORM_DIR} output -raw vertex_ai_index_endpoint_id)"
# This is the line that was causing the error, it has been removed.
# INDEX_PUBLIC_DOMAIN="$(terraform -chdir=${TERRAFORM_DIR} output -raw vertex_ai_index_endpoint_public_domain)"
INDEX_ENDPOINT_ID="$(basename "${INDEX_ENDPOINT_ID_FULL}")"

# --- INTERACTIVE SELECTION: Audit Type ---
echo "🔹 Please select the audit type."
audit_types=("Zertifizierungsaudit" "Überwachungsaudit")
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
tasks=("Run ETL (Embedding)" "Run Single Audit Stage" "Run All Audit Stages" "Generate Final Report" "Quit")
PS3="Select task number: "
declare TASK_ARGS # This will hold the arguments for main.py

select task in "${tasks[@]}"; do
  case $task in
    "Run ETL (Embedding)")
      TASK_ARGS="--run-etl"
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
      break
      ;;
    "Run All Audit Stages")
      TASK_ARGS="--run-all-stages"
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

# --- Final gcloud Execution ---
echo "🚀 Executing task with args: [main.py ${TASK_ARGS}]"

# NOTE: The '--args' flag on 'gcloud run jobs execute' overrides the default
# command arguments of the deployed job, allowing us to run any task.
# The reference to INDEX_PUBLIC_DOMAIN has been removed from the env vars list.
gcloud run jobs execute "bsi-audit-automator-job" \
  --region "${VERTEX_AI_REGION}" \
  --project "${GCP_PROJECT_ID}" \
  --update-env-vars="GCP_PROJECT_ID=${GCP_PROJECT_ID},BUCKET_NAME=${BUCKET_NAME},INDEX_ENDPOINT_ID=${INDEX_ENDPOINT_ID},VERTEX_AI_REGION=${VERTEX_AI_REGION},SOURCE_PREFIX=source_documents/,OUTPUT_PREFIX=output/,ETL_STATUS_PREFIX=output/etl_status/,AUDIT_TYPE=${AUDIT_TYPE},TEST=${TEST_MODE},MAX_CONCURRENT_AI_REQUESTS=${MAX_CONCURRENT_AI_REQUESTS}" \
  --args="${TASK_ARGS}"

echo "✅ Job execution' finished."