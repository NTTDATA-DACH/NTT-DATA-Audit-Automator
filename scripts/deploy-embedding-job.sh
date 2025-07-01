# ===================================================================
# A: DEPLOY JOB BLUEPRINT (Run this once, or when code changes)
# ===================================================================

gcloud run jobs deploy "bsi-etl-job" \
  --source . \
  --tasks 1 \
  --max-retries 3 \
  --memory 4Gi \
  --cpu 2 \
  --region "europe-west4" \
  --project "bsi-audit-kunde-x" \
  --task-timeout "7200" \
  --command "python" \
  --args "main.py,--run-etl"

echo "✅ Job 'bsi-etl-job' has been deployed."