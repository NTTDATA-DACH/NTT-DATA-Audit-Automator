### **AI Data Processing Strategy (Revised)**

**Guiding Principle:** Accuracy and Auditability through Targeted Queries.

This strategy abandons the "single large prompt" approach in favor of a more robust, three-phase process centered around a Vector Database (VDB) and Retrieval-Augmented Generation (RAG). The process is driven by the structure of our `master_report_template.json`, ensuring that every AI task is small, focused, and directly tied to a specific section of the final report.

---

### **Phase 0: Document Ingestion and Indexing**

**Objective:** To process all customer source documents and create a searchable vector index. This is a one-time setup process for each audit.

1.  **Iterate Source Documents (Rule B):** The application will list all source documents from the GCS source prefix. It will process them *one by one*.
2.  **Document Chunking:** Each document is loaded and broken down into smaller, semantically meaningful chunks (e.g., paragraphs or sections of 300-500 words). Each chunk will retain metadata linking it back to its original source document and page number. This is critical for auditability.
3.  **Embedding Generation:** Each chunk is passed to a text-embedding model (like `text-embedding-004`) to be converted into a numerical vector representation.
4.  **Vector Database Indexing (Rule A):** The chunks and their corresponding vectors are stored and indexed in a managed Vector Database (e.g., Vertex AI Vector Search). This index allows for rapid, semantic-based retrieval of the most relevant document chunks for any given query.

---

### **Phase 1: Staged, Chapter-Driven Generation**

**Objective:** To systematically populate the audit report by iterating through each subchapter, querying the Vector Database for relevant context, and generating a validated JSON stub.

This is the core execution loop, orchestrated by the `AuditController`.

1.  **Iterate Report Template (Rule C):** The controller will parse our `master_report_template.json` and loop through each chapter and subchapter that needs to be filled (e.g., starting with 3.1, then 3.2, etc.).
2.  **Formulate Targeted Query:** For each subchapter, the application will use its `title` and `description` to create a concise, semantically rich search query.
    *   *Example for Subchapter 3.1:* The `title` ("Aktualität der Referenzdokumente") and `description` ("Die Aktualität der verwendeten Referenzdokumente muss festgestellt werden.") are combined to form a query like: `"Prüfung der Aktualität und Überarbeitung von Referenzdokumenten A.0, A.1, A.4"`.
3.  **Retrieve Relevant Context:** This search query is sent to the Vector Database. The VDB returns the top N most relevant document chunks (e.g., the top 5-10 chunks) based on semantic similarity.
4.  **Construct AI Prompt (Rule D):** A highly specific, contextual prompt is assembled for the Gemini model. This prompt contains:
    *   **The Role:** "You are a BSI security auditor."
    *   **The Task:** The subchapter's `title` and `description` from our template, which tells the model exactly what part of the report it is working on.
    *   **The Context:** The full text of the relevant document chunks retrieved in the previous step. Each chunk will be clearly marked with its source document name.
    *   **The Schema Stub:** The specific JSON schema defining the required output for *only this subchapter*.
5.  **Execute with "Two-Plus-One" Verification (New Rule):** Instead of a single call, we use a three-step process to ensure quality and consistency:
    *   **a. Parallel Generation:** The same prompt (constructed in Step 4) is sent to the model **twice** in parallel. This yields two independent results, `resultA` and `resultB`.
    *   **b. Consensus Generation:** A new, third prompt is constructed. This "synthesis prompt" instructs the model to act as a senior reviewer. It will contain:
        *   **The Role:** "You are a senior BSI auditor reviewing the work of two junior auditors."
        *   **The Task:** "Synthesize the two provided results (Result A and Result B) into a single, final, and more accurate response. Combine the strengths of both, resolve any inconsistencies, and ensure the final output strictly conforms to the provided JSON schema."
        *   **The Context:** The full JSON of `resultA` and `resultB`.
        *   **The Schema Stub:** The same schema stub from the initial requests.
    *   This third request produces the `finalResult`.

6.  **Final Validation:** The `finalResult` from the consensus step is validated against the stub schema. This ensures the final, synthesized output is still structurally correct.

7.  **Save Intermediate Result (Rule E):** The validated `finalResult` for the subchapter is saved as a discrete file in GCS (e.g., `output/results/chapter_3.1.json`). The process then repeats for the next subchapter.

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