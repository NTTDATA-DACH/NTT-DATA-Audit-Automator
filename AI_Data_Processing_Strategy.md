### **AI Data Processing Strategy (New)**

**Guiding Principle:** Accuracy and Auditability through Direct Document Analysis and Hybrid Processing.

This strategy leverages the native file processing capabilities of the Gemini API, eliminating the need for a separate vector database and ETL pipeline. The process is centered around two core workflows: a standard workflow for general subchapters and a specialized hybrid workflow for handling extremely large or complex documents. The entire process remains driven by the structure of our `master_report_template.json`.

---

### **Phase 0: On-Demand Document Classification**

**Objective:** To create a mapping of source documents to their BSI categories, enabling targeted analysis. This is a one-time setup step performed on the first run of any audit stage.

1.  **Trigger & Check:** When the first audit stage is initiated, the `RagClient` (acting as a "Document Finder") checks for the existence of `output/document_map.json` in GCS.
2.  **On-Demand Creation:** If the map does not exist:
    *   **List:** The client lists all source documents from the `source_documents/` GCS prefix.
    *   **Classify:** It makes a single call to `gemini-2.5-pro`, providing the list of filenames and requesting classification into BSI-specific categories (e.g., "Netzplan", "Sicherheitsleitlinie").
    *   **Save:** The resulting map, which links each document's full GCS path to a category, is saved to `output/document_map.json`. This makes the process idempotent. **If the AI call fails, it robustly creates a fallback map, labeling all documents as "Sonstiges" (Miscellaneous), to allow the pipeline to continue.**
3.  **Load into Memory:** The `RagClient` loads this map into memory for fast lookups during the audit.

---

### **Phase 1: Staged, Contextual AI-Driven Generation (Standard Workflow)**

**Objective:** To systematically populate the audit report by iterating through each required subchapter, providing the AI with direct access to relevant source documents. This is the standard process for most subchapters.

1.  **Iterate Report Stages:** The `AuditController` orchestrates the process, running a "stage" for each chapter (e.g., Chapter 1, 3, 4) and instantiating the required `Runner` class.
2.  **Identify Document Needs:** For each automated subchapter (e.g., 3.2 `sicherheitsleitlinieUndRichtlinienInA0`), the stage runner identifies the required document categories (e.g., `["Sicherheitsleitlinie", "Organisations-Richtlinie"]`).
3.  **Retrieve GCS URIs:** The runner asks the `RagClient` for the corresponding document URIs. The `RagClient` consults the `document_map.json` and returns a list of full `gs://...` paths.
4.  **Construct AI Prompt:** A highly specific prompt is assembled, containing:
    *   **The Role:** "You are a BSI security auditor..."
    *   **The Task:** The specific questions to answer for the subchapter.
    *   **The Schema Stub:** A JSON schema defining the required output, which **mandates a structured `finding` object**.
5.  **Generate with Direct Document Context:** The `AiClient` is called with the prompt, the schema, and the list of GCS URIs. The client attaches these URIs directly to the Gemini API call, allowing the model to analyze the full content of the specified PDFs.
6.  **Validate and Collect:** The returned JSON is validated against the schema. The `AuditController` inspects the result, and if a `finding` is present and not 'OK', it's added to a central list.
7.  **Save Intermediate Result:** The validated JSON for the stage is saved to GCS (e.g., `output/results/Chapter-3.json`), ensuring the process is resumable.

---

### **Phase 2: Hybrid Processing for Complex Tasks (Chapter 3.6.1)**

**Objective:** To accurately analyze the very large "Grundschutz-Check" document by combining large-scale data extraction with deterministic and targeted AI analysis. This special workflow is used for subchapter 3.6.1.

#### **Sub-Phase 2.A: Idempotent Data Extraction**
1.  **Target and Chunk:** The runner targets the "Grundschutz-Check" PDF. Using the `PyMuPDF` library, it splits the document in-memory into 50-page chunks to avoid API token and page limits.
2.  **Process Chunks in Parallel:**
    *   For each chunk, a temporary PDF is uploaded to GCS.
    *   A parallel AI call is made for each chunk, using a specific prompt designed to extract all requirements (`Anforderungen`) and their details (`ID`, `Umsetzungsstatus`, `Umsetzungserläuterung`, `DatumLetztePruefung`) into a structured format.
3.  **Aggregate and Save:** The structured data from all chunks is aggregated into a single list. This master list is saved to GCS as `output/results/intermediate/extracted_grundschutz_check.json`. This extraction is idempotent; if the file already exists, this sub-phase is skipped. Temporary PDF chunks are deleted.

#### **Sub-Phase 2.B: Deterministic & AI Analysis**
1.  **Load Extracted Data:** The runner loads the intermediate JSON file generated in the previous step. The original large PDF is no longer used.
2.  **Answer Questions with Hybrid Logic:**
    *   **Q1 & Q5 (Status & Date Check):** Answered **deterministically** with Python code that iterates through the loaded JSON data, checking for the presence of a status and validating the review date. This is fast and 100% accurate.
    *   **Q2, Q3, & Q4 (Plausibility & Cross-Referencing):** Answered with targeted **AI calls**. The context for these calls is a small, filtered subset of the JSON data, not the entire document.
        *   For Q2, only "entbehrlich" items are sent.
        *   For Q3, only items matching Level 1 ("MUSS") controls are sent.
        *   For Q4, only "nein" or "teilweise" items are sent, and the GCS URI for the "Realisierungsplan" document is also attached to the API call for cross-referencing.
3.  **Consolidate Findings:** The results and findings from all five questions are consolidated into a final structured answer for subchapter 3.6.1.

---

### **Phase 3: Final Report Assembly**

**Objective:** To deterministically merge all validated JSON stubs and the collected findings into the final