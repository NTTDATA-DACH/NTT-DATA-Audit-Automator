#!/bin/bash
#
# This script enables all the necessary Google Cloud APIs for the
# BSI Grundschutz Audit Automation project.
#

# --- Configuration ---
# Set your Google Cloud Project ID here.
PROJECT_ID="bsi-audit-kunde-x"

# --- Main Script ---

if [ -z "$PROJECT_ID" ]; then
  echo "Error: PROJECT_ID variable is not set. Please edit the script."
  exit 1
fi

echo "Setting the active project to: $PROJECT_ID"
gcloud config set project "$PROJECT_ID"

# List of APIs to enable for the project.
SERVICES_TO_ENABLE=(
  "aiplatform.googleapis.com"        # Vertex AI (Models, Index, Endpoint)
  "compute.googleapis.com"         # Compute Engine (for VPC)
  "servicenetworking.googleapis.com" # Service Networking (for VPC Peering)
  "storage.googleapis.com"         # Cloud Storage (for all data I/O)
  "cloudresourcemanager.googleapis.com" # Cloud Resource Manager (for project access)
)

echo ""
echo "Enabling necessary APIs for project: $PROJECT_ID..."
echo "This may take a few minutes."
echo "----------------------------------------------------"

for SERVICE in "${SERVICES_TO_ENABLE[@]}"; do
  echo "Enabling $SERVICE..."
  gcloud services enable "$SERVICE" --project="$PROJECT_ID"
  if [ $? -ne 0 ]; then
    echo "Error: Failed to enable $SERVICE. Please check permissions and try again."
    exit 1
  fi
done

echo "----------------------------------------------------"
echo "All required APIs have been enabled successfully."
echo ""
gcloud services list --project="$PROJECT_ID" --enabled | grep -E "aiplatform|compute|servicenetworking|storage|cloudresourcemanager"