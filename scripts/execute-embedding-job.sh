# Define all necessary variables for the job
export GCP_PROJECT_ID="bsi-audit-kunde-x"
export CUSTOMER_ID="kunde-x"
export BUCKET_NAME="bsi-audit-kunde-x-kunde-x-audit-data"
export INDEX_ENDPOINT_ID="8256523084039716864"
export VERTEX_AI_REGION="europe-west4"
export AUDIT_TYPE="Zertifizierungsaudit"

# Construct the full image URI
export IMAGE_URI="europe-west4-docker.pkg.dev/${GCP_PROJECT_ID}/bsi-audit-repo/bsi-automator:latest"

# Execute the job
gcloud run jobs execute "bsi-etl-job-${CUSTOMER_ID}" \
  --image "${IMAGE_URI}" \
  --region "${VERTEX_AI_REGION}" \
  --task-timeout "3600" \
  --set-env-vars="GCP_PROJECT_ID=${GCP_PROJECT_ID},CUSTOMER_ID=${CUSTOMER_ID},BUCKET_NAME=${BUCKET_NAME},INDEX_ENDPOINT_ID=${INDEX_ENDPOINT_ID},VERTEX_AI_REGION=${VERTEX_AI_REGION},SOURCE_PREFIX=${CUSTOMER_ID}/source_documents/,OUTPUT_PREFIX=${CUSTOMER_ID}/output/,AUDIT_TYPE=${AUDIT_TYPE},TEST=false" \
  --command "python" \
  --args "main.py,--run-etl"

echo "Cloud Run Job started. Monitor its progress in the Google Cloud Console."