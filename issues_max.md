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
MAX-7 (unified `asyncio.gather` policy via `gather_resilient` helper + documented fail-fast sites).

What remains below needs a real GCP environment, behavioral testing, or network access — so it was
deliberately deferred rather than done blind.

---

## 🟡 Medium

### MAX-8 — Dependencies unpinned / no lockfile
**[requirements.txt](audit-automator/requirements.txt)** · _from A#6 / B-M1_

Zero version constraints on `google-cloud-aiplatform`, `google-cloud-storage`,
`google-cloud-documentai`, `PyMuPDF`, `jsonschema`, `python-dotenv`. Non-reproducible builds; a
breaking upstream SDK release silently breaks the next image rebuild (compounds MAX-5b).

**Fix:** pin (`pkg==x.y.z`) or add a lockfile (`pip-compile` / `uv`). **Deferred because** correct
pins require resolving against the real install set (`pip-compile`), which needs network.

### MAX-9 — Expand test coverage _(partially done)_
_from A#8 / B-M3_

Dependency-light smoke tests were added ([tests/test_smoke.py](audit-automator/tests/test_smoke.py))
plus [requirements-dev.txt](audit-automator/requirements-dev.txt). Still open:
- the empty [tests/test_placeholder.py](audit-automator/tests/test_placeholder.py) (0 bytes) should
  be removed once real tests exist;
- no coverage yet for block grouping / marker detection, AI-response error handling, or report
  assembly (the path that hid MAX-1). These need cloud-SDK stubs/mocks, so they were deferred.
- no CI config wired up.

---

## 📝 Follow-up TODO (left in code)

- **Author a real report JSON Schema.** MAX-1 replaced the no-op template-as-schema validation with
  a cheap structural check (`"bsiAuditReport"` root present). A proper schema under
  `assets/schemas/` would restore real validation of the assembled report; catch `SchemaError` (not
  just `ValidationError`) when it's added.

---

## ✅ Verified NOT issues (reference — do not re-investigate) · _from B's Debunked section_
- `ai_refiner.py:264` "`self.ai_client` undefined": **false** — assigned in `__init__`
  ([ai_refiner.py:24](audit-automator/src/audit/stages/gs_extraction/ai_refiner.py#L24)).
- "`e.message` doesn't exist on `ValidationError`": **false** — `jsonschema.ValidationError`
  exposes `.message`.
- `rag_client.py` "category-map path constant never applied": **false** — already used at
  [rag_client.py:12-14](audit-automator/src/clients/rag_client.py#L12-L14).
