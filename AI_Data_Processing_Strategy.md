### **AI Data Processing Strategy (Revised)**

**Guiding Principle:** Accuracy and Auditability through Targeted Queries.

This strategy abandons the "single large prompt" approach in favor of a more robust, three-phase process centered around a Vector Database (VDB) and Retrieval-Augmented Generation (RAG). The process is driven by the structure of our `master_report_template.json`, ensuring that every AI task is small, focused, and directly tied to a specific section of the final report.

---

### **Phase 0: Incremental Document Ingestion and Indexing**

**Objective:** To process each customer source document individually and upload its embeddings to GCS, allowing for robust, scalable, and incremental indexing by Vertex AI Vector Search.

1.  **List Source Documents:** The ETL processor lists all source document blobs from the customer's `source_documents/` GCS directory.

2.  **Per-Document Processing Loop:** The application iterates through each source document one at a time. The following steps are performed for each document before moving to the next:
    a.  **Extract & Chunk:** The text content of a single document (e.g., a PDF) is extracted. This text is then broken down into smaller, semantically meaningful chunks (e.g., paragraphs of ~500 words). Each chunk retains metadata linking it back to the source file.
    b.  **Generate Embeddings:** The list of text chunks for *this document only* is sent to the embedding model (`gemini-embedding-001`) to be converted into numerical vectors.
    c.  **Format & Upload Individual JSON:** The chunks, their corresponding embeddings, and their metadata are formatted into a single JSON file. This file is then immediately uploaded to the `vector_index_data/` GCS directory with a unique name derived from the source document (e.g., `policy_v2.pdf.json`).

3.  **Automatic Index Ingestion:** The Vertex AI Vector Search Index is configured to monitor the `vector_index_data/` directory. It automatically detects each new JSON file as it's uploaded, ingests the data, and updates the search index without requiring a manual "re-index" step.

**Rationale for this approach:**
*   **Scalability:** Processing documents one-by-one prevents out-of-memory errors, even with thousands of large source files.
*   **Resilience:** If the ETL process fails midway, the embeddings for all previously completed documents are already safely stored and indexed. The process can be resumed without losing work.
*   **Speed:** Vertex AI can begin indexing the first documents while later ones are still being processed, leading to a faster overall time-to-availability for the search index.

---

### **Phase 1: Staged, Chapter-Driven Generation**

**Objective:** To systematically populate the audit report by iterating through each subchapter, querying the Vector Database for relevant context, and generating a validated JSON stub.

This is the core execution loop, orchestrated by the `AuditController`.

1.  **Iterate Report Template (Rule C):** The controller will parse our `master_report_template.json` and loop through each chapter and subchapter that needs to be filled (e.g., starting with 3.1, then 3.2, etc.).
2.  **Formulate Targeted Query:** For each subchapter, the application will use its `title` and `description` to create a concise, semantically rich search query.
    *   *Example for Subchapter 3.1:* The `title` ("Aktualität der Referenzdokumente") and `description` ("Die Aktualität der verwendeten Referenzdokumente muss festgestellt werden.") are combined to form a query like: `"Prüfung der Aktualität und Überarbeitung von Referenzdokumenten A.0, A.1, A.4"`.
3.  **Retrieve Relevant Context IDs:** This search query is sent to the Vector Database. The VDB returns the top N most relevant document chunks (e.g., the top 5-10 chunks) based on semantic similarity. **Crucially, the VDB only returns the unique `id` for each matching chunk, not the text itself.**
4.  **Retrieve Full-Text Context:** The application uses the retrieved chunk IDs to look up the full text of each chunk from the embedding files stored in GCS. This lookup is performed using an in-memory map (`id -> text`) that is built once when the application starts, ensuring fast retrieval. This two-step process provides the final text evidence to the AI model.
5.  **Construct AI Prompt (Rule D):** A highly specific, contextual prompt is assembled for the Gemini model. This prompt contains:
    *   **The Role:** "You are a BSI security auditor."
    *   **The Task:** The subchapter's `title` and `description` from our template, which tells the model exactly what part of the report it is working on.
    *   **The Context:** The full text of the relevant document chunks retrieved in the previous step. Each chunk will be clearly marked with its source document name.
    *   **The Schema Stub:** The specific JSON schema defining the required output for *only this subchapter*.
6.  **Execute with "Two-Plus-One" Verification (New Rule):** Instead of a single call, we use a three-step process to ensure quality and consistency:
    *   **a. Parallel Generation:** The same prompt (constructed in Step 5) is sent to the model **twice** in parallel. This yields two independent results, `resultA` and `resultB`.
    *   **b. Consensus Generation:** A new, third prompt is constructed. This "synthesis prompt" instructs the model to act as a senior reviewer. It will contain:
        *   **The Role:** "You are a senior BSI auditor reviewing the work of two junior auditors."
        *   **The Task:** "Synthesize the two provided results (Result A and Result B) into a single, final, and more accurate response. Combine the strengths of both, resolve any inconsistencies, and ensure the final output strictly conforms to the provided JSON schema."
        *   **The Context:** The full JSON of `resultA` and `resultB`.
        *   **The Schema Stub:** The same schema stub from the initial requests.
    *   This third request produces the `finalResult`.

7.  **Final Validation:** The `finalResult` from the consensus step is validated against the stub schema. This ensures the final, synthesized output is still structurally correct.

8.  **Save Intermediate Result (Rule E):** The validated `finalResult` for the subchapter is saved as a discrete file in GCS (e.g., `output/results/chapter_3.1.json`). The process then repeats for the next subchapter.

---

### **Phase 2: Final Report Assembly**

**Objective:** To deterministically merge all the validated JSON stubs into the final, comprehensive audit report.

This phase remains the same as in the previous strategy, ensuring a clean separation of concerns.

1.  **Load Master Template (Rule F):** The `report_generator.py` script loads the empty `master_report_template.json`.
2.  **Aggregate Stubs:** The script reads all the individual result files (`chapter_3.1.json`, `chapter_3.2.json`, etc.) from the GCS output directory.
3.  **Populate Report:** It systematically traverses the master template and populates each section with the data from the corresponding, validated JSON stub.
4.  **Render Final Output:** The final, fully populated JSON object is saved. It can then be rendered into a human-readable format like Markdown or PDF.

### **Rationale for This Pivotal Change**

This RAG-based architecture directly addresses the unreliability of the LCW approach for this use case and offers superior benefits:

*   **Accuracy:** By providing the model with only a small, highly relevant context for each task, we drastically reduce the risk of hallucination, confusion, and factual errors.
*   **Auditability & Traceability:** For every generated finding in the final report, we can log exactly which chunks from which source documents were used as context. This is a powerful feature for proving the audit's validity.
*   **Robustness:** The process is no longer a single, monolithic task. If the generation for one subchapter fails, it can be retried independently without affecting the others.
*   **Scalability:** This approach scales elegantly to hundreds or thousands of source documents without increasing the complexity of a single AI call.