### **AI Data Processing Strategy (Revised)**

**Guiding Principle:** Accuracy and Auditability through Focused, Evidence-Based Generation.

This strategy uses a three-phase process centered around a Vertex AI Vector Search index and Retrieval-Augmented Generation (RAG). The process is driven by the structure of our `master_report_template.json`, ensuring that every AI task is small, focused, and directly tied to a specific section of the final report.

---

### **Phase 0: Idempotent Document Ingestion and Indexing**

**Objective:** To process customer source documents and populate a semantic search index, ensuring resilience and preventing rework. This phase is executed by the `EtlProcessor`.

1.  **List & Filter Documents:** The processor lists all source documents from the configured GCS prefix. For each document, it first checks for a corresponding `.success` or `.failed` status marker in the `etl_status/` GCS prefix. If either marker exists, the document is skipped.
2.  **Per-Document Processing Loop:** For each new document:
    a.  **Extract & Chunk:** Text is extracted (using PyMuPDF for PDFs) and broken into semantically meaningful chunks using a `RecursiveCharacterTextSplitter`. Each chunk is prefixed with metadata (e.g., source page number) for traceability.
    b.  **Generate Embeddings:** Chunks are sent to the `gemini-embedding-001` model to be converted into 3072-dimension vectors. This process is batched and includes robust retry logic.
    c.  **Format & Upload Individual JSONL:** The chunks, their embeddings, and metadata (`id`, `source_document`, `text_content`) are formatted into a single JSONL file (one JSON object per line), specific to the source document. This file is uploaded to the `vector_index_data/` GCS directory.
    d.  **Mark as Completed:** Upon successful upload, an empty `.success` file is created in the status directory. If any step fails permanently after retries, a `.failed` file containing the error is created instead.
3.  **Automatic Index Ingestion:** The Vertex AI Vector Search Index is configured via Terraform to monitor the `vector_index_data/` directory and automatically ingests new JSONL files as they arrive.

---

### **Phase 1: Staged, Finding-Oriented Generation**

**Objective:** To systematically populate the audit report by iterating through each required subchapter, using RAG to generate content and a structured finding. This is orchestrated by the `AuditController`.

1.  **Iterate Report Stages:** The `AuditController` orchestrates the process, running a "stage" for each chapter (e.g., Chapter 1, 3, 4, 5, 7). It uses lazy-loading to instantiate the required `Runner` class for each stage only when needed.
2.  **Formulate Targeted Query:** For each automated subchapter (e.g., 1.2, 3.1, 3.2), the stage runner defines a specific, semantically rich search query tailored to the questions of that section.
3.  **Retrieve Relevant Context:** The query is sent to the `RagClient`. The client embeds the query and searches the Vertex AI Vector Index for the most similar document chunks, returning their full text content as evidence.
4.  **Construct AI Prompt:** A highly specific prompt is assembled, containing:
    *   **The Role:** "You are a BSI security auditor..."
    *   **The Task:** The specific questions to answer for the subchapter.
    *   **The Context:** The full text of the relevant document chunks retrieved in the previous step.
    *   **The Schema Stub:** A JSON schema defining the required output, which **mandates a structured `finding` object** with a `category` ('AG', 'AS', 'E', 'OK') and a `description`.
5.  **Generate and Validate:** The prompt is sent to the `gemini-2.5-pro` model via the `AiClient`. The returned JSON is validated against the schema.
6.  **Centralized Finding Collection:** The `AuditController` inspects the result from every stage. If a generated `finding` object's category is not 'OK', it is appended to a master list of findings held in memory by the controller.
7.  **Save Intermediate Result:** The validated JSON for the entire chapter is saved to GCS (e.g., `output/results/chapter_3.json`). This ensures the entire process is resumable.

---

### **Phase 2: Final Report Assembly**

**Objective:** To deterministically merge all validated JSON stubs and the collected findings into the final audit report. This is handled by the `ReportGenerator`.

1.  **Save All Findings:** After all stages are run, the `AuditController` saves the master list of findings to `output/results/all_findings.json`.
2.  **Load Master Template:** The `ReportGenerator` script loads the `master_report_template.json`.
3.  **Populate Stage Content:** It reads all the individual stage result files (`chapter_1.json`, etc.) from GCS and populates the corresponding sections of the report. This includes filling in text blocks, answers to questions, and deterministically generated tables (like the control checklist in Chapter 5).
4.  **Populate Findings Tables:** The `ReportGenerator` then reads `output/results/all_findings.json` and iterates through the list, populating the three tables in Chapter 7.2 (`geringfuegigeAbweichungen`, `schwerwiegendeAbweichungen`, `empfehlungen`) based on the `category` of each finding.
5.  **Render Final Output:** The final, fully populated JSON object is saved as `final_audit_report.json`, ready for review in the `report_editor.html` tool.