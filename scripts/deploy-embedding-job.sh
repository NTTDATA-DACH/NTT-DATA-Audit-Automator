# Set your GCP Project ID and a name for your repository
export GCP_PROJECT_ID="bsi-audit-kunde-x"
export AR_REPO_NAME="bsi-audit-repo" # A name for your container repository

# Optional: Create the Artifact Registry repository if it doesn't exist
gcloud artifacts repositories create "${AR_REPO_NAME}" \
    --repository-format=docker \
    --location=europe-west4 \
    --description="Repository for BSI Audit Automator images"

# Build the image using Cloud Build and tag it
gcloud builds submit . --tag "europe-west4-docker.pkg.dev/${GCP_PROJECT_ID}/${AR_REPO_NAME}/bsi-automator:latest"

echo "Image build complete."