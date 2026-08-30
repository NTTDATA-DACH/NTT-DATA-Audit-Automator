#!/usr/bin/env python3
"""Live smoke test for the google-genai migration (MAX-5b).

Unlike tests/test_smoke.py (offline, no SDK calls), this script makes a REAL
Vertex AI request through the migrated `AiClient`, exercising the full path:
client init -> GenerateContentConfig (system_instruction + response_schema) ->
async client.aio.models.generate_content -> finish_reason / response.text parsing.

It is a MANUAL test: it needs GCP creds + network, so it is not part of the
default pytest run. It is named `live_ai_smoke.py` (not `test_*.py`) and kept
under tests/manual/ so pytest's default discovery skips it; run it explicitly.

Prerequisites (shell with GCP access):
    pip install -r requirements.txt
    gcloud auth application-default login        # or a service-account key
    export GCP_PROJECT_ID=your-project-id

Run (from anywhere — the script locates the package itself):
    python tests/manual/live_ai_smoke.py

Optional: also exercise the Part.from_uri / PDF-attachment path:
    export TEST_PDF_GCS_URI=gs://your-bucket/some.pdf
    python tests/manual/live_ai_smoke.py
"""
import asyncio
import os
import sys
import pathlib

# Make the script runnable from any cwd: the package root is tests/manual/../..
# and the AiClient reads assets via a path relative to that root, so chdir there.
PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PACKAGE_ROOT))
os.chdir(PACKAGE_ROOT)

from src.config import AppConfig
from src.clients.ai_client import AiClient
from src.constants import GROUND_TRUTH_MODEL, CHUNK_PROCESSING_MODEL


def _build_config() -> AppConfig:
    project_id = os.getenv("GCP_PROJECT_ID")
    if not project_id:
        sys.exit("ERROR: set GCP_PROJECT_ID (the Vertex AI project to call).")
    # Only gcp_project_id / max_concurrent_ai_requests matter for AiClient; the
    # remaining AppConfig fields are unused by this code path, so they get
    # harmless placeholders.
    return AppConfig(
        gcp_project_id=project_id,
        source_prefix="",
        output_prefix="",
        audit_type="",
        doc_ai_processor_name="",
        max_concurrent_ai_requests=1,
        is_test_mode=True,
        bucket_name=None,
    )


async def _run() -> int:
    client = AiClient(_build_config())

    # A tiny schema so we can verify response_schema enforcement end-to-end.
    schema = {
        "type": "object",
        "properties": {
            "language": {"type": "string"},
            "word_count": {"type": "integer"},
        },
        "required": ["language", "word_count"],
    }
    prompt = (
        "Identify the language of this sentence and count its words: "
        "'Der schnelle braune Fuchs springt ueber den faulen Hund.' "
        "Respond using the provided JSON schema."
    )

    pdf_uri = os.getenv("TEST_PDF_GCS_URI")
    gcs_uris = [pdf_uri] if pdf_uri else None
    if pdf_uri:
        print(f"PDF attachment leg enabled (Part.from_uri): {pdf_uri}")

    # Check BOTH configured models: the ground-truth default (no override) and
    # the chunk-processing model (via model_override, which also exercises that path).
    # De-duplicate in case both env vars resolve to the same ID.
    models = [("GROUND_TRUTH_MODEL", GROUND_TRUTH_MODEL)]
    if CHUNK_PROCESSING_MODEL != GROUND_TRUTH_MODEL:
        models.append(("CHUNK_PROCESSING_MODEL", CHUNK_PROCESSING_MODEL))

    failures = 0
    for label, model_id in models:
        print(f"\n--- {label} = {model_id} (calling Vertex AI, hits the network) ---")
        # Report what is actually sent: this script is the gate for the model refresh
        # (current model IDs, temperature 1, thinking level, JSON-Schema field).
        effective = client._build_generation_config(schema, model_id)
        # getattr, not attribute access: _build_generation_config falls back to the older
        # fields when the installed SDK lacks them, and reading them directly would make
        # this script fail on exactly the SDKs the fallback exists for.
        schema_field = "response_json_schema" if getattr(effective, "response_json_schema", None) else "response_schema"
        thinking_config = getattr(effective, "thinking_config", None)
        thinking = thinking_config.thinking_level if thinking_config else None
        print(f"    config: temperature={effective.temperature}, thinking_level={thinking}, schema via {schema_field}")
        try:
            result = await client.generate_json_response(
                prompt=prompt,
                json_schema=schema,
                gcs_uris=gcs_uris,
                request_context_log=f"live_ai_smoke[{label}]",
                model_override=model_id,
            )
            print(f"SUCCESS ({model_id}) — schema-validated response: {result}")
        except Exception as e:  # noqa: BLE001 — smoke test: report per-model, keep going
            failures += 1
            print(f"FAILED  ({model_id}) — {type(e).__name__}: {e}")

    print(f"\n{len(models) - failures}/{len(models)} model(s) OK.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
