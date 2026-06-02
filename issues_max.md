# Code Review — NTT-DATA-Audit-Automator (consolidated, verified)

_Date: 2026-06-02 · Commit `4a1fa56`._

This is the **union of all real issues** from the two prior reviews
([issues.md](issues.md) = A, [issues_with_leatCTX.md](issues_with_leatCTX.md) = B),
de-duplicated, severity-ranked, and **re-verified against raw source**. Each item cites
`file:line`, the fix, and which review(s) originally found it. See
[evaluation.md](evaluation.md) for the head-to-head comparison and method. False positives
from the original reviews are excluded; verified non-issues are listed at the bottom.

---

## 🔴 Critical

### MAX-1 — Final-report validation is a silent no-op (false safety net)
**[report_generator.py:30-34](audit-automator/src/audit/report_generator.py#L30-L34), [:150-154](audit-automator/src/audit/report_generator.py#L150-L154)** · _from A#1_

`_load_report_schema()` loads the **data template** `assets/json/master_report_template.json`
and passes it as the `schema` to `jsonschema.validate(instance=report, schema=self.report_schema)`.
That file is content, not a schema — 126 content nodes (`80×"type":"question"`, `29×"finding"`,
`17×"prose"`).

**Verified runtime behavior:** the template's keys (`bsiAuditReport`, `content`, …) are not
JSON-Schema applicator keywords, so jsonschema never descends to the nested `"type"` nodes.
`validate()` therefore passes for **any** input (confirmed: it accepts an unrelated
`{"foo":"bar"}`). The report's only validation step does **nothing** — every report, malformed
or not, passes and is saved. (Note: this corrects A's original claim that it *raises* and
discards the report; it does the opposite.)

**Fix:** author a real JSON Schema for the report (separate file under `assets/schemas/`) and
validate against it; or remove the `validate` call entirely rather than ship a no-op that
implies safety. Also catch `SchemaError`, not just `ValidationError`, if a real schema is added.

### MAX-2 — `sys.exit()` aborts the whole run as a fake success
**[block_grouper.py:49-52](audit-automator/src/audit/stages/gs_extraction/block_grouper.py#L49-L52)** · _from B-H1_

When no Zielobjekt markers are found, `group_layout_blocks_by_zielobjekt` calls bare
`sys.exit()` (and `import sys` is present at line 4). This terminates the entire process with
**exit code 0** — it looks like a successful run — mid-pipeline. A missing-marker case is a
recoverable/empty-result condition, not grounds to kill the job.

**Fix:** raise a domain exception the caller can handle, or save an empty/`_UNGROUPED_` grouping
and log a warning, then continue.

---

## 🟠 High

### MAX-3 — Findings file written and read via two different path conventions
**[controller.py:170](audit-automator/src/audit/controller.py#L170)** (write) vs
**[controller.py:219](audit-automator/src/audit/controller.py#L219)** /
**[report_generator.py](audit-automator/src/audit/report_generator.py) `_populate_chapter_7_findings`** (read) · _from A#3_

Write uses `f"{self.config.output_prefix}results/all_findings.json"`; reads use the constant
`ALL_FINDINGS_PATH = "output/results/all_findings.json"` ([constants.py:16](audit-automator/src/constants.py#L16)).
These agree **only because** `OUTPUT_PREFIX="output/"` ([envs.sh:47](audit-automator/envs.sh#L47)).
Any other prefix → findings written to one path, read from another → Chapter 7.2 silently empty.

**Fix:** one source of truth — derive every output path from `config.output_prefix`, or make all
path constants relative to it. Same split affects the final report
([report_generator.py:158](audit-automator/src/audit/report_generator.py#L158) vs the unused
`FINAL_REPORT_PATH`, see MAX-12).

### MAX-4 — Retry loop catches everything, retries non-retryable errors
**[ai_client.py:208](audit-automator/src/clients/ai_client.py#L208)** · _from A#5 / B-H3_

`except (api_core_exceptions.GoogleAPICallError, Exception) as e:` — the trailing `Exception`
subsumes the API error and catches **all** errors: invalid-schema `ValueError`, "no candidates",
bad `finish_reason`, JSON parse errors, plain bugs. Each is retried 5× with exponential backoff
(~31s wasted) before surfacing, masking real bugs and slowing failure.

**Fix:** retry only transient API/transport errors; let programming/validation errors propagate
immediately. (Drop the redundant `GoogleAPICallError` from the tuple.)

### MAX-5 — Outdated models **and** deprecated Vertex SDK
**[constants.py:6-7](audit-automator/src/constants.py#L6-L7)**, **[ai_client.py:5,9](audit-automator/src/clients/ai_client.py#L5)** · _from A#2 / B-M5_

- **Models:** `GROUND_TRUTH_MODEL="gemini-2.5-pro"`, `CHUNK_PROCESSING_MODEL="gemini-2.5-flash-lite"`
  are a full generation behind (Gemini 3.1 is GA as of 2026-06). Suggested: `2.5-pro → 3.1-pro`,
  `2.5-flash-lite → 3.1-flash-lite`. Avoid the discontinued `gemini-3-pro-preview` string.
- **SDK:** the client uses `google.cloud.aiplatform` + `vertexai.generative_models`
  (`GenerativeModel`/`GenerationConfig`/`Part`), which Google deprecated (removal ~mid-2026).

**Fix:** bump model IDs now (low effort); migrate to the `google-genai` SDK
(`google.genai`, `client.models.generate_content`) as a larger follow-up. Re-eval extraction
prompts after the model bump (3.x reasons differently).

---

## 🟡 Medium

### MAX-6 — `aiplatform.init` location hardcoded `"global"`, contradicts config + logs
**[ai_client.py:38-39](audit-automator/src/clients/ai_client.py#L38-L39), [:51](audit-automator/src/clients/ai_client.py#L51)** · _from A#4 / B-M4_

The `config.region` init is commented out; the active call hardcodes `location="global"`, yet the
log line still claims `in region '{config.region}'`. `REGION` is effectively ignored and logs
mislead during region/quota debugging.

**Fix:** drive location from `config.region` (or document why `"global"` is intentional) and align
the log message.

### MAX-7 — `asyncio.gather` exception policy is inconsistent
_from B-M2_

Most batch call sites use bare `asyncio.gather` (abort-all-on-first-failure, cancel siblings):
[controller.py:190](audit-automator/src/audit/controller.py#L190),
[document_processor.py:76](audit-automator/src/audit/stages/gs_extraction/document_processor.py#L76)/[:91](audit-automator/src/audit/stages/gs_extraction/document_processor.py#L91),
[stage_3_dokumentenpruefung.py:309](audit-automator/src/audit/stages/stage_3_dokumentenpruefung.py#L309)/[:316](audit-automator/src/audit/stages/stage_3_dokumentenpruefung.py#L316),
[stage_4_pruefplan.py:138](audit-automator/src/audit/stages/stage_4_pruefplan.py#L138),
[ai_refiner.py:95](audit-automator/src/audit/stages/gs_extraction/ai_refiner.py#L95)/[:121](audit-automator/src/audit/stages/gs_extraction/ai_refiner.py#L121),
[stage_previous_report_scan.py:78](audit-automator/src/audit/stages/stage_previous_report_scan.py#L78) —
while [report_generator.py:124](audit-automator/src/audit/report_generator.py#L124) **does** use
`return_exceptions=True`.

**Fix:** pick one policy and apply it uniformly. For long, expensive AI batches prefer
`return_exceptions=True` + per-item handling so one bad document doesn't discard completed work.

### MAX-8 — Dependencies unpinned / no lockfile
**[requirements.txt](audit-automator/requirements.txt)** · _from A#6 / B-M1_

Zero version constraints on `google-cloud-aiplatform`, `google-cloud-storage`,
`google-cloud-documentai`, `PyMuPDF`, `jsonschema`, `python-dotenv`. Non-reproducible builds; a
breaking upstream SDK release silently breaks the next image rebuild (compounds MAX-5).

**Fix:** pin (`pkg==x.y.z`) or add a lockfile (`pip-compile` / `uv`).

### MAX-9 — No test coverage
**[tests/test_placeholder.py](audit-automator/tests/test_placeholder.py)** is 0 bytes; no `pytest`
in deps; no CI · _from A#8 / B-M3_

**Fix:** add smoke/unit tests for the deterministic paths most likely to silently regress —
block grouping, marker detection, path construction, AI-response error handling, and report
assembly (which would have caught MAX-1).

### MAX-10 — `config.py` does work at import time and can kill the process
**[config.py:59-65](audit-automator/src/config.py#L59-L65)** · _from A#9_

A singleton `config = load_config_from_env()` runs on import; on missing env it `print`s and
`exit(1)`. Importing **any** module (e.g. for a unit test or tooling) requires full GCP env or the
interpreter exits — a direct blocker for MAX-9.

**Fix:** construct config in an explicit entrypoint call so the package is importable/testable.

---

## 🟢 Low / polish

### MAX-11 — Committed junk / scratch files (tracked in git) · _from A#7 / B-H2_
- `audit-automator/src/clients/rag_client.py.rej` — failed patch reject; its change is already in
  [rag_client.py:12-14](audit-automator/src/clients/rag_client.py#L12-L14). `git rm` it.
- `validation.patch` (repo root) — already applied to `ai_client.py`. Delete.
- `audit-automator/check_import.py` — debug scratch. Remove or move out of `src`.

### MAX-12 — Dead DEBUG logging in production · _from B-L1_
**[logging_setup.py:15](audit-automator/src/logging_setup.py#L15), [:26](audit-automator/src/logging_setup.py#L26)** —
`basicConfig` sets DEBUG, then the `if not is_test_mode` block resets the **root** logger to INFO,
so the promised app-level DEBUG logs never emit.
**Fix:** use a named application logger at DEBUG, or update the misleading comments.

### MAX-13 — Duplicated / dead constants
- `PROMPT_CONFIG_PATH` re-hardcoded at [ai_client.py:18](audit-automator/src/clients/ai_client.py#L18)
  though it already exists in [constants.py:53](audit-automator/src/constants.py#L53). Import it. _(A#10)_
- `FINAL_REPORT_PATH` imported at [report_generator.py:13](audit-automator/src/audit/report_generator.py#L13)
  but never used (report saved to a date-stamped path). Remove import + constant. _(A#12)_

### MAX-14 — `read_json` typing / local import
**[gcs_client.py:95](audit-automator/src/clients/gcs_client.py#L95)** — `import json` inside the
method (move to module level); hint is `-> dict` but `all_findings.json` is a `list`, so callers
get a `list` ([controller.py:223](audit-automator/src/audit/controller.py#L223),
report_generator). Fix hint to `Union[dict, list]` / `Any`. _(A#13 / B-L2)_

### MAX-15 — Unguarded parsing of external/AI data · _from B-L3 / B-L4_
- `{int(b['blockId']): b ...}` at [block_grouper.py:44](audit-automator/src/audit/stages/gs_extraction/block_grouper.py#L44)
  raises `ValueError` on a non-numeric `blockId`.
- `res['answers'][0]` / `res['finding']['category']` around
  [stage_3_dokumentenpruefung.py:145](audit-automator/src/audit/stages/stage_3_dokumentenpruefung.py#L145)
  rely entirely on schema enforcement → `IndexError`/`KeyError` on a degraded AI response.

**Fix:** validate/guard before indexing; handle the empty/malformed case explicitly.

### MAX-16 — Stale developer docs in `envs.sh` · _from A#11_
**[envs.sh:19](audit-automator/envs.sh#L19), [:70-71](audit-automator/envs.sh#L70-L71)** —
advertises a nonexistent flag `--run-etl` (real flag is `--run-gs-check-extraction`, see
[main.py:31-34](audit-automator/src/main.py#L31-L34)) and a command `bsi-auditor` while the
function actually defined is `auditor` ([:57](audit-automator/envs.sh#L57)). Align docs to reality.

### MAX-17 — Models not config-driven · _from B-L6_
Model IDs are baked into [constants.py:6-7](audit-automator/src/constants.py#L6-L7); a swap needs a
code change (and is partly why they drifted out of date — MAX-5). Consider env/config overrides.

---

## ✅ Verified NOT issues (do not re-investigate) · _from B's Debunked section_
- `ai_refiner.py:264` "`self.ai_client` undefined": **false** — assigned in `__init__`
  ([ai_refiner.py:24](audit-automator/src/audit/stages/gs_extraction/ai_refiner.py#L24)).
- `report_generator.py:153` "`e.message` doesn't exist on `ValidationError`": **false** —
  `jsonschema.ValidationError` exposes `.message`. _(But note this same block hides MAX-1.)_
- `rag_client.py` "category-map path constant never applied": **false** — already used at
  [rag_client.py:12-14](audit-automator/src/clients/rag_client.py#L12-L14).

---

## Suggested fix order
1. **MAX-1, MAX-2** — silent no-op validation + fake-success exit (correctness/trust).
2. **MAX-3, MAX-4** — data-loss path split + masked/slow failures.
3. **MAX-5** — model bump (quick) ahead of the SDK migration; pair with MAX-9 to eval.
4. **MAX-8, MAX-9, MAX-10** — reproducibility + a testable, importable package.
5. **MAX-6, MAX-7** — robustness/observability.
6. **MAX-11 … MAX-17** — cleanup as you touch the surrounding code.
