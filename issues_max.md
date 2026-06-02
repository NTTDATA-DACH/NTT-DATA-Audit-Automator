# Code Review — NTT-DATA-Audit-Automator (remaining work)

_Originally consolidated from [issues.md](issues.md) (A) and
[issues_with_leatCTX.md](issues_with_leatCTX.md) (B); see [evaluation.md](evaluation.md) for the
comparison. Most findings were fixed on branch **`fix/max-findings`** (commit `53592b4`)._

**✅ Fixed and removed from this list:** MAX-1 (no-op report validation), MAX-2 (`sys.exit()`
fake-success), MAX-3 (findings path split), MAX-4 (over-broad retry `except`), MAX-5 *model bump*
(2.5→3.1, now env-driven), MAX-6 (region log), MAX-10 (import-time `exit`), MAX-11 (junk files),
MAX-12 (dead DEBUG logging), MAX-13 (dup/dead constants), MAX-14 (`read_json` typing/import),
MAX-15a (`int(blockId)` guard), MAX-16 (`envs.sh` docs), MAX-17 (models config-driven),
MAX-5b (migrated `ai_client` off the deprecated `vertexai`/`aiplatform` SDK to `google-genai`),
MAX-15b (guarded nested access on stage-3 targeted Q-handler AI responses),
MAX-7 (unified `asyncio.gather` policy via `gather_resilient` helper + documented fail-fast sites),
MAX-9 (removed empty placeholder; added unit tests for block grouping/marker detection, AI-response
error handling, the Q-handler guard, and the report structural check; wired GitHub Actions CI),
MAX-8 (pinned deps via `requirements.in`→`requirements.txt` lockfile, compiled on Python 3.11 to
match the Dockerfile; `google-genai` capped to the live-tested `<2`; lock validated in-container).

What remains below needs a real GCP environment, behavioral testing, or network access — so it was
deliberately deferred rather than done blind.

---

## 📝 Follow-up TODO (left in code)

- ✅ **Done — Author a real report JSON Schema.** Added
  [master_report_schema.json](audit-automator/assets/schemas/master_report_schema.json) (draft-07):
  validates the `bsiAuditReport` chapter skeleton and the Chapter 7.2 findings tables (the path that
  hid MAX-1) while staying lenient about free-text/AI-populated content. Wired into
  `ReportGenerator._validate_report_against_schema`, which runs after the cheap structural gate and
  catches both `ValidationError` (malformed report) and `SchemaError` (broken schema asset). Covered
  by tests in [test_report_assembly.py](audit-automator/tests/test_report_assembly.py).

---

## ✅ Verified NOT issues (reference — do not re-investigate) · _from B's Debunked section_
- `ai_refiner.py:264` "`self.ai_client` undefined": **false** — assigned in `__init__`
  ([ai_refiner.py:24](audit-automator/src/audit/stages/gs_extraction/ai_refiner.py#L24)).
- "`e.message` doesn't exist on `ValidationError`": **false** — `jsonschema.ValidationError`
  exposes `.message`.
- `rag_client.py` "category-map path constant never applied": **false** — already used at
  [rag_client.py:12-14](audit-automator/src/clients/rag_client.py#L12-L14).
