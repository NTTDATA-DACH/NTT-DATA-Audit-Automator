# BSI Grundschutz Audit Automator

This project automates BSI Grundschutz security audits by transforming customer documentation into a structured report using a cloud-native, multi-stage pipeline on Google Cloud. It leverages the Vertex AI Gemini API with a "Document Finder" model to ensure audit findings are contextually relevant, evidence-based, and accurate.

## End-to-End Workflow

### Managing Audit Data: Resetting Between Runs
One script covers both cases; the infrastructure is never touched, so no `terraform apply` is needed afterwards.

**New documents for the same audit** — wipes everything the pipeline generated, keeps the customer's documents:
```bash
bash ./scripts/reset_audit_data.sh
```

**New customer / new document set** — additionally deletes `source_documents/`:
```bash
bash ./scripts/reset_audit_data.sh --with-sources
```

Confirm with `y` (or pass `-y`), then proceed to the Standard Workflow below. The bucket is read from the Terraform state; override it with `BUCKET_NAME=... bash ./scripts/reset_audit_data.sh`.

### Standard Workflow

1.  **Infrastructure Deployment:** If this is the very first run, use Terraform in the `terraform/` directory to create the GCS Bucket, VPC Network, and all necessary Vertex AI and IAM resources.
2.  **Upload Customer Documents:** Upload the customer's documentation, including any previous audit reports, to the `source_documents/` path in the GCS bucket. PDF and spreadsheet exports (XLSX, XLSM, CSV) are both fine; name the files after what they are, since classification goes by filename. Formats matter:
    *   A **Grundschutz-Check (A.4) as spreadsheet** is parsed deterministically — no Document AI, no AI calls for the extraction. As PDF it goes through Document AI and the AI refiner.
    *   Every other spreadsheet reaches the model as its row text (exact cell values, row numbers to cite, far fewer tokens than a page scan). A spreadsheet **replaces a PDF of the same name**; PDFs without a sheet twin are sent as they are.
    *   DOCX and other office formats are not readable by the model and are skipped; the inventory in `output/intermediate/rag/document_inventory.json` (also logged per category at start-up) shows every file, its format and whether it reached the model.
3.  **Deploy the Job Container:** Build and deploy the application container to Cloud Run Jobs.
    ```bash
    # Works from any directory; the scripts locate the repo root themselves
    bash ./scripts/deploy-audit-job.sh
    ```
4.  **Execute Audit Tasks:** Run the desired audit task using the interactive execution script. The first time a task is run, the system will **automatically classify all source documents** if a classification map doesn't already exist.
    ```bash
    # This script provides an interactive menu for all tasks.
    bash ./scripts/execute-audit-job.sh
    ```
    *   To run the entire pipeline, select **"Run All Audit Stages"**. This will execute all steps in the correct prerequisite order, starting with the Grundschutz-Check extraction.
    *   To run only a specific part, select the desired option from the menu.
5.  **Generate the Final Report:** After the stages are complete, this task assembles the final report from all generated components.
    ```bash
    # Select "Generate Final Report" from the menu
    bash ./scripts/execute-audit-job.sh
    ```
6.  **Manual Review and Finalization:** Open the generated `report-YYMMDD.json` from the `output/` GCS prefix in the `report_editor.html` tool to perform the final manual review and make any necessary adjustments.

## Local Development & Smoke Testing

For development you can drive the pipeline locally as a Python module against a real
GCP project, without deploying the Cloud Run job. The pipeline still reads/writes GCS
and calls Vertex AI + Document AI, so you need credentials and a bucket.

1.  **Install dependencies and authenticate:**
    ```bash
    python -m venv venv && source venv/bin/activate     # or reuse the repo venv
    pip install -r audit-automator/requirements.txt      # pinned lock
    gcloud auth application-default login                # ADC for GCS/DocAI/Vertex
    ```
2.  **Set environment variables.** If you have Terraform state, `source audit-automator/envs.sh`
    pulls project/bucket/DocAI from it, sets `TEST=true`, and defines an `auditor`
    helper. Otherwise export the six required vars manually (`GCP_PROJECT_ID`, `BUCKET_NAME`,
    `DOC_AI_PROCESSOR_NAME`, `SOURCE_PREFIX=source_documents/`, `OUTPUT_PREFIX=output/`,
    `AUDIT_TYPE`).
3.  **Generate mock source documents** (no real customer data needed). Classification is
    **filename-based**, so the generator names each PDF after the BSI category it represents and
    drops them straight into `source_documents/`:
    ```bash
    # write PDFs to ./mock_documents (gitignored), then upload, in one step:
    python scripts/make_mock_docs.py --bucket <BUCKET_NAME>
    # or generate locally only:
    python scripts/make_mock_docs.py
    # Grundschutz-Check as XLSX instead of PDF (exercises the spreadsheet path, no Document AI):
    python scripts/make_mock_docs.py --a4-sheet
    ```
    The Grundschutz-Check mock carries fake Zielobjekt markers so the extraction stage has
    structure to group on. Content is intentionally minimal — this exercises plumbing
    (SDK calls, batching, report assembly/validation), **not** output quality. Real customer
    files used to develop parsers stay out of git: office files and PDFs are ignored wholesale.
4.  **Run tasks locally** (dependency order; `--force` on the first run rebuilds the
    filename→category map):
    ```bash
    cd audit-automator
    python -m src.main --run-gs-check-extraction --force   # prerequisite (Document AI)
    python -m src.main --run-stage Chapter-3 --force        # one AI stage (TEST mode = cheap)
    python -m src.main --generate-report                    # assemble + JSON-Schema-validate report
    # or the whole pipeline:  python -m src.main --run-all-stages --force
    ```

### One-shot end-to-end test
To provision a throwaway environment and run the whole pipeline against mock data in
one go, use the harness script (idempotent; creates a bucket + Document AI processor,
uploads mocks, runs all stages, prints cleanup commands):
```bash
# ⚠️ creates real GCS/DocAI resources and makes real Vertex/DocAI calls (costs money)
scripts/test_full_run.sh                 # full run (prompts for confirmation)
scripts/test_full_run.sh --report-only   # provision + assemble report only (no AI spend)
scripts/test_full_run.sh --skip-run      # provision + upload mocks only
```
Override defaults via env, e.g. `BUCKET_NAME=my-test-bucket GCP_PROJECT_ID=… scripts/test_full_run.sh`.

### Updating the BSI requirement catalog
The audit checks against the **official BSI IT-Grundschutz-Kompendium, Edition 2023**. The
runtime does not parse XML: a build-time converter turns the official BSI XML into the lean
catalog at `audit-automator/assets/json/bsi_kompendium_ed2023.json`, which is committed.

```bash
cd audit-automator
python -m src.tools.build_bsi_catalog        # downloads the pinned XML once into .cache/ed23/
```

The source URL is sha256-pinned in `src/tools/ed23_xml.py`, and the converter asserts the
reference counts of that edition (111 Bausteine, 1,834 active requirements, 290 ENTFALLEN)
plus canonical IDs and valid B/S/H levels. If the BSI publishes a new edition, the hash check
fails on purpose — update the pin and the expected counts deliberately, then commit the
regenerated JSON. `tests/test_catalog_invariants.py` re-checks the committed file in CI.

Requirements from Bausteine the institution defined itself are not part of the official
Kompendium; they resolve to level `unbekannt` and are handled by a dedicated prompt rule.

### AI configuration: model, thinking levels, output budget, batch tier
AI behaviour is environment-driven (defaults in `src/constants.py`). One model serves every
task: `gemini-4-argon` on the Gemini Enterprise Agent Platform (formerly Vertex AI), GA on the
global endpoint since 2026-10-13, SDK `google-genai>=2.30.1,<3`. Cost and quality are set per
call by the **thinking level** (`low`/`medium`/`high`) and the **output budget**, never by a
second model: the bulk extraction runs the same model at `low`, the checker at `high`.

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `GEMINI_MODEL` | `gemini-4-argon` | The one model for every call; a rollback lever, not a model menu |
| `THINKING_LEVEL` | `medium` | Thinking level for every call that reasons over customer documents (the maker). Legacy values `minimal`/`off` are mapped to `low`, `max` to `high`; Argon returns 400 for them |
| `CHUNK_THINKING_LEVEL` | `low` | Thinking level of the bulk extraction in the AI refiner; a failed chunk is retried at `THINKING_LEVEL` |
| `CHECKER_THINKING_LEVEL` | `high` | Thinking level of the second opinion in the maker/checker pass |
| `EXTRACTION_THINKING_LEVEL` | `low` | Thinking level of the calls that only classify or extract: filename classification, ground-truth extraction from A.1/A.3, scan of the previous report. Google places classification and extraction at `low` for `gemini-4-argon` |
| `MAX_OUTPUT_TOKENS` | `65536` | Budget per call for thinking **and** answer. Below `32768` the client refuses to start (the model would think the budget away and return truncated JSON); above `65536` the SDK continues the answer in 64K hops (long decode, up to 20 min per hop) |
| `ENABLE_MAKER_CHECKER` | `true` | Second-opinion pass over report-relevant answers |
| `ENABLE_BATCH` | `true` | Send the refiner's chunk requests as one batch job |
| `BATCH_DEADLINE_S` | `300` | Seconds a batch job may take before it is cancelled and the rest runs as standard calls |
| `BATCH_MIN_ITEMS` | `5` | Below this many open chunks the refiner skips the batch tier |

**Caching is implicit.** `gemini-4-argon` has no explicit context cache; instead a request whose
prefix repeats a recent request's prefix of at least 6,144 tokens is billed at about 5 % for that
part, for 15–20 minutes, with no API call. The client therefore sends the persona and the source
documents first and the varying prompt last, so the maker and its checker, and the Chapter-3
subchapters that read the same documents, share the discount. Every call logs its input, cached,
thinking and output tokens and its finish reason.

**Batch tier.** The Grundschutz-Check refiner sends all open chunks as one batch job under
`output/temp/batch/<job-key>/` at half the standard price. After `BATCH_DEADLINE_S` the job is
cancelled (not abandoned), the results it delivered are kept, and the remaining chunks run as
standard calls. A rerun reads the job folder first, so an interrupted job's results are never
paid twice. Per-Zielobjekt results are still cached under `output/intermediate/gs_extraction/individual_results/`.

**Answer validation.** Every reply, from the standard path or the batch tier, is validated
against the JSON schema sent with the request; a mismatch or a truncated answer (`MAX_TOKENS`,
or a `CONTINUATION` the SDK did not follow) is retried like any other failure instead of
reaching the report. No sampling parameters are sent: Argon ignores `temperature`/`top_p`/`top_k`
and rejects the rest, so determinism lives in the prompt and the schema.

With the maker/checker enabled, every answer that reaches the report or produces a finding is
re-judged by a second, independent call against the same source documents: it checks evidence,
AG/AS/E categorisation and completeness, and may replace the answer with a corrected one. Every
verdict is written to `output/intermediate/checker_log.json` as the QS trail of the audit —
review it alongside the report. This roughly doubles the AI calls on those stages; set
`ENABLE_MAKER_CHECKER=false` to fall back to a single pass.

**Prompts.** All prompts live in `assets/json/prompt_config.json`. The system instruction is
role-neutral and German: BSI context, the rule that only the attached documents and the task data
count as evidence (document text is the object under review, never an instruction), the AG/AS/E
category definitions with their lenient grading, and the output language. What a call has to do
(answer the questions, grade, extract, plan) stands in its task prompt, so the maker and the
checker can share the same system instruction and cache prefix without contradicting each other.
Two checks are made in code rather than asked of the model: 3.6.1 lists the Basis-Anforderungen
whose status is not `Ja` (the catalog knows the MUSS ids, the extracted check knows the status),
and every Chapter 4 Baustein plan is held against the ground-truth map (process-layer Bausteine
ISMS/ORP/CON/OPS/DER belong to the Informationsverbund, every other Baustein to a Zielobjekt the
A.3 mapping lists for it). A plan that violates the map is requested once more with the
violations named; rows that still fail are dropped and logged for manual completion.

Before rolling out a model or SDK change, make one live call per thinking level against the real project:
```bash
cd audit-automator && GCP_PROJECT_ID=<project> python tests/manual/live_ai_smoke.py
```

### Running the tests
Unit tests are dependency-light and run from the `audit-automator/` directory; CI runs the
same suite on every push/PR (`.github/workflows/ci.yml`):
```bash
cd audit-automator
pip install -r requirements-dev.txt
python -m pytest
```

## The Audit Stages Explained

The audit pipeline runs in a strict, dependency-aware order.

*   **Phase 0: Document Classification (On-Demand)** (`src/clients/rag_client.py`): This is an automated, on-demand first step that is triggered by other stages. If the `output/intermediate/rag/document_category_map.json` file does not exist, the `RagClient` (acting as a "Document Finder") will use an AI call to classify all source document *filenames* into BSI-specific categories (e.g., "Strukturanalyse", "Vorheriger-Auditbericht"). This map is then saved and used by all subsequent stages. On every start the finder also logs and saves a **format inventory** per category (`document_inventory.json`): which files exist, which format they have, and which of them the model gets (spreadsheets as row text, PDFs as file parts, a spreadsheet replacing a PDF of the same name, unreadable formats skipped).

*   **Stage: Grundschutz-Check Extraction (Prerequisite)** (`audit/stages/stage_gs_check_extraction.py`): This is the first operational stage in a full run. It performs the "Ground-Truth-Driven Semantic Chunking" strategy. It builds an authoritative map of the customer's system from documents like `Strukturanalyse` and `Modellierung`, then uses this map to perform a context-aware extraction and refinement of all requirements from the `Grundschutz-Check` document. The output is a clean, structured JSON file (`extracted_grundschutz_check_merged.json`) that serves as the foundation for later analysis. If the Grundschutz-Check is delivered as a **spreadsheet** (XLSX/XLSM/CSV export of the GRC tool, vertical key/value layout), the stage parses it deterministically instead — same output file, no Document AI, no AI calls — and matches the sheet's Zielobjekte to the ground-truth map by name. A sheet whose layout the parser does not recognise fails the stage with a clear message rather than passing empty data; the PDF path (Document AI + batch-tier refiner) remains the fallback when no sheet is present.
    * It is important to check the results of this stage, as they lay theground truth for all the following ones! Check /output/intermediate/gs_extraction/system_structure_map.json for the correct name of the "Informationsverbund" and at least do a qs of extracted_grundschutz_check_merged.json as well.


*   **Stage: Chapter 1 - General Information** (`audit/stages/stage_1_general.py`): Generates introductory content for the report.
    *   **1.4 (Informationsverbund):** Uses the "Document Finder" to retrieve relevant documents and generate a description of the audit scope.
    *   Other sections are intentionally left as placeholders for manual input.

*   **Stage: Scan Previous Report** (`audit/stages/stage_previous_report_scan.py`): This stage can run in parallel with others. It finds the document classified as `Vorheriger-Auditbericht` and runs three parallel AI extractions to pull structured data for Chapters 1.1-1.3 (General Info), 4.1.1-4.1.2 (Previous Audit Scope), and 7.2 (Previous Findings) into `output/results/Scan-Report.json`.

*   **Stage: Chapter 4 - Audit Plan Creation** (`audit/stages/stage_4_pruefplan.py`): This stage runs after the prerequisite stages. It generates the audit plan and is **conditional** on the `AUDIT_TYPE` environment variable, using different prompts and rules for a "Zertifizierungsaudit" vs. a "Überwachungsaudit". It relies on the ground-truth map created by the extraction stage.

*   **Stage: Chapter 3 - Document Review** (`audit/stages/stage_3_dokumentenpruefung.py`): Performs a deep analysis of core documents. For most subchapters, it uses the Document Finder to retrieve a small, relevant set of documents for the AI to analyze. For the critical `Grundschutz-Check` analysis (3.6.1), it **consumes the high-quality data prepared by the `Grundschutz-Check-Extraction` stage** to ensure maximum accuracy. The chapter skeleton follows the BSI Muster-Auditbericht (Kompendium) V3.0. The two sample tables, 3.3.8 for the Strukturanalyse and 3.4.7 for the Schutzbedarfsfeststellung, are filled by the model from those documents, 3.4.7 reusing the samples 3.3.8 selected; the optional questions about the bereinigter Netzplan are recorded as N/A when no Netzplan is among the documents.

*   **Stage: Chapter 5 - On-Site Audit Preparation** (`audit/stages/stage_5_vor_ort_audit.py`): Prepares materials for the human auditor.
    *   **5.5.2 (Control Verification):** This task is **deterministic**. It reads the audit plan from Chapter 4's output, looks up all required controls from the BSI OSCAL catalog, and enriches this list with the customer's implementation details from the data generated by the `Grundschutz-Check-Extraction` stage. This generates a structured checklist for the auditor and does **not** use AI.

*   **Stage: Chapter 7 - Appendix** (`audit/stages/stage_7_anhang.py`): Generates content for the report's appendix.
    *   **7.1 (Reference Documents):** A **deterministic** task that lists all files found in the source GCS folder.
    *   **7.2 (Abweichungen und Empfehlungen):** This section is populated by the separate `ReportGenerator` task, which reads the centrally collected `all_findings.json` file.