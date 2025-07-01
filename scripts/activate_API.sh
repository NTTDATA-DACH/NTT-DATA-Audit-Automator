#!/bin/bash
#
# This script enables all the necessary Google Cloud APIs for the
# BSI Audit Automator project to function correctly.
#
# USAGE:
# 1. Make sure you are logged into gcloud: `gcloud auth login`
# 2. Set your default project: `gcloud config set project YOUR_PROJECT_ID`
# 3. Make the script executable: `chmod +x enable_apis.sh`
# 4. Run the script: `./enable_apis.sh`
#

# Exit immediately if a command exits with a non-zero status.
set -e

# Get the project ID from the current gcloud configuration.
PROJECT_ID=$(gcloud config get-value project)

if [[ -z "$PROJECT_ID" ]]; then
    echo "GCP project ID is not set."
    echo "Please run 'gcloud config set project YOUR_PROJECT_ID' and try again."
    exit 1
fi

echo "Enabling required APIs for project: $PROJECT_ID"
echo "This may take a few minutes..."

# A list of all APIs required for the project.
APIS_TO_ENABLE=(
  # For running our application as a containerized job
  "run.googleapis.com"

  # For building the container image from source using `gcloud run jobs deploy --source`
  "cloudbuild.googleapis.com"

  # For storing the built container images
  "artifactregistry.googleapis.com"

  # For all AI/ML operations: embeddings, generation, and Vector Search
  "aiplatform.googleapis.com"

  # For reading and writing all data from/to GCS buckets
  "storage.googleapis.com"

  # Good practice: Often required for services to manage resources
  "cloudresourcemanager.googleapis.com"
)

# Loop through the array and enable each API.
for API in "${APIS_TO_ENABLE[@]}"; do
  echo "Enabling $API..."
  gcloud services enable "$API" --project="$PROJECT_ID"
done

echo ""
echo "✅ All required APIs have been successfully enabled for project '$PROJECT_ID'."