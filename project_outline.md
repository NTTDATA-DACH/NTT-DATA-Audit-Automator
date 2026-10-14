### **Project Brief: BSI Grundschutz Audit Automation with Vertex AI (Revised)**

This document outlines the requirements and development protocol for a Python-based application designed to automate BSI Grundschutz audits using the Vertex AI Gemini API.

**1. Project Overview & Objective**

The primary goal is to develop a cloud-native application that performs a security audit based on the German BSI Grundschutz framework. The application will run as a batch job on Google Cloud Platform (GCP), processing customer-provided documents against BSI standards and generating a structured audit report.

The core of the application is a hybrid pipeline combining deterministic logic with AI-driven analysis. It includes a dedicated **Grundschutz-Check Extraction Stage** which uses a "Ground-Truth-Driven Semantic Chunking" strategy. This involves first creating an authoritative map of the customer's system structure from documents like the `Strukturanalyse` and `Modellierung`. This map is then used to perform a context-aware extraction and reconstruction of security requirements from the `Grundschutz-Check`, ensuring highly accurate, evidence-based findings for later analysis stages. For simpler tasks, a document-finder model provides relevant source documents as context to the Gemini model.

The audit process and resulting report must be based on two key documents:
*   The relevant **BSI Grundschutz Standards** (BSI 200-1, BSI 200-2 and BSI 200-3 and the BSI Grundschutz Kompendium 2023 with its Auditierungsschema and Zertifizierungsschem).
*   The **`bsi-audit-automator/assets/json/master_report_template.json`** file, which serves as the structural and content template for the final audit report.

**2. Core Functional Requirements**

*   **Idempotent Pre-Processing:** A dedicated, resilient pre-processing stage (`Grundschutz-Check-Extraction`) creates authoritative intermediate data from core BSI documents. It is idempotent and its outputs are consumed by later stages.
*   **Staged Audit Process:** The audit is conducted in discrete stages corresponding to the report chapters. The application supports running the pre-processing stage, all analysis stages, or single analysis stages. Some sections (e.g., 1.4, 5.1) are intentionally placeholders for manual auditor input.
*   **State Management & Resumability:** The application saves the results of each stage to Google Cloud Storage (GCS). For full audit runs (`--run-all-stages`), it checks for and skips previously completed stages. For single-stage runs, it overwrites existing results by default.
*   **Centralized Finding Collection:** The application systematically collects all structured findings (deviations and recommendations with categories 'AG', 'AS', 'E') from all stages into a central `all_findings.json` file.
*   **Audit Type Configuration:** The application is configurable for "Überwachungsaudit" or "Zertifizierungsaudit" via an environment variable, which drives different logic in the audit planning stage (Chapter 4).
*   **Deterministic and AI-Driven Logic:** The pipeline intelligently combines AI-driven analysis with deterministic, rule-based logic.
    *   e.g., The `Grundschutz-Check-Extraction` stage first builds a 'ground-truth' map of the system using targeted AI calls, then uses this map to deterministically reconstruct and analyze security control data from unstructured documents.
        *   e.g., Chapter 5 deterministically generates a control checklist by consuming the data from the extraction stage and the BSI catalog.
        *   **Comprehensive Reporting:** The final output is a comprehensive JSON audit report, populated from individual stage results and the central findings file, ready for review and finalization in the `report_editor.html` tool.

**3. Gemini Model and API Interaction**

*   **Model Configuration:** one model id lives in `src/constants.py` (`GEMINI_MODEL`, default `gemini-4-argon`, overridable per environment as a rollback lever); there is no hardcoded model anywhere else and no second model. Cost and quality are set per call by the thinking level and the output budget:
    *   **Document reasoning (the maker):** `THINKING_LEVEL`, default `medium`.
    *   **Bulk extraction in the AI refiner:** `CHUNK_THINKING_LEVEL`, default `low`; a failed chunk is retried at the full `THINKING_LEVEL` on the same model.
    *   **Checker:** `CHECKER_THINKING_LEVEL`, default `high` — the second opinion in the maker/checker pass.
*   **Generation config** (one place: `AiClient._build_generation_config`):
    *   **No sampling parameters.** `temperature`/`top_p`/`top_k` are ignored by Argon, `candidate_count` and the penalties return 400; determinism is prompt + schema.
    *   **Thinking level:** `low`/`medium`/`high`. Legacy names `minimal`/`off` map to `low`, `max` to `high` (Argon returns 400 for them and for `thinking_budget`); anything else is a config error.
    *   **Output budget:** `MAX_OUTPUT_TOKENS`, default 65,536, counts thinking and answer. The client refuses values below 32,768; above 65,536 the SDK (`google-genai>=2.30.1`) continues the answer in 64K hops (`automatic_continuation`), each hop may run up to 20 minutes (client timeout).
    *   **Structured output:** the task's JSON Schema goes to `response_json_schema`.
*   **Robustness:** all API calls go through an asynchronous, semaphore-limited wrapper with an exponential-backoff retry loop (5 attempts, transient errors and unparseable output only; 400/401/403/404 raise at once). The semaphore guards the in-flight call only, not the backoff sleep.
*   **Document formats:** the model gets PDFs as file parts and spreadsheets as rendered row text; a spreadsheet replaces a PDF of the same name, other office formats are skipped, and the per-category decision is logged and saved as `document_inventory.json`. A Grundschutz-Check delivered as spreadsheet bypasses Document AI and the refiner entirely (`sheet_parser.py`).
*   **Implicit caching:** Argon has no explicit context cache (`caches.create` is a 400). A request whose prefix repeats a recent request's prefix of at least 6,144 tokens is discounted by about 95 % for 15–20 minutes, so the client sends the persona and the documents first and the varying prompt last — maker and checker, and the Chapter-3 subchapters on the same set, share the discount. Every call logs its input/cached/thinking/output tokens and finish reason.
*   **Batch tier:** the AI refiner sends all open chunks as one batch job (half price). After `BATCH_DEADLINE_S` (300 s) the job is cancelled, delivered results are kept, and the rest runs as standard calls. A rerun reads the job folder under `output/temp/batch/` first.
*   **Maker/checker:** every answer that reaches the report or produces a finding is re-judged by a second call against the same source documents (`ENABLE_MAKER_CHECKER`, default on). Verdicts land in `output/intermediate/checker_log.json`.
*   **Requirement catalog:** the BSI Kompendium (Edition 2023) is generated from the sha256-pinned official BSI XML at build time (`python -m src.tools.build_bsi_catalog`) and committed as `assets/json/bsi_kompendium_ed2023.json`. The runtime reads only that JSON — no network, no XML parser in the image.

===
**4. AI Collaboration & Development Protocol**

**4.1. Communication Protocol**
*   **Commit Message Format:** Start every response with a summary formatted as follows:
`Case: ` -> A brief summary of my request, case is speaking
`---`
`Dixie: ` -> A brief summary of your solution and key details
*   **How to test this change:** CLI or similar to test.
*   **Explain Your Reasoning:** Briefly explain the "why" behind your code and architectural decisions.
*   **Track Changes:** For minor code changes (under 20 lines), present them in a `diff` format that can be applied with `patch` (Make double sure the format ist 100% correct!). For larger changes, provide the full file content.
*   **No Silent Changes:** Never alter code or logic without explicitly stating the change.
*   **Statefulness and Conciseness:** You are expected to be stateful. Review the existing project context before formulating a response. If your analysis reveals no new bugs, required refactorings, or actionable improvements beyond what is already listed, you MUST respond concisely with the message: `Dixie: I have analyzed the current project state and have no new code changes or tasks to recommend at this time.` Do not re-list or re-phrase existing TODOs.

**4.2. Code Quality and Development Standards**
*   **Modularity and Single Responsibility:** The existing architecture (clients, processors, controllers, stages) must be maintained. Each module and class should have a single, well-defined purpose. For example, `GcsClient` should only ever contain GCS-related logic.
*   **Clarity and Naming:** Code should be as self-documenting as possible. Use descriptive, unambiguous names for variables, functions, and classes (e.g., `_process_single_document` is clearer than `_process`).
*   **Docstrings and Comments:**
*   All public modules, classes, and functions MUST have Python docstrings explaining their purpose, arguments (`Args:`), and return values (`Returns:`).
*   Use inline comments not to explain *what* the code is doing (which should be obvious from the code itself), but to explain *why* a particular implementation choice was made, especially for complex logic, business rules, or workarounds.
*   **Strict Type Hinting:** All new functions and methods MUST include full type hints for arguments and return values. This is essential for static analysis, readability, and preventing runtime errors.
*   **Consistent Formatting:** Code MUST adhere to the PEP 8 style guide. It is recommended to use an automated formatter like `black` to ensure consistency across the project.
*   **Try ... except** For all external calls to API, fielsystems etc use `try`
*   **Robust Error Handling:** Avoid broad `except Exception:` clauses. Catch specific, anticipated exceptions and log them with informative messages. The robust retry logic in `AiClient` serves as a model for handling external API calls.

**4.3. Architecture: "Schema-Stub" Generation**
This is a critical architectural pattern for ensuring reliability.
*   **JSON-Based Communication:** All data sent to and received from the Gemini model must be in JSON format. This must be configured in the `generation_config`.
*   **Schema-Driven Prompts:** For each model interaction, generate a JSON schema "stub" that defines the expected output format. This schema should be included in the prompt.
*   **Minimal Data Subsets:** Pre-filter and structure data sent to the model into a minimal, easy-to-understand JSON object.
*   **Schema as a Quality Gate:** Use the generated schemas to validate the model's JSON output. All validation errors must be caught and logged.
*   **Deterministic Assembly:** The Python script is responsible for the final, deterministic assembly of the complete report from the validated JSON stubs generated by the model.

*   **Critical Schema Constraint: No Tuple Validation for Arrays.**
*   **Problem:** The Vertex AI SDK does **not** support "tuple validation" for arrays, where the `"items"` keyword is an array of different schemas (e.g., `"items": [ { "type": "boolean" }, { "type": "string" } ]`). This format will cause a `TypeError` deep within the SDK's internal parsing logic.
*   **Solution:** To define an array that can contain elements of different types, you **MUST** use a single schema object for `"items"` that contains an `"anyOf"` block listing the possible types. Use `minItems` and `maxItems` to enforce a fixed array length if required.
*   **Correct Implementation Example:**
```json
"answers": {
"type": "array",
"items": {
"anyOf": [
{ "type": "boolean" },
{ "type": "string", "format": "date" }
]
},
"minItems": 4,
"maxItems": 4
}
```


**4.5. Code and Asset Management**
*   **Externalized Assets:** All prompts must be stored in `bsi-audit-automator/assets/json/prompt_config.json` and all JSON schemas in external `.json` files. This separation of logic and assets is mandatory.
*   **Customer Data:** The customer documents are located in one directory the following GCS URI: [GCS_DATA_URI]

**4.6. Testing and Logging**
*   **Test Mode (`TEST="true"`):**
*   Limit processing to a small number of source files (e.g., the first 3).
*   Within each file, limit the data sent for generation (e.g., 10% of discovered items).
*   **Conditional Logging:**
*   The root logger level should be `INFO`.
*   **In Test Mode:** Log detailed, step-by-step messages at the `INFO` level.
*   **In Production Mode (`TEST="false"`):** Do not Log verbose messages at the `DEBUG` level. Log only high-level status updates at the `INFO` level. Log Status updates in long running processes as well.
*   **Suppress Library Noise:** In production, set the logging level for third-party libraries like `google.auth` and `urllib3` to `WARNING` to maintain clean logs.

**4.7. Environment and Configuration**

| Variable | Required? | Source | Description |
| :--- | :---: | :--- | :--- |
| `GCP_PROJECT_ID` | Yes | Terraform | The Google Cloud Project ID. |
| `BUCKET_NAME` | Yes | Terraform | The GCS bucket for all I/O operations. |
| `DOC_AI_PROCESSOR_NAME` | Yes | Terraform | Full resource name of the Document AI Layout Parser (its location is derived from this name). |
| `AUDIT_TYPE` | Yes | User Input | Specifies the audit type (e.g., "Zertifizierungsaudit"). |
| `SOURCE_PREFIX` | Yes | Script | GCS prefix for source documents (e.g., `source_documents/`). |
| `OUTPUT_PREFIX` | Yes | Script | GCS prefix for generated files (e.g., `output/`). |
| `TEST` | No | User Input | `"true"` enables test mode (caps context files per call). Defaults to `false`. |
| `MAX_CONCURRENT_AI_REQUESTS` | No | Script | Concurrent Gemini calls. Defaults to 5. |
| `GEMINI_MODEL` | No | Env | The one model for every call. Defaults to `gemini-4-argon`. |
| `THINKING_LEVEL` | No | Env | `low`/`medium`/`high` for document reasoning. Defaults to `medium` (legacy `minimal`/`off` map to `low`, `max` to `high`). |
| `CHUNK_THINKING_LEVEL` | No | Env | Thinking level of the bulk extraction. Defaults to `low`. |
| `CHECKER_THINKING_LEVEL` | No | Env | Thinking level of the maker/checker second opinion. Defaults to `high`. |
| `MAX_OUTPUT_TOKENS` | No | Env | Thinking + answer budget per call. Defaults to `65536`; below `32768` the client refuses to start. |
| `ENABLE_MAKER_CHECKER` | No | Env | Second-opinion pass over report-relevant answers. Defaults to `true`. |
| `ENABLE_BATCH` | No | Env | Refiner chunks as one batch job. Defaults to `true`. |
| `BATCH_DEADLINE_S` | No | Env | Seconds before a batch job is cancelled and the rest runs as standard calls. Defaults to `300`. |
| `BATCH_MIN_ITEMS` | No | Env | Minimum open chunks for the batch tier. Defaults to `5`. |
