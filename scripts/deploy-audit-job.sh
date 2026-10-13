#!/bin/bash
set -euo pipefail

# --- Locate repo root (this script lives in <root>/scripts), so it works from any directory ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_DIR="${ROOT_DIR}/audit-automator"   # Docker build context: holds the Dockerfile
TERRAFORM_DIR="${ROOT_DIR}/terraform"
if [ ! -f "${TERRAFORM_DIR}/terraform.tfstate" ]; then
  echo "❌ Error: no Terraform state in '${TERRAFORM_DIR}'. Run 'terraform apply' there first."
  exit 1
fi

# --- Configuration from Terraform ---
echo "🔹 Fetching infrastructure details from Terraform..."
JOB_NAME="bsi-audit-automator-job"
REGION="$(terraform -chdir=${TERRAFORM_DIR} output -raw region)"
PROJECT_ID="$(terraform -chdir=${TERRAFORM_DIR} output -raw project_id)"
ARTIFACT_REGISTRY_REPO="$(terraform -chdir=${TERRAFORM_DIR} output -raw artifact_registry_repository_name)"
SERVICE_ACCOUNT="$(terraform -chdir=${TERRAFORM_DIR} output -raw service_account_email)"
VPC_NETWORK_NAME="$(terraform -chdir=${TERRAFORM_DIR} output -raw vpc_network_name)"
SUBNET_NAME="$(terraform -chdir=${TERRAFORM_DIR} output -raw subnet_name)"
IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REGISTRY_REPO}/${JOB_NAME}"

# --- 1. Build and Push Container Image ---
echo "🚀 Building and pushing container image to Artifact Registry..."
echo "Image URI: ${IMAGE_URI}"
gcloud builds submit "${APP_DIR}" --tag "${IMAGE_URI}" --project "${PROJECT_ID}"

# --- 2. Delete Existing Job (for a clean deployment) ---
echo "🗑️  Checking for and deleting existing job to ensure a clean deployment..."
if gcloud run jobs describe "${JOB_NAME}" --region "${REGION}" --project "${PROJECT_ID}" &> /dev/null; then
  gcloud run jobs delete "${JOB_NAME}" \
    --region "${REGION}" \
    --project "${PROJECT_ID}" \
    --quiet
  echo "✅ Existing job '${JOB_NAME}' deleted."
else
  echo "ℹ️ No existing job found to delete. Proceeding."
fi

# --- 3. Deploy New Cloud Run Job ---
echo "📦 Deploying new Cloud Run Job '${JOB_NAME}'..."
gcloud run jobs deploy "${JOB_NAME}" \
  --image "${IMAGE_URI}" \
  --tasks 1 \
  --max-retries 1 \
  --memory 8Gi \
  --cpu 2 \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --task-timeout "7200" \
  --service-account "${SERVICE_ACCOUNT}" \
  --network "${VPC_NETWORK_NAME}" \
  --subnet "${SUBNET_NAME}" \
  --vpc-egress "all-traffic"

echo "✅ Deployment complete."