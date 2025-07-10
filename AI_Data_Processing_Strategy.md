### **AI Data Processing Strategy**

**Guiding Principle:** Precision through Focused vs. Exhaustive Context.

An LLM's attention is a finite resource. Providing it with an entire corpus of documents to answer a single, specific question forces it to sift through a massive amount of irrelevant noise. This can paradoxically *decrease* precision by diluting evidence, increasing hallucination, and causing the model to drift from its original instruction.

The architecture of this application is designed to mimic an expert human auditor. Instead of saying, "The answer is somewhere in this library of manuals," we say, "Here are the three most relevant documents. Please analyze them." This is achieved through a two-step process: first finding the most relevant documents, then performing a deep analysis of their full content.

---

### **The Two Pillars Driving the Automation**

The entire audit process is dynamically controlled by two central JSON files. This design decouples the audit's structure and AI logic from the Python code, making the system highly maintainable and adaptable.

1.  **`master_report_template.json` (The Structural Blueprint):**
    *   **What it is:** This file is the complete, empty skeleton of the final audit report. It defines every chapter, subchapter, question, and table that must be present.
    *   **Its Role:** It is the **single source of truth for the audit's scope and structure**. The application's `Chapter3Runner`, for example, programmatically parses this file to build its execution plan. It doesn't have hardcoded tasks; instead, it discovers tasks by finding sections with questions in the template. This ensures that if a new subchapter is added to the template, the application will automatically attempt to process it without requiring code changes. It dictates *what* needs to be audited.

2.  **`prompt_config.json` (The Operational Brain):**
    *   **What it is:** This file acts as a detailed instruction manual for the AI. It contains a dictionary that maps subchapter keys (from the `master_report_template.json`) to their specific processing instructions.
    *   **Its Role:** For each task defined by the template, this file specifies *how* it should be audited. Each entry contains:
        *   **`prompt`**: The specific prompt template to be sent to the Gemini model.
        *   **`schema_path`**: A path to the JSON schema that the AI's output **must** adhere to, ensuring structured and validated data.
        *   **`source_categories`**: A list of document categories (e.g., `["Strukturanalyse", "Netzplan"]`) needed to answer the questions. This is the critical link that tells the `RagClient` which documents to "retrieve" for the task.

Together, these files allow the audit process to be modified and extended by simply editing configuration, not Python code, providing immense flexibility.

---

### **Phase 0: On-Demand Document Classification (The "Retrieval" Mechanism)**

**Objective:** To create a persistent, intelligent index of all source documents. This map is the foundation for the "Retrieval" part of the RAG pattern, allowing for fast, targeted document selection in later phases. This is a one-time setup step.

1.  **Trigger & Idempotency Check:** The `RagClient` (acting as the "Document Finder") first checks GCS for `output/document_map.json`. If this map already exists, the entire classification phase is skipped, making the process efficient and idempotent.
2.  **On-Demand Creation:** If the map is missing, the `RagClient` orchestrates its creation:
    *   **List & Classify:** It lists all filenames from the source GCS directory and sends this list to `gemini-2.5-pro` with a prompt (from `prompt_config.json`) instructing it to classify each file into a BSI category based on naming conventions.
    *   **Robust Fallback:** This step is designed for resilience. If the AI call fails for any reason (e.g., API error, invalid response), the application does not halt. Instead, the `RagClient` logs a critical warning and generates a **fallback map**, classifying every document as "Sonstiges" (Miscellaneous). This ensures the pipeline can always proceed, with the known consequence of reduced precision in document selection.
3.  **Load into Memory:** The `RagClient` parses the final JSON map and loads it into an in-memory dictionary. This dictionary, mapping categories to lists of GCS paths, enables near-instantaneous "retrieval" of document URIs for all subsequent audit tasks.

---

### **Phase 1: Staged, Contextual AI-Driven Generation (The Standard "Analysis" Workflow)**

**Objective:** To systematically execute the audit by first retrieving a focused set of documents and then providing them to the AI for deep analysis. This is the standard process for most subchapters.

1.  **Task Identification:** The `AuditController` initiates a stage runner (e.g., `Chapter3Runner`). The runner inspects its execution plan (derived from the `master_report_template.json`).
2.  **Document Retrieval:** For a given subchapter key (e.g., `definitionDesInformationsverbundes`), the runner looks up the corresponding entry in `prompt_config.json` to find the required `source_categories`. It then asks the `RagClient` for the GCS URIs of all documents belonging to those categories.
3.  **Focused Analysis:** The `AiClient` is invoked with the prompt, the required output schema, and the **list of retrieved GCS URIs**. This is the core of the strategy: the model is given direct, full access to a small, highly relevant set of documents, allowing it to perform a deep, focused analysis without the distraction of irrelevant information.
4.  **Validation, Collection, and State Management:** The structured JSON response from the AI is validated against its schema. The `AuditController` extracts any findings and adds them to a central list. The result for the stage is saved to GCS (e.g., `output/results/Chapter-3.json`), ensuring the entire process is resumable.

---

### **Phase 2: Hybrid Processing for Complex Tasks (A Deep Dive into Chapter 3.6.1)**

**Objective:** To accurately analyze the very large and complex "Grundschutz-Check" document. This special workflow is the most sophisticated example of the hybrid strategy, blending large-scale extraction, deterministic logic, and highly targeted AI analysis.

#### **Sub-Phase 2.A: Idempotent Two-Pass Data Extraction**
This phase is engineered for maximum data recall and robustness from a long, complex PDF that is prone to parsing errors.

1.  **Target and Two-Pass Chunking:** The runner targets the "Grundschutz-Check" PDF. It performs **two full extraction passes in parallel** to combat the fragility of parsing tables that may span page breaks differently depending on chunk size.
    *   **Pass 1:** The document is split into **50-page chunks**.
    *   **Pass 2:** It is simultaneously split into **60-page chunks**. This dual-pronged approach significantly increases the probability that at least one of the passes will correctly parse and extract every single requirement.
2.  **Parallel Extraction:** For each chunk in both passes, a temporary PDF is uploaded to GCS, and a parallel AI call is made with a prompt specifically designed for data extraction.
3.  **Merge for Completeness:** The results from both passes are aggregated. A merging logic then iterates through the combined data. For any requirement ID found in both sets, it compares the text length of the explanations and keeps the version with more content, assuming it's the more complete extraction. This "survival of the fittest" merge creates a single, high-quality master list.
4.  **Idempotent Save:** This final list is saved as `output/results/intermediate/extracted_grundschutz_check_merged.json`. If this file already exists on subsequent runs, this entire, computationally expensive sub-phase is skipped.

#### **Sub-Phase 2.B: Deterministic & Targeted AI Analysis**
This phase uses the clean, structured JSON data from the extraction to answer the five audit questions with surgical precision, using the right tool for each job.

1.  **Load Extracted Data:** The runner loads the merged intermediate JSON file. The original PDF is now irrelevant for this analysis.
2.  **Execute Hybrid Logic Question-by-Question:**
    *   **Q1 (Status Check) & Q5 (Date Check):** These are answered with **100% deterministic Python code.** The script simply iterates the JSON list to check for the presence of the `umsetzungsstatus` field and to perform date arithmetic on the `datumLetztePruefung` field. Using Python here is infinitely faster, cheaper, and more reliable than using an LLM for a simple, non-semantic task.
    *   **Q2 (Plausibility of 'entbehrlich'):** This requires semantic understanding, making it an AI task. However, instead of asking the AI to find these items, the script first **filters the JSON to isolate only the items marked "entbehrlich."** It then sends this tiny, focused list to the AI. The model's attention is not wasted on searching; it is entirely dedicated to analyzing the justifications' plausibility.
    *   **Q3 (MUSS requirements):** This is another targeted AI task. The script first **deterministically queries the `ControlCatalog`** to get a definitive list of all Level 1 "MUSS" control IDs. It then filters the extracted JSON to create a small list of just these MUSS requirements and sends them to the AI to verify they are all marked "Ja".
    *   **Q4 (Unmet requirements):** This is the pinnacle of the hybrid approach. The script filters the JSON for items marked "nein" or "teilweise". It then sends this structured list of unmet requirements to the AI **along with the unstructured GCS URI for the entire "Realisierungsplan" PDF.** The prompt instructs the model to perform a cross-referencing task: for each item in the list, verify it is documented in the attached plan. This combines structured data, unstructured data, and semantic analysis in a single, powerful call.
3.  **Consolidate Findings:** The findings from all five questions are consolidated into one final finding for the subchapter.

---

### **Phase 3: Final Report Assembly**

**Objective:** To deterministically merge all validated JSON stubs and the collected findings into the final audit report. This phase is handled by the `ReportGenerator` and is strictly non-AI to ensure final-stage reliability.

1.  **Load All Components:** The `ReportGenerator` loads the `master_report_template.json`, all individual stage result files (e.g., `Chapter-3.json`, `Chapter-4.json`), and the central `all_findings.json`.
2.  **Merge and Populate:** It systematically traverses the master template's structure and injects the content from the corresponding stage results.
3.  **Populate Findings Tables:** It specifically populates the deviation and recommendation tables in Chapter 7.2 by iterating through the `all_findings.json` file.
4.  **Save Final Report:** The fully assembled, validated JSON report is saved to `output/final_audit_report.json`.
