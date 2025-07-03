#!/bin/bash
set -euo pipefail

# --- Configuration from Terraform ---
echo "🔹 Fetching infrastructure details from Terraform..."
TERRAFORM_DIR="../terraform"
JOB_NAME="bsi-audit-automator-job"
REGION="$(terraform -chdir=${TERRAFORM_DIR} output -raw region)"
PROJECT_ID="$(terraform -chdir=${TERRAFORM_DIR} output -raw project_id)"
ARTIFACT_REGISTRY_REPO="$(terraform -chdir=${TERRAFORM_DIR} output -raw artifact_registry_repository_name)"
SERVICE_ACCOUNT="$(terraform -chdir=${TERRAFORM_DIR} output -raw service_account_email)"
IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REGISTRY_REPO}/${JOB_NAME}"

# --- 1. Build and Push Container Image ---
echo "🚀 Building and pushing container image to Artifact Registry..."
echo "Image URI: ${IMAGE_URI}"
gcloud builds submit . --tag "${IMAGE_URI}" --project "${PROJECT_ID}"

# --- 2. Deploy Cloud Run Job ---
echo "📦 Deploying Cloud Run Job '${JOB_NAME}'..."
gcloud run jobs deploy "${JOB_NAME}" \
  --image "${IMAGE_URI}" \
  --tasks 1 \
  --max-retries 3 \
  --memory 4Gi \
  --cpu 2 \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --task-timeout "7200" \
  --command "python" \
  --args "main.py" \
  --args "--help" \
  --service-account "${SERVICE_ACCOUNT}"

echo "✅ Deployment complete."