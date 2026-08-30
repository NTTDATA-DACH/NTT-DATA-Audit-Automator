#!/bin/bash
set -euo pipefail

# ===================================================================
# RESET THE AUDIT DATA IN GCS
# ===================================================================
#
# WHAT IT DOES:
# Deletes the pipeline's generated data so the next run starts clean:
#   - output/          all intermediate files, stage results, findings, reports
#   - source_documents/  ONLY with --with-sources (new customer / new document set)
#
# The infrastructure is left untouched — nothing here needs a `terraform apply`
# afterwards. Use this both for a fresh document set (formerly "refresh") and for a
# new customer (formerly "reset"): the difference is just --with-sources.
#
# PREREQUISITES:
#   - 'gsutil' authenticated; 'terraform' only if BUCKET_NAME is not set.
#   - Run from the project root, or set BUCKET_NAME yourself.
#
# USAGE:
#   bash ./scripts/reset_audit_data.sh [--with-sources] [-y]

WITH_SOURCES=false
ASSUME_YES=false
for arg in "$@"; do
  case "$arg" in
    --with-sources) WITH_SOURCES=true ;;
    -y|--yes) ASSUME_YES=true ;;
    *) echo "❌ Unknown argument: $arg"; echo "Usage: $0 [--with-sources] [-y]"; exit 1 ;;
  esac
done

# --- Resolve the bucket: explicit env wins, Terraform output is the fallback ---
TERRAFORM_DIR="terraform"
if [ -z "${BUCKET_NAME:-}" ]; then
  if [ ! -d "$TERRAFORM_DIR" ]; then
    echo "❌ Error: BUCKET_NAME is not set and '$TERRAFORM_DIR/' was not found."
    echo "   Run this from the project root, or: BUCKET_NAME=my-bucket $0"
    exit 1
  fi
  echo "🔹 Reading the bucket name from the Terraform state..."
  BUCKET_NAME="$(terraform -chdir=${TERRAFORM_DIR} output -raw gcs_bucket_name)"
fi

echo "🚨 WARNING: this deletes audit data in gs://${BUCKET_NAME}/ 🚨"
echo "-----------------------------------------------------"
echo "  1. DELETE gs://${BUCKET_NAME}/output/*   (results, findings, reports, intermediates)"
if [ "$WITH_SOURCES" = true ]; then
  echo "  2. DELETE gs://${BUCKET_NAME}/source_documents/*   (the customer's documents)"
else
  echo "  2. KEEP   gs://${BUCKET_NAME}/source_documents/   (pass --with-sources to delete)"
fi
echo "-----------------------------------------------------"

if [ "$ASSUME_YES" != true ]; then
  read -p "Proceed? (y/n) " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted by user."
    exit 1
  fi
fi

# `|| true`: gsutil exits non-zero when a prefix is already empty, which is not an error here.
echo "🗑️  Deleting output/ ..."
gsutil -m rm -r "gs://${BUCKET_NAME}/output/*" || true
if [ "$WITH_SOURCES" = true ]; then
  echo "🗑️  Deleting source_documents/ ..."
  gsutil -m rm -r "gs://${BUCKET_NAME}/source_documents/*" || true
fi

echo ""
echo "✅ Reset complete — the infrastructure is unchanged."
echo "   Next steps:"
if [ "$WITH_SOURCES" = true ]; then
  echo "   1. Upload the new documents to gs://${BUCKET_NAME}/source_documents/"
  echo "   2. Start the pipeline: auditor --run-gs-check-extraction, then --run-all-stages"
else
  echo "   1. Start the pipeline: auditor --run-gs-check-extraction, then --run-all-stages"
fi
