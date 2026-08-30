#!/usr/bin/env bash
#
# End-to-end TEST harness for the BSI Audit Automator.
#
# Provisions a throwaway environment and runs the full pipeline against mock
# documents, so you can smoke-test the whole flow without Terraform:
#   1. enable required APIs
#   2. create a GCS bucket (idempotent)
#   3. create or reuse a Document AI Layout Parser processor (idempotent)
#   4. generate + upload mock source documents
#   5. export the required env vars
#   6. run: gs-check-extraction -> all stages -> generate-report
#
# ⚠️  This creates REAL Google Cloud resources and makes REAL Vertex AI +
#     Document AI calls, which COST money. It runs in TEST mode (smaller batches)
#     but is not free. Clean up afterwards (see the notes printed at the end).
#
# PREREQUISITES:
#   - gcloud + gsutil installed and authenticated, with an active project owner/editor
#   - Application Default Credentials:  gcloud auth application-default login
#   - Python deps installed:            pip install -r audit-automator/requirements.txt
#
# USAGE:
#   scripts/test_full_run.sh [-y] [--skip-run] [--report-only]
#     -y             skip the confirmation prompt
#     --skip-run     provision + upload mocks only; do not run the pipeline
#     --report-only  provision + upload, then run ONLY --generate-report (no AI/DocAI cost)
#
# CONFIG (override via environment before running):
#   GCP_PROJECT_ID   (default: $GOOGLE_CLOUD_PROJECT, else `gcloud config get-value project`)
#   REGION           (default: europe-west3)         GCS / general region
#   DOCAI_LOCATION   (default: eu)                   Document AI location (eu|us)
#   BUCKET_NAME      (default: <project>-audit-test) globally-unique bucket name
#   AUDIT_TYPE       (default: "2. Überwachungsaudit")
#
set -euo pipefail

say() { echo -e "\n🔹 $*"; }
die() { echo -e "\n❌ $*" >&2; exit 1; }

# --- Locate repo root (this script lives in <root>/scripts) ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_DIR="${ROOT_DIR}/audit-automator"

# --- Resolve project id: explicit env -> Cloud Shell's GOOGLE_CLOUD_PROJECT -> gcloud config ---
GCP_PROJECT_ID="${GCP_PROJECT_ID:-${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}}"
[[ -n "${GCP_PROJECT_ID}" && "${GCP_PROJECT_ID}" != "(unset)" ]] \
  || die "Could not determine project. Set GCP_PROJECT_ID, or run: gcloud config set project <id>"

# --- Config (env-overridable) ---
REGION="${REGION:-europe-west3}"
DOCAI_LOCATION="${DOCAI_LOCATION:-eu}"
BUCKET_NAME="${BUCKET_NAME:-${GCP_PROJECT_ID}-audit-test}"
AUDIT_TYPE="${AUDIT_TYPE:-2. Überwachungsaudit}"
PROCESSOR_DISPLAY_NAME="audit-test-layout"

# --- Flags ---
ASSUME_YES=0
SKIP_RUN=0
REPORT_ONLY=0
for arg in "$@"; do
  case "$arg" in
    -y|--yes)       ASSUME_YES=1 ;;
    --skip-run)     SKIP_RUN=1 ;;
    --report-only)  REPORT_ONLY=1 ;;
    *) echo "Unknown argument: $arg"; exit 1 ;;
  esac
done

# --- Prerequisite checks ---
command -v gcloud >/dev/null  || die "gcloud not found on PATH."
command -v gsutil >/dev/null  || die "gsutil not found on PATH."
command -v python  >/dev/null || die "python not found on PATH."
gcloud auth application-default print-access-token >/dev/null 2>&1 \
  || die "No Application Default Credentials. Run: gcloud auth application-default login"
python -c "import google.genai, fitz, google.cloud.documentai" 2>/dev/null \
  || die "Python deps missing. Run: pip install -r ${APP_DIR}/requirements.txt"

echo "============================================================"
echo " BSI Audit Automator — full TEST run"
echo "   Project:        ${GCP_PROJECT_ID}"
echo "   Region:         ${REGION}   (Document AI: ${DOCAI_LOCATION})"
echo "   Bucket:         gs://${BUCKET_NAME}"
echo "   Audit type:     ${AUDIT_TYPE}"
if [[ $REPORT_ONLY -eq 1 ]]; then echo "   Mode:           provision + report-only (no AI/DocAI spend)";
elif [[ $SKIP_RUN -eq 1 ]]; then echo "   Mode:           provision only (no pipeline run)";
else echo "   Mode:           FULL pipeline (incurs Vertex AI + Document AI cost)"; fi
echo "============================================================"
if [[ $ASSUME_YES -eq 0 ]]; then
  read -p "Proceed and create/use these cloud resources? [y/N] " -n 1 -r; echo
  [[ "$REPLY" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
fi

# --- 1. Enable APIs ---
say "Enabling required APIs (idempotent)…"
gcloud services enable storage.googleapis.com documentai.googleapis.com aiplatform.googleapis.com \
  --project="${GCP_PROJECT_ID}"

# --- 2. Create bucket (idempotent) ---
if gsutil ls -b "gs://${BUCKET_NAME}" >/dev/null 2>&1; then
  say "Bucket gs://${BUCKET_NAME} already exists — reusing."
else
  say "Creating bucket gs://${BUCKET_NAME} in ${REGION}…"
  gcloud storage buckets create "gs://${BUCKET_NAME}" \
    --project="${GCP_PROJECT_ID}" --location="${REGION}" --uniform-bucket-level-access
fi

# --- 3. Create or reuse Document AI Layout Parser processor ---
DOCAI_HOST="${DOCAI_LOCATION}-documentai.googleapis.com"
[[ "${DOCAI_LOCATION}" == "us" ]] && DOCAI_HOST="documentai.googleapis.com"
DOCAI_PARENT="projects/${GCP_PROJECT_ID}/locations/${DOCAI_LOCATION}"
TOKEN="$(gcloud auth print-access-token)"

# Print the processor 'name' from a Document AI JSON response file:
#   $1 = json file, $2 = displayName to match (empty = single 'name' field, i.e. create response)
json_processor_name() {
  python - "$1" "$2" <<'PY'
import sys, json
try:
    data = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
want = sys.argv[2]
if want:  # search a list response for a matching displayName
    for p in data.get("processors", []):
        if p.get("displayName") == want:
            print(p.get("name", "")); break
else:     # a single-processor (create) response
    print(data.get("name", ""))
PY
}

say "Ensuring a Document AI Layout Parser processor exists…"
RESP="$(mktemp)"
curl -s -H "Authorization: Bearer ${TOKEN}" \
  "https://${DOCAI_HOST}/v1/${DOCAI_PARENT}/processors" -o "${RESP}"
DOC_AI_PROCESSOR_NAME="$(json_processor_name "${RESP}" "${PROCESSOR_DISPLAY_NAME}")"

if [[ -n "${DOC_AI_PROCESSOR_NAME}" ]]; then
  say "Reusing processor: ${DOC_AI_PROCESSOR_NAME}"
else
  say "Creating Layout Parser processor '${PROCESSOR_DISPLAY_NAME}'…"
  curl -s -X POST -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    "https://${DOCAI_HOST}/v1/${DOCAI_PARENT}/processors" \
    -d "{\"type\":\"LAYOUT_PARSER_PROCESSOR\",\"displayName\":\"${PROCESSOR_DISPLAY_NAME}\"}" -o "${RESP}"
  DOC_AI_PROCESSOR_NAME="$(json_processor_name "${RESP}" "")"
  [[ -n "${DOC_AI_PROCESSOR_NAME}" ]] || { echo "--- Document AI response ---"; cat "${RESP}"; \
    die "Failed to create Document AI processor. Check that LAYOUT_PARSER_PROCESSOR is available in '${DOCAI_LOCATION}'."; }
  say "Created processor: ${DOC_AI_PROCESSOR_NAME}"
fi
rm -f "${RESP}"

# --- 4. Generate + upload mock documents ---
say "Generating and uploading mock source documents…"
python "${SCRIPT_DIR}/make_mock_docs.py" --bucket "${BUCKET_NAME}"

# --- 5. Export env vars (the ones the app requires + tuning) ---
# REGION is used above for bucket creation only; the app itself never reads it.
say "Exporting environment variables…"
export GCP_PROJECT_ID BUCKET_NAME AUDIT_TYPE DOC_AI_PROCESSOR_NAME
export SOURCE_PREFIX="source_documents/"
export OUTPUT_PREFIX="output/"
export TEST="true"
export MAX_CONCURRENT_AI_REQUESTS="${MAX_CONCURRENT_AI_REQUESTS:-5}"

# --- 6. Run the pipeline ---
run_app() { ( cd "${APP_DIR}" && python -m src.main "$@" ); }

if [[ $SKIP_RUN -eq 1 ]]; then
  say "Provisioning complete (--skip-run). Env is set; run manually, e.g.:"
  echo "    (cd ${APP_DIR} && python -m src.main --run-all-stages --force)"
elif [[ $REPORT_ONLY -eq 1 ]]; then
  say "Assembling report only (--report-only)…"
  run_app --generate-report
else
  say "Step 1/3 — Grundschutz-Check extraction (Document AI)…"
  run_app --run-gs-check-extraction --force
  say "Step 2/3 — All audit stages (Vertex AI)…"
  run_app --run-all-stages --force
  say "Step 3/3 — Assemble + validate final report…"
  run_app --generate-report
fi

echo -e "\n✅ Done."
echo "   Outputs:  gs://${BUCKET_NAME}/output/results/"
echo "   Report:   gsutil ls gs://${BUCKET_NAME}/output/results/report_*.json"
echo ""
echo "🧹 To clean up and stop incurring storage cost:"
echo "   gsutil -m rm -r gs://${BUCKET_NAME}"
echo "   gcloud storage buckets delete gs://${BUCKET_NAME} --project=${GCP_PROJECT_ID}"
echo "   # delete the test processor:"
echo "   curl -s -X DELETE -H \"Authorization: Bearer \$(gcloud auth print-access-token)\" \\"
echo "        https://${DOCAI_HOST}/v1/${DOC_AI_PROCESSOR_NAME}"
