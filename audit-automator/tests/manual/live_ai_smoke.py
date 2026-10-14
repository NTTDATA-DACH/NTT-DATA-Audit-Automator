#!/usr/bin/env python3
"""Live smoke test for the Gemini client (gemini-4-argon on the Agent Platform).

Unlike tests/test_smoke.py (offline, no SDK calls), this script makes a REAL
request through `AiClient`, exercising the full path: client init ->
GenerateContentConfig (system_instruction + response_json_schema + thinking level +
output budget) -> async client.aio.models.generate_content -> finish_reason /
response.text parsing. Run it once after every model or SDK change (one call per posture).

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
from src.constants import CHECKER_THINKING_LEVEL, GEMINI_MODEL, THINKING_LEVEL


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

    # One model, two roles: the document level (maker) and the checker level. Each leg is a
    # real call, so a wrong id, a rejected thinking level or a starved output budget shows here.
    legs = [("THINKING_LEVEL", THINKING_LEVEL)]
    if CHECKER_THINKING_LEVEL != THINKING_LEVEL:
        legs.append(("CHECKER_THINKING_LEVEL", CHECKER_THINKING_LEVEL))

    failures = 0
    for label, level in legs:
        print(f"\n--- {GEMINI_MODEL} at {label} = {level} (calling the Agent Platform, hits the network) ---")
        # Report what is actually sent: this script is the gate for a model refresh
        # (model id, thinking level, output budget, no sampling params, JSON-Schema field).
        effective = client._build_generation_config(schema, thinking_level=level)
        print(
            f"    config: thinking_level={effective.thinking_config.thinking_level}, "
            f"max_output_tokens={effective.max_output_tokens}, automatic_continuation={effective.automatic_continuation}, "
            f"schema via response_json_schema"
        )
        try:
            result = await client.generate_json_response(
                prompt=prompt,
                json_schema=schema,
                gcs_uris=gcs_uris,
                request_context_log=f"live_ai_smoke[{label}]",
                thinking_level=level,
            )
            print(f"SUCCESS ({label}) — schema-validated response: {result}")
        except Exception as e:  # noqa: BLE001 — smoke test: report per leg, keep going
            failures += 1
            print(f"FAILED  ({label}) — {type(e).__name__}: {e}")

    print(f"\n{len(legs) - failures}/{len(legs)} leg(s) OK.")
    # With TEST_PDF_GCS_URI set, the second leg repeats the first leg's prefix (persona + PDF):
    # once that prefix exceeds 6,144 tokens the totals show cached input tokens > 0.
    print(client.usage_summary())
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
