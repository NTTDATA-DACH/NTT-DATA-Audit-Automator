# Replace [YOUR_PROJECT_NUMBER] with the actual number from Step 1
export PROJECT_NUMBER=`gcloud projects describe bsi-audit-2 --format="value(projectNumber)"`
export PROJECT_ID="bsi-audit-2"

# Grant the Cloud Build service account permission to write to Artifact Registry
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
    --role="roles/artifactregistry.writer"

echo "✅ IAM permission granted."