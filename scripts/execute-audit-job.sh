#!/bin/bash
set -euo pipefail

# ===================================================================
# INTERACTIVE SCRIPT TO EXECUTE THE BSI AUDIT AUTOMATOR CLOUD RUN JOB
# ===================================================================
# This script interactively prompts for the audit type and task, then
# triggers the corresponding Google Cloud Run job with the correct
# environment variables and arguments.
#
# PREREQUISITES:
#   - You must have run 'terraform apply' in the ../terraform directory.
#   - You must have the 'gcloud' and 'terraform' CLIs installed and authenticated.
#
# USAGE:
#   Run this from the project root directory ('bsi-audit-automator'):
#      ./run_cloud_job.sh
# ===================================================================

# --- Script Usage ---
usage() {
  echo "Usage: $0"
  echo "Interactively selects and executes a BSI audit task as a Cloud Run job."
  echo "Must be run from the project root ('bsi-audit-automator')."
  exit 1
}

# --- Argument Validation ---
if [[ $# -ne 0 ]]; then
  usage
fi

# --- Configuration for the Cloud Run Job ---
# Set to "false" for a production-like run. Set to "true" to process fewer files/items.
TEST="false"
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
REGION="$(terraform -chdir=${TERRAFORM_DIR} output -raw region)"
BUCKET_NAME="$(terraform -chdir=${TERRAFORM_DIR} output -raw gcs_bucket_name)"
DOC_AI_PROCESSOR_NAME="$(terraform -chdir=${TERRAFORM_DIR} output -raw documentai_processor_name)"
JOB_NAME="bsi-audit-automator-job" # The name of the Cloud Run Job resource

echo "   - GCP Project: ${GCP_PROJECT_ID}"
echo "   - Region: ${REGION}"
echo "   - GCS Bucket: ${BUCKET_NAME}"
echo "   - DocAI Processor: retrieved"
echo ""

# --- INTERACTIVE SELECTION: Audit Type ---
echo "🔹 Please select the audit type for this run."
audit_types=("Zertifizierungsaudit" "1. Überwachungsaudit" "2. Überwachungsaudit")
PS3="Select audit type number: "
select AUDIT_TYPE in "${audit_types[@]}"; do
  if [[ -n "$AUDIT_TYPE" ]]; then
    echo "   Selected audit type: $AUDIT_TYPE"
    echo ""
    break
  else
    echo "   Invalid selection. Try again."
  fi
done

# --- INTERACTIVE SELECTION: Task/Stage ---
echo "🔹 Please select the task to execute."
tasks=(
    "Run Grundschutz-Check Extraction (Idempotent Prerequisite)"
    "Scan Previous Audit Report"
    "Run Single Audit Stage"
    "Run All Audit Stages (Full Pipeline)"
    "Generate Final Report"
    "Quit"
)
PS3="Select task number: "
main_arg=""
force_arg=""

select task in "${tasks[@]}"; do
  case $task in
    "Scan Previous Audit Report")
      main_arg="--scan-previous-report"
      read -p "   Force re-run? (Overwrites existing scanned data) [y/N]: " force_choice
      if [[ "$force_choice" =~ ^[Yy]$ ]]; then
          force_arg=",--force"
      fi
      break
      ;;
    "Run Grundschutz-Check Extraction (Idempotent Prerequisite)")
      main_arg="--run-gs-check-extraction"
      read -p "   Force re-run? (Overwrites existing extracted data) [y/N]: " force_choice
      if [[ "$force_choice" =~ ^[Yy]$ ]]; then
          force_arg=",--force"
      fi
      break
      ;;
    "Run Single Audit Stage")
      echo "   🔹 Please select the stage to run."
      stages=("Chapter-1" "Chapter-3" "Chapter-4" "Chapter-5" "Chapter-7")
      PS3_STAGE="   Select stage number: "
      select STAGE_NAME in "${stages[@]}"; do
        if [[ -n "$STAGE_NAME" ]]; then
          main_arg="--run-stage,${STAGE_NAME}"
          break
        else
          echo "   Invalid stage selection. Try again."
        fi
      done
      read -p "   Force re-run? (Overwrites existing stage results) [y/N]: " force_choice
      if [[ "$force_choice" =~ ^[Yy]$ ]]; then
          force_arg=",--force"
      fi
      break
      ;;
    "Run All Audit Stages (Full Pipeline)")
      main_arg="--run-all-stages"
      read -p "   Force re-run? (Overwrites all existing stage results) [y/N]: " force_choice
       if [[ "$force_choice" =~ ^[Yy]$ ]]; then
          force_arg=",--force"
      fi
      break
      ;;
    "Generate Final Report")
      main_arg="--generate-report"
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

# The --args flag on gcloud run jobs execute wants a single comma-separated string.
# We build it by concatenating the main argument and the optional force flag.
FULL_TASK_ARGS="${main_arg}${force_arg}"

# --- Final gcloud Execution ---
echo ""
echo "🚀 Preparing to execute Cloud Run job '${JOB_NAME}'..."
echo "   With args: [${FULL_TASK_ARGS//,/ }]"
echo ""

# The '--args' flag on 'gcloud run jobs execute' overrides the default command arguments.
# A comma is used as the delimiter for arguments.
gcloud run jobs execute "${JOB_NAME}" \
  --region "${REGION}" \
  --project "${GCP_PROJECT_ID}" \
  --wait \
  --update-env-vars="GCP_PROJECT_ID=${GCP_PROJECT_ID},BUCKET_NAME=${BUCKET_NAME},REGION=${REGION},SOURCE_PREFIX=source_documents/,OUTPUT_PREFIX=output/,AUDIT_TYPE=${AUDIT_TYPE},TEST=${TEST},MAX_CONCURRENT_AI_REQUESTS=${MAX_CONCURRENT_AI_REQUESTS},DOC_AI_PROCESSOR_NAME=${DOC_AI_PROCESSOR_NAME}" \
  --args="${FULL_TASK_ARGS}"

echo ""
echo "✅ Job execution finished."