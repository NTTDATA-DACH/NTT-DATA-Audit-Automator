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
2.  **Upload Customer Documents:** Upload the customer's documentation (PDFs), including any previous audit reports, to the `source_documents/` path in the GCS bucket.
3.  **Deploy the Job Container:** Build and deploy the application container to Cloud Run Jobs.
    ```bash
    # Run from the project root
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
    ```
    The Grundschutz-Check mock carries fake Zielobjekt markers so the extraction stage has
    structure to group on. Content is intentionally minimal — this exercises plumbing
    (SDK calls, batching, report assembly/validation), **not** output quality.
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

### AI configuration and the maker/checker pass
Model IDs and AI behaviour are environment-driven (defaults in `src/constants.py`):

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `GROUND_TRUTH_MODEL` | `gemini-3.1-pro` | Default model for document reasoning |
| `CHUNK_PROCESSING_MODEL` | `gemini-3.7-flash` | High-volume path in the AI refiner |
| `THINKING_LEVEL` | `minimal` | `minimal`/`low`/`medium`/`high`; clamped to `low` on pro models |
| `ENABLE_MAKER_CHECKER` | `true` | Second-opinion pass over report-relevant answers |
| `CHECKER_MODEL` | = `GROUND_TRUTH_MODEL` | Model used for that second opinion |

With the maker/checker enabled, every answer that reaches the report or produces a finding is
re-judged by a second, independent call against the same source documents: it checks evidence,
AG/AS/E categorisation and completeness, and may replace the answer with a corrected one. Every
verdict is written to `output/intermediate/checker_log.json` as the QS trail of the audit —
review it alongside the report. This roughly doubles the AI calls on those stages; set
`ENABLE_MAKER_CHECKER=false` to fall back to a single pass.

Before rolling out changed model IDs, verify them against the real project:
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

*   **Phase 0: Document Classification (On-Demand)** (`src/clients/rag_client.py`): This is an automated, on-demand first step that is triggered by other stages. If the `output/intermediate/rag/document_category_map.json` file does not exist, the `RagClient` (acting as a "Document Finder") will use an AI call to classify all source document *filenames* into BSI-specific categories (e.g., "Strukturanalyse", "Vorheriger-Auditbericht"). This map is then saved and used by all subsequent stages.

*   **Stage: Grundschutz-Check Extraction (Prerequisite)** (`audit/stages/stage_gs_check_extraction.py`): This is the first operational stage in a full run. It performs the "Ground-Truth-Driven Semantic Chunking" strategy. It builds an authoritative map of the customer's system from documents like `Strukturanalyse` and `Modellierung`, then uses this map to perform a context-aware extraction and refinement of all requirements from the `Grundschutz-Check` document. The output is a clean, structured JSON file (`extracted_grundschutz_check_merged.json`) that serves as the foundation for later analysis.
    * It is important to check the results of this stage, as they lay theground truth for all the following ones! Check /output/intermediate/gs_extraction/system_structure_map.json for the correct name of the "Informationsverbund" and at least do a qs of extracted_grundschutz_check_merged.json as well.


*   **Stage: Chapter 1 - General Information** (`audit/stages/stage_1_general.py`): Generates introductory content for the report.
    *   **1.4 (Informationsverbund):** Uses the "Document Finder" to retrieve relevant documents and generate a description of the audit scope.
    *   Other sections are intentionally left as placeholders for manual input.

*   **Stage: Scan Previous Report** (`audit/stages/stage_previous_report_scan.py`): This stage can run in parallel with others. It finds the document classified as `Vorheriger-Auditbericht` and runs three parallel AI extractions to pull structured data for Chapters 1.1-1.3 (General Info), 4.1.1-4.1.2 (Previous Audit Scope), and 7.2 (Previous Findings) into `output/results/Scan-Report.json`.

*   **Stage: Chapter 4 - Audit Plan Creation** (`audit/stages/stage_4_pruefplan.py`): This stage runs after the prerequisite stages. It generates the audit plan and is **conditional** on the `AUDIT_TYPE` environment variable, using different prompts and rules for a "Zertifizierungsaudit" vs. a "Überwachungsaudit". It relies on the ground-truth map created by the extraction stage.

*   **Stage: Chapter 3 - Document Review** (`audit/stages/stage_3_dokumentenpruefung.py`): Performs a deep analysis of core documents. For most subchapters, it uses the Document Finder to retrieve a small, relevant set of documents for the AI to analyze. For the critical `Grundschutz-Check` analysis (3.6.1), it **consumes the high-quality data prepared by the `Grundschutz-Check-Extraction` stage** to ensure maximum accuracy.

*   **Stage: Chapter 5 - On-Site Audit Preparation** (`audit/stages/stage_5_vor_ort_audit.py`): Prepares materials for the human auditor.
    *   **5.5.2 (Control Verification):** This task is **deterministic**. It reads the audit plan from Chapter 4's output, looks up all required controls from the BSI OSCAL catalog, and enriches this list with the customer's implementation details from the data generated by the `Grundschutz-Check-Extraction` stage. This generates a structured checklist for the auditor and does **not** use AI.

*   **Stage: Chapter 7 - Appendix** (`audit/stages/stage_7_anhang.py`): Generates content for the report's appendix.
    *   **7.1 (Reference Documents):** A **deterministic** task that lists all files found in the source GCS folder.
    *   **7.2 (Abweichungen und Empfehlungen):** This section is populated by the separate `ReportGenerator` task, which reads the centrally collected `all_findings.json` file.