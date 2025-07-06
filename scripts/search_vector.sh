#!/bin/bash

# 1. Define your vector as a comma-separated string of numbers.
#    This would typically be the output from an embedding model.
#    (Using a dummy 3-element vector for this example)
# VECTOR="0.123, 0.456, 0.789"

# 2. Define your endpoint details from your original command
ENDPOINT_URL="https://834988240.europe-west4-547781257801.vdb.vertexai.goog/v1/projects/547781257801/locations/europe-west4/indexEndpoints/3154929868647432192:findNeighbors"
DEPLOYED_INDEX_ID="bsi_deployed_index_kunde_x"

# 3. Construct the JSON payload.
#    Using printf is a safe way to inject the shell variable into the JSON structure.
#    Notice that `featureVector` now correctly becomes an array of numbers, not a string.
JSON_PAYLOAD=$(printf '{
  "deployedIndexId": "%s",
  "queries": [
    {
      "datapoint": {
        "featureVector": [%s]
      }
    }
  ],
  "returnFullDatapoint": false
}' "$DEPLOYED_INDEX_ID" "$VECTOR")

# 4. Execute the curl command
#    - Added "Content-Type: application/json" header, which is best practice.
#    - The payload is passed as a variable to avoid quoting issues.
curl -X POST \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  "$ENDPOINT_URL" \
  -d "$JSON_PAYLOAD"
