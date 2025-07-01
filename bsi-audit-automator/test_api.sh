# https://aiplatform.googleapis.com/v1/projects/bsi-audit-kunde-x/locations/global/models/text-embedding-004

export PROJECT_ID="bsi-audit-kunde-x"
export REGION="global"
export MODEL_ID="text-embedding-001"

export URL="https://${REGION}-aiplatform.googleapis.com/v1/projects/${PROJECT_ID}/locations/${REGION}/publishers/google/models/${MODEL_ID}:predict"

echo "URL: $URL"


curl -X POST \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  $URL -d \
  '{
    "instances": [
      ...
    ],
    "parameters": {
      ...
    }
  }'
