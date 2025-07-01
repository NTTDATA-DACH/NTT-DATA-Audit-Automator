# ===================================================================
# B: EXECUTE JOB FOR A CUSTOMER (Run this for each audit)
# ===================================================================

# Set customer-specific variables here
export GCP_PROJECT_ID="bsi-audit-kunde-x"
export CUSTOMER_ID="kunde-x"
export BUCKET_NAME="bsi-audit-kunde-x-kunde-x-audit-data"
export INDEX_ENDPOINT_ID="8256523084039716864"
export VERTEX_AI_REGION="europe-west4"
export AUDIT_TYPE="Zertifizierungsaudit"

gcloud run jobs execute "bsi-etl-job" \
  --region "europe-west4" \
  --project "${GCP_PROJECT_ID}" \
  --wait \
  --update-env-vars="GCP_PROJECT_ID=${GCP_PROJECT_ID},CUSTOMER_ID=${CUSTOMER_ID},BUCKET_NAME=${BUCKET_NAME},INDEX_ENDPOINT_ID=${INDEX_ENDPOINT_ID},VERTEX_AI_REGION=${VERTEX_AI_REGION},SOURCE_PREFIX=${CUSTOMER_ID}/source_documents/,OUTPUT_PREFIX=${CUSTOMER_ID}/output/,AUDIT_TYPE=${AUDIT_TYPE},TEST=false"

echo "✅ Job execution for customer '${CUSTOMER_ID}' finished."