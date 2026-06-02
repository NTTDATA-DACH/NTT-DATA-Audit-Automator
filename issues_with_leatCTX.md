# Code Review — NTT-DATA-Audit-Automator

_Date: 2026-06-02 · Scope: full repo (clients, config, stages, controller, report generator, scripts, terraform, Dockerfile, requirements) · ~3,600 LOC of Python._

This is a read-only review. No code was changed. Every finding below was **verified against the current working tree** — line numbers are accurate as of this commit. A short "Debunked" section at the end lists plausible-looking issues that were checked and found to be *not* real, so you don't waste time on them.

**On "old models":** the tool uses `gemini-2.5-pro` and `gemini-2.5-flash-lite` ([src/constants.py:6-7](audit-automator/src/constants.py#L6-L7)). As of June 2026 these are **a full generation behind** — the Gemini 3 / 3.1 series is now GA on Vertex AI. They still work (2.5 is not retired) but should be upgraded. See **M5** below.

---

## High

### H1 — `sys.exit()` hard-kills the whole audit mid-run
**[audit-automator/src/audit/stages/gs_extraction/block_grouper.py:52](audit-automator/src/audit/stages/gs_extraction/block_grouper.py#L52)**

When no Zielobjekt markers are found, the code calls `sys.exit()`. This terminates the entire process with **exit code 0** — i.e. it looks like a successful run — in the middle of grouping, aborting everything downstream. A missing-marker situation is a recoverable/empty-result condition, not a reason to kill the process.

**Fix:** raise a domain exception the caller can handle, or return an empty grouping and log a warning.

### H2 — Committed merge/patch leftovers in the repo
**[audit-automator/src/clients/rag_client.py.rej](audit-automator/src/clients/rag_client.py.rej)** (tracked in git) and **[validation.patch](validation.patch)** (repo root)

Both are stale artifacts. The changes they describe are **already present** in the source:
- `rag_client.py.rej` proposes importing `DOCUMENT_CATEGORY_MAP_PATH` — but [rag_client.py:12-14](audit-automator/src/clients/rag_client.py#L12-L14) already imports and uses it.
- `validation.patch` adds `generate_validated_json_response` and the `jsonschema` import — but [ai_client.py](audit-automator/src/clients/ai_client.py) already has both.

They serve no purpose and create confusion about the true state of the repo (especially `.rej`, which implies an unresolved conflict that doesn't actually exist).

**Fix:** `git rm src/clients/rag_client.py.rej` and delete `validation.patch`.

### H3 — Retry loop catches *all* exceptions, including bugs
**[audit-automator/src/clients/ai_client.py:208](audit-automator/src/clients/ai_client.py#L208)**

```python
except (api_core_exceptions.GoogleAPICallError, Exception) as e:
```

The trailing `Exception` makes the whole `except` catch everything — programming errors (`KeyError`, `ValueError`, schema bugs, typos) included. These non-transient errors are then retried 5× with exponential backoff (~1+2+4+8+16 ≈ 31s wasted) before finally surfacing, which both masks real bugs and slows failures. The `GoogleAPICallError` in the tuple is redundant.

**Fix:** retry only transient/API errors (e.g. `GoogleAPICallError`, timeouts, and genuine JSON-parse retries); let other exceptions propagate immediately.

---

## Medium

### M1 — Dependencies are completely unpinned
**[audit-automator/requirements.txt](audit-automator/requirements.txt)**

Every dependency is listed without a version (`google-cloud-aiplatform`, `google-cloud-storage`, `google-cloud-documentai`, `PyMuPDF`, `jsonschema`, …). Builds are non-reproducible, and a breaking upstream SDK release can silently break production at the next image rebuild — especially risky given the heavy reliance on the fast-moving Vertex AI SDK.

**Fix:** pin versions (`pkg==x.y.z`) or add a lockfile (`pip-compile` / `uv`).

### M2 — `asyncio.gather` without `return_exceptions=True` (inconsistent)
Call sites that will abort the whole batch (and cancel sibling tasks) if any one sub-task fails:
- [controller.py:190](audit-automator/src/audit/controller.py#L190)
- [gs_extraction/document_processor.py:76](audit-automator/src/audit/stages/gs_extraction/document_processor.py#L76) and [:91](audit-automator/src/audit/stages/gs_extraction/document_processor.py#L91)
- [stage_3_dokumentenpruefung.py:309](audit-automator/src/audit/stages/stage_3_dokumentenpruefung.py#L309) and [:316](audit-automator/src/audit/stages/stage_3_dokumentenpruefung.py#L316)
- [stage_4_pruefplan.py:138](audit-automator/src/audit/stages/stage_4_pruefplan.py#L138)
- [gs_extraction/ai_refiner.py:95](audit-automator/src/audit/stages/gs_extraction/ai_refiner.py#L95) and [:121](audit-automator/src/audit/stages/gs_extraction/ai_refiner.py#L121)
- [stage_previous_report_scan.py:78](audit-automator/src/audit/stages/stage_previous_report_scan.py#L78)

Notably, [report_generator.py:124](audit-automator/src/audit/report_generator.py#L124) **does** use `return_exceptions=True`, so the behavior is inconsistent across the codebase.

**Fix:** pick one policy — fail-fast or collect-and-report — and apply it uniformly. For long, expensive AI batches, `return_exceptions=True` + per-item error handling is usually preferable so one bad document doesn't discard all the completed work.

### M3 — No test coverage
**[audit-automator/tests/test_placeholder.py](audit-automator/tests/test_placeholder.py)** is 0 bytes.

There are no real tests for ~3,600 LOC of audit logic, JSON parsing, and AI-response handling.

**Fix:** add at least smoke/unit tests for the deterministic parsing paths (block grouping, marker detection, schema validation, path construction) and the AI-response error handling. These are the parts most likely to silently regress.

### M4 — Hardcoded `location="global"` contradicts config and logs
**[audit-automator/src/clients/ai_client.py:38-39](audit-automator/src/clients/ai_client.py#L38-L39), [:53](audit-automator/src/clients/ai_client.py#L53)**

```python
# aiplatform.init(project=config.gcp_project_id, location=config.region)
aiplatform.init(project=config.gcp_project_id, location="global")
...
logging.info(f"... in region '{config.region}'.")
```

`config.region` is commented out of the actual init but the log line still claims to use it — misleading when debugging region/quota issues. `config.region` is now effectively unused for model init.

**Fix:** either drive the location from config, or remove the now-dead `region` config dependency, and fix the log message to reflect what's really used.

### M5 — Models are a generation behind (Gemini 2.5 → 3.1)
**[audit-automator/src/constants.py:6-7](audit-automator/src/constants.py#L6-L7)**

```python
CHUNK_PROCESSING_MODEL = "gemini-2.5-flash-lite"
GROUND_TRUTH_MODEL     = "gemini-2.5-pro"
```

As of June 2026 the Gemini 3 / 3.1 series is GA on Vertex AI; the 2.5 models are one full generation old. They still serve (2.5 is not retired), but newer models bring better reasoning/quality at comparable or lower cost — relevant for an audit tool where extraction accuracy matters. Recommended targets:

| Current | Suggested upgrade |
|---|---|
| `gemini-2.5-pro` (ground truth) | `gemini-3.1-pro` |
| `gemini-2.5-flash-lite` (chunk processing) | `gemini-3.1-flash-lite` |

Caution: `gemini-3-pro-preview` was **discontinued on 2026-03-26** — use `gemini-3.1-pro`, not the `3-pro-preview` string. Verify exact GA model IDs and regional availability against the [Vertex AI model list](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models) before switching, and re-run/eval your extraction prompts since Gemini 3 can shift output behavior. This pairs with making the model IDs config-driven rather than hardcoded.

---

## Low

### L1 — Production DEBUG logging is effectively dead
**[audit-automator/src/logging_setup.py:15](audit-automator/src/logging_setup.py#L15)**

`basicConfig` sets DEBUG in production, but the `if not config.is_test_mode` block immediately overrides the root logger back to INFO. So the app's DEBUG logs never emit, despite comments promising "app logs at DEBUG." The intent (high-level INFO + detailed DEBUG) is not achieved.

**Fix:** use a named application logger set to DEBUG (rather than the root logger) if detailed app logs in prod are actually wanted; otherwise update the comments to match reality.

### L2 — `import json` inside a method
**[audit-automator/src/clients/gcs_client.py:95](audit-automator/src/clients/gcs_client.py#L95)** — move the import to module level.

### L3 — Unguarded `int()` on block IDs
**[audit-automator/src/audit/stages/gs_extraction/block_grouper.py:44](audit-automator/src/audit/stages/gs_extraction/block_grouper.py#L44)**

`{int(b['blockId']): b for b in ...}` raises `ValueError` on any non-numeric `blockId`. Guard/validate the input.

### L4 — Unguarded nested access on AI responses
**[audit-automator/src/audit/stages/stage_3_dokumentenpruefung.py:145](audit-automator/src/audit/stages/stage_3_dokumentenpruefung.py#L145)** (and the sibling Q-handlers around it)

`res['answers'][0]` / `res['finding']['category']` rely entirely on schema enforcement. A degraded or empty AI response → `IndexError`/`KeyError`. Add defensive checks or handle the validation failure explicitly.

### L5 — Inconsistent indentation
**[audit-automator/src/clients/rag_client.py:119](audit-automator/src/clients/rag_client.py#L119)** — a 13-space-indented `logging.info` in the `else` branch. Valid Python, cosmetic only.

### L6 — Models hardcoded (not config-driven)
**[audit-automator/src/constants.py:6-7](audit-automator/src/constants.py#L6-L7)** — baking the model IDs into constants means a model swap requires a code change. Consider env/config overrides. (The fact that they're now outdated — see **M5** — is partly a symptom of this.)

---

## Debunked (checked — NOT issues)

These were flagged during review but verified to be false; listed so they don't get re-investigated:

- **`ai_refiner.py:264` — "`self.ai_client` undefined / NameError":** false. `self.ai_client` is assigned in `__init__` at [ai_refiner.py:24](audit-automator/src/audit/stages/gs_extraction/ai_refiner.py#L24).
- **`report_generator.py:153` — "`e.message` doesn't exist on jsonschema `ValidationError`":** false. `jsonschema.ValidationError` does expose a `.message` attribute.
- **`rag_client.py` — "path constant never applied, still hardcoded `output/document_map.json`":** false. The `.rej`'s change is already in the source ([rag_client.py:12-14](audit-automator/src/clients/rag_client.py#L12-L14)).

---

## Suggested priority order
1. **H1** (silent data-loss / fake-success exit) and **H3** (masked bugs + slow failures) — real correctness/operability risks.
2. **H2** — quick repo hygiene; removes confusion (`git rm` + delete).
3. **M5** — upgrade Gemini 2.5 → 3.1 (better accuracy/cost for the audit extraction; do alongside M3 so you can eval the change).
4. **M1, M3** — reproducibility and a safety net before any further changes.
5. **M2, M4** — robustness and observability.
5. **Low items** — cleanup as you touch the surrounding code.
