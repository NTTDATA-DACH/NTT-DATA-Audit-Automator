# Code Review — NTT-DATA-Audit-Automator

_Review date: 2026-06-02. Last commit reviewed: `4a1fa56` (2025-10-30)._

Scope: BSI Grundschutz audit-report pipeline on GCP (Vertex AI Gemini, Document AI, GCS). Modules read directly: `ai_client`, `gcs_client`, `rag_client`, `document_ai_client`, `controller`, `report_generator`, `config`, `main`, `ai_refiner`, `control_catalog`, `constants`, Dockerfile, `envs.sh`, deploy scripts, `.gitignore`. All findings cite `file:line`.

---

## 🔴 Critical

### 1. Final report "validation" uses a data template as a JSON Schema
`report_generator.py:23,26-33` loads `assets/json/master_report_template.json` and passes it to `jsonschema.validate(instance=report, schema=self.report_schema)` (`:150`). That file is a **data template**, not a JSON Schema — it contains literal content nodes such as `"type": "prose"` / `"type": "finding"` (126 `"type":` keys). `jsonschema` interprets `"type"` as a schema keyword, so a value like `"prose"` is an invalid type → validation is either meaningless or raises a spurious error. On `ValidationError` the code `return`s **without saving** (`:152-154`), so the assembled report can be silently discarded.

**Fix:** author a real JSON Schema for the report (separate from the template), or remove the `validate` step.

### 2. Outdated Gemini models AND deprecated Vertex AI SDK
Two distinct problems in the model layer:

**(a) Outdated model IDs.** `constants.py:6-7` pins `GROUND_TRUTH_MODEL = "gemini-2.5-pro"` and `CHUNK_PROCESSING_MODEL = "gemini-2.5-flash-lite"`. As of June 2026 these are **prior-generation** — still supported, but two generations behind the current lineup. The current flagships are **`gemini-3.1-pro`** (Pro / reasoning) and **`gemini-3.5-flash`** (Flash), with `gemini-3.1-flash-lite` as the lightweight tier. Recommended replacements: `gemini-2.5-pro → gemini-3.1-pro` for `GROUND_TRUTH_MODEL`; `gemini-2.5-flash-lite → gemini-3.1-flash-lite` (or `gemini-3.1-flash` if more capability is needed) for `CHUNK_PROCESSING_MODEL`. Avoid `gemini-3.5-flash` here — it is more expensive than `gemini-3.1-flash` and not warranted for chunk processing. Validate output quality/cost after switching, since the 3.x models reason differently. Also note `gemini-2.0-flash`/`flash-lite` were shut down on 2026-06-01, so staying current matters.

**(b) Deprecated SDK.** `ai_client.py:9-12` uses `google.cloud.aiplatform` + `vertexai.generative_models.GenerativeModel/GenerationConfig/Part`. Google deprecated the Vertex generative-AI modules of this SDK (deprecated mid-2025, removal ~mid-2026); as of today it is at/near end-of-life.

**Fix:** bump the model IDs in `constants.py` to the 3.x generation, and migrate the client to the `google-genai` SDK (`google.genai`, `client.models.generate_content`). The model-ID bump is the urgent, low-effort change; the SDK migration is larger.

_Sources: [Vertex AI models](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models), [model versions & lifecycle](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/model-versions)._

### 3. Findings / final-report paths are written and read via two different conventions
`controller.py:170` writes `all_findings.json` to `f"{config.output_prefix}results/all_findings.json"`, but `controller.py:219` and `report_generator.py:241` **read** it from the constant `ALL_FINDINGS_PATH = "output/results/all_findings.json"`. Same split for the final report: saved as `f"{output_prefix}results/report_{date}.json"` (`report_generator.py:158`) while `FINAL_REPORT_PATH` is imported but unused. This only works because `OUTPUT_PREFIX` is hard-coded to `"output/"` in `envs.sh`. Any other prefix → findings silently lost and Chapter 7.2 empty.

**Fix:** pick one source of truth — build all paths from `output_prefix`, or make all path constants relative to it.

---

## 🟠 High

### 4. `aiplatform.init` region hard-coded to `"global"` but logs claim `config.region`
`ai_client.py:39` calls `aiplatform.init(project=..., location="global")` (the `config.region` variant above it is commented out), yet `:51` logs `"in region '{config.region}'"`. The `REGION` env var is effectively ignored and the logs are misleading.

**Fix:** use `config.region` (or document why `"global"` is intentional) and align the log message.

### 5. Retry loop catches everything and retries non-retryable errors
`ai_client.py:208` `except (api_core_exceptions.GoogleAPICallError, Exception)` — `Exception` already subsumes the API error, so the tuple is redundant and **every** exception is retried 5× with backoff: invalid-schema `ValueError`, "no candidates", bad `finish_reason`, JSON parse errors. Wastes time/quota and masks real bugs.

**Fix:** retry only transient API/transport errors; fail fast on programming/validation errors.

### 6. No dependency pinning / lockfile
`requirements.txt` has zero version constraints. For code untouched for months, `pip install` resolves to latest — which, combined with #2, will likely break the build.

**Fix:** pin versions and add a lockfile. Dockerfile base `python:3.11-slim-bookworm` is fine, but only useful when paired with pinned deps.

---

## 🟡 Medium

### 7. Committed junk / scratch files (tracked in git)
- `audit-automator/src/clients/rag_client.py.rej` — a failed `patch` reject whose change is already applied in `rag_client.py:12-14`. Delete.
- `validation.patch` (repo root) — a loose patch already applied to `ai_client.py` (the `generate_validated_json_response` method exists). Delete.
- `audit-automator/check_import.py` — debug scratch using `from google.cloud import documentai` (the real client uses `documentai_v1`). Remove or move out of source.

### 8. No tests
`audit-automator/tests/test_placeholder.py` is empty (0 lines); `requirements.txt` has no `pytest`; no CI config. Zero automated coverage for a multi-stage pipeline.

### 9. `config.py` does work at import time and can kill the process
`config.py:61-65` builds a singleton `config` on import and calls `exit(1)` on missing env. Importing **any** module (e.g. for a unit test or tooling) requires the full GCP env or the interpreter exits.

**Fix:** construct config in an explicit entrypoint call to make the package importable/testable.

### 10. Duplicated constant
`PROMPT_CONFIG_PATH` is hard-coded in `ai_client.py:18` even though it exists in `constants.py` and is imported from there by `rag_client.py` and `ai_refiner.py`.

**Fix:** import it from `constants` for one source of truth.

### 11. Stale docs in `envs.sh`
References a nonexistent flag `--run-etl` (real flag is `--run-gs-check-extraction`, see `main.py:31-34`), and the closing echo advertises a command `bsi-auditor` / `bsi-audit-automator` while the helper function it actually defines is `auditor`.

---

## 🟢 Low / polish

12. `report_generator.py:13` imports `FINAL_REPORT_PATH` but never uses it (report is saved to a date-stamped path instead) — dead import + dead constant (`constants.py:20`).

13. `gcs_client.py:95` does `import json` **inside** `read_json` even though `json` is a module-level import elsewhere. Also `read_json` is type-hinted `-> dict`, but `all_findings.json` is a list, so callers receive a `list` (e.g. `controller.py:223`, `report_generator.py:241`). Fix the hint to `Union[dict, list]` / `Any`.

14. Dead/commented code: test-mode file limiting in `rag_client.py:168-170` is commented out; the `config.region` init line in `ai_client.py:38` is commented out. Remove if intentional.

15. Heavy emoji logging in `ai_refiner.py` (⚡🎯✅❌, e.g. `:172,187,225`) clutters Cloud Logging output — cosmetic.

16. Broad `except Exception` appears across most stage runners (`stage_3/4/5/7`, etc.). Acceptable for resilience, but worth auditing that none silently swallow programming errors.
