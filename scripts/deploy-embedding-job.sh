#!/bin/bash
set -euo pipefail

# The name of the Cloud Run Job
JOB_NAME="bsi-etl-job"
# The Google Cloud region where the job will be deployed.
REGION="europe-west4"
# The name of your Artifact Registry repository.
# TODO: Replace with your actual repository name.
ARTIFACT_REGISTRY_REPO="your-repo-name"
# The full image name in Artifact Registry.
IMAGE_URI="${REGION}-docker.pkg.dev/${GOOGLE_CLOUD_PROJECT}/${ARTIFACT_REGISTRY_REPO}/${JOB_NAME}"

gcloud run jobs deploy "${JOB_NAME}" \
  --source . \
  --image "${IMAGE_URI}" \
  --tasks 1 \
  --max-retries 3 \
  --memory 4Gi \
  --cpu 2 \
  --region "${REGION}" \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --task-timeout "7200" \
  --command "python" \
  --args "main.py,--run-etl" \
  --service-account "$(terraform output -raw service_account_email)"
