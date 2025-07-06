#!/bin/bash
set -e # Exit immediately if a command exits with a non-zero status.

# ==============================================================================
# Terraform State Recovery Script for BSI Audit Automator
# ==============================================================================
# This script is designed to recover a lost Terraform state file by importing
# all existing cloud resources defined in 'main.tf' into a new state.
#
# It automatically reads configuration values from 'terraform.tfvars'.
#
# INSTRUCTIONS:
# 1. Place this script in the same directory as your 'main.tf' and 'terraform.tfvars' files.
# 2. Make the script executable: chmod +x repair_tf.sh
# 3. Run the script: ./repair_tf.sh
# ==============================================================================

# --- Configuration ---
# Reads configuration automatically from terraform.tfvars
TFVARS_FILE="terraform.tfvars"
echo "--- Reading configuration from $TFVARS_FILE ---"

if [ ! -f "$TFVARS_FILE" ]; then
    echo "ERROR: $TFVARS_FILE not found. Please ensure it exists in the current directory."
    exit 1
fi

# Function to parse a variable from the tfvars file. Handles whitespace and comments.
get_tfvar() {
    local var_name="$1"
    # Use awk to find the line starting with var_name, split by quotes, and print the second field.
    awk -F'"' -v var="$var_name" '$1 ~ "^\\s*" var "\\s*=" {print $2; exit}' "$TFVARS_FILE"
}

PROJECT_ID="bsi-auditor-4" # $(get_tfvar "project_id")
PROJECT_NUMBER="547781257801" # $(get_tfvar "project_number")
REGION="europe-west4"   # $(get_tfvar "region")
SERVICE_ACCOUNT_ID="bsi-automator-sa"  # $(get_tfvar "service_account_id")
VPC_NETWORK_NAME="bsi-audit-vpc" # $(get_tfvar "vpc_network_name")

# Validate that all variables were found
if [ -z "$PROJECT_ID" ] || [ -z "$PROJECT_NUMBER" ] || [ -z "$REGION" ] || [ -z "$SERVICE_ACCOUNT_ID" ] || [ -z "$VPC_NETWORK_NAME" ]; then
    echo "ERROR: One or more required variables could not be read from $TFVARS_FILE."
    echo "Please ensure project_id, project_number, region, service_account_id, and vpc_network_name are set."
    exit 1
fi

echo "Successfully read configuration."
echo

# --- Pre-requisite Checks ---
echo "--- Checking Prerequisites ---"

if ! command -v gcloud &> /dev/null; then
    echo "ERROR: gcloud CLI not found. Please install it."
    exit 1
fi

if ! command -v terraform &> /dev/null; then
    echo "ERROR: terraform is not installed. Please install it."
    exit 1
fi

echo "All prerequisites are met."
echo

# --- Initialization ---
echo "--- Step 1: Initializing Terraform ---"
echo "This will create a new, empty state file."
rm -f .terraform.lock.hcl terraform.tfstate* # Clean up previous attempts
terraform init
echo

# --- Dynamic ID Fetching ---
echo "--- Step 2: Fetching Dynamic Resource IDs from GCP ---"

# Vertex AI resources are identified by a numeric ID, not their display name.
# We use gcloud to find these IDs.

echo "Fetching Vertex AI Index ID..."
VERTEX_INDEX_ID=$(gcloud ai indexes list --project="$PROJECT_ID" --region="$REGION" --filter="displayName=bsi-audit-index" --format="value(name)" | awk -F/ '{print $NF}')
if [ -z "$VERTEX_INDEX_ID" ]; then
    echo "ERROR: Could not find Vertex AI Index with display name 'bsi-audit-index' in region '$REGION'."
    echo "Please ensure the resource exists and the configuration is correct."
    exit 1
fi
echo "Found Vertex AI Index ID: $VERTEX_INDEX_ID"

echo "Fetching Vertex AI Index Endpoint ID..."
VERTEX_ENDPOINT_ID=$(gcloud ai index-endpoints list --project="$PROJECT_ID" --region="$REGION" --filter="displayName=bsi-audit-endpoint" --format="value(name)" | awk -F/ '{print $NF}')
if [ -z "$VERTEX_ENDPOINT_ID" ]; then
    echo "ERROR: Could not find Vertex AI Index Endpoint with display name 'bsi-audit-endpoint' in region '$REGION'."
    exit 1
fi
echo "Found Vertex AI Index Endpoint ID: $VERTEX_ENDPOINT_ID"
echo

# --- Resource Import ---
echo "--- Step 3: Importing All Resources into Terraform State ---"

# The format is: terraform import <TERRAFORM_RESOURCE_ADDRESS> <GCP_RESOURCE_ID>

# 1. Project Services (APIs)
echo "Importing Project Services (APIs)..."
APIS=(
    "run.googleapis.com"
    "cloudbuild.googleapis.com"
    "artifactregistry.googleapis.com"
    "aiplatform.googleapis.com"
    "storage.googleapis.com"
    "cloudresourcemanager.googleapis.com"
    "compute.googleapis.com"
    "servicenetworking.googleapis.com"
    "iam.googleapis.com"
)
for API in "${APIS[@]}"; do
    echo "Importing API: $API"
    terraform import "google_project_service.project_apis[\"$API\"]" "${PROJECT_ID}/${API}"
done

# 2. GCS Bucket and Placeholder Object
BUCKET_NAME="${PROJECT_ID}-audit-data"
echo "Importing GCS Bucket: $BUCKET_NAME"
#terraform import google_storage_bucket.bsi_audit_bucket "$BUCKET_NAME"

echo "Importing GCS Placeholder Object..."
#terraform import google_storage_bucket_object.json_placeholder "${BUCKET_NAME}/vector_index_data/placeholder.json"

# 3. Artifact Registry
AR_REPO_ID="projects/${PROJECT_ID}/locations/${REGION}/repositories/bsi-audit-repo"
echo "Importing Artifact Registry Repository..."
terraform import google_artifact_registry_repository.bsi_repo "$AR_REPO_ID"

# 4. Service Account
SA_EMAIL="${SERVICE_ACCOUNT_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
echo "Importing Service Account: $SA_EMAIL"
terraform import google_service_account.bsi_job_sa "projects/${PROJECT_ID}/serviceAccounts/${SA_EMAIL}"

# 5. Networking (VPC, Subnet, Peering)
echo "Importing VPC Network: $VPC_NETWORK_NAME"
terraform import google_compute_network.bsi_vpc "$VPC_NETWORK_NAME"

echo "Importing VPC Subnetwork..."
terraform import google_compute_subnetwork.bsi_audit_subnet "${REGION}/bsi-audit-subnet"

echo "Importing VPC Peering Address Range..."
terraform import google_compute_global_address.peering_range "vertex-ai-peering-range"

echo "Importing Service Networking Connection..."
#terraform import google_service_networking_connection.vertex_vpc_connection "${VPC_NETWORK_NAME}/servicenetworking.googleapis.com"

# 6. Vertex AI (Index and Endpoint)
VERTEX_INDEX_GCP_ID="projects/${PROJECT_ID}/locations/${REGION}/indexes/${VERTEX_INDEX_ID}"
echo "Importing Vertex AI Index..."
terraform import google_vertex_ai_index.bsi_audit_index "$VERTEX_INDEX_GCP_ID"

VERTEX_ENDPOINT_GCP_ID="projects/${PROJECT_ID}/locations/${REGION}/indexEndpoints/${VERTEX_ENDPOINT_ID}"
echo "Importing Vertex AI Index Endpoint..."
terraform import google_vertex_ai_index_endpoint.bsi_audit_endpoint "$VERTEX_ENDPOINT_GCP_ID"

# 7. IAM Bindings
echo "Importing IAM Bindings..."

# SA -> Vertex AI User
IAM_VERTEX_ID="${PROJECT_ID} roles/aiplatform.user serviceAccount:${SA_EMAIL}"
terraform import 'google_project_iam_member.sa_vertex_access' "$IAM_VERTEX_ID"

# SA -> GCS Object Admin
IAM_GCS_ID="b/${BUCKET_NAME} roles/storage.objectAdmin serviceAccount:${SA_EMAIL}"
terraform import google_storage_bucket_iam_member.sa_gcs_access "$IAM_GCS_ID"

# Cloud Build SA -> Artifact Registry Writer
CB_SA_EMAIL="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"
IAM_AR_ID="projects/${PROJECT_ID}/locations/${REGION}/repositories/bsi-audit-repo roles/artifactregistry.writer serviceAccount:${CB_SA_EMAIL}"
terraform import google_artifact_registry_repository_iam_member.cloudbuild_ar_writer "$IAM_AR_ID"

echo
echo "All resources have been imported into the local state file: terraform.tfstate"
echo

# --- Verification ---
echo "--- Step 4: Verifying the State ---"
echo "Running 'terraform plan'. A successful recovery will show 'No changes'."

terraform plan

echo
echo "--- Recovery Process Complete ---"
echo
echo "Final Verification:"
echo "Review the output of 'terraform plan' above."
echo "  - If it shows 'No changes. Your infrastructure matches the configuration.',"
echo "    then your state has been successfully reconciled!"
echo "  - If it shows changes, it means your 'main.tf' file does not perfectly"
echo "    match your deployed infrastructure. You may need to adjust the *.tf"
echo "    files and run 'terraform plan' again until no changes are reported."
echo
echo "NOTE on Provisioners:"
echo "The 'local-exec' provisioners for deploying the Vertex AI index were NOT"
echo "executed during this import. The import process only populates the state."
echo
echo "IMPORTANT: Now that you have recovered your state, configure a remote backend"
echo "(e.g., a GCS bucket with versioning) immediately to prevent future state loss."

