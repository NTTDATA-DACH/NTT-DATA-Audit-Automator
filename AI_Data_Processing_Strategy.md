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

### **Phase -1: Ingestion of Prior Audit Data**
**Objective:** To extract structured data from a previous audit report to potentially inform the current audit. This is the new, initial step in the pipeline.

1.  **Document Identification:** The process relies on the on-demand document classification (Phase 0) to find a file categorized as `Vorheriger-Auditbericht`.
2.  **Parallel Extraction:** The `PreviousReportScanner` stage initiates three targeted AI calls in parallel to maximize efficiency. Each call is given the previous report PDF and a specific prompt to extract data from different chapters:
    *   **Task 1 (Chapter 1):** Extracts tables for `Versionshistorie`, `Auditierte Institution`, and `Auditteam`.
    *   **Task 2 (Chapter 4):** Extracts tables detailing the previously audited `Bausteine`.
    *   **Task 3 (Chapter 7):** Extracts tables of all previous findings (`Abweichungen` and `Empfehlungen`).
3.  **Structured Output:** Each extraction task uses a strict JSON schema to ensure the output is structured and reliable. The combined results are saved to `output/results/Scan-Report.json`.

---

### **Phase 0: On-Demand Document Classification (The Document Finder)**

**Objective:** To create a persistent, intelligent index of all source documents. This map is the foundation for finding targeted documents in later phases. This is a one-time, on-demand setup step.

1.  **Trigger & Idempotency Check:** The `RagClient` (acting as the "Document Finder") first checks GCS for `output/document_map.json`. If this map already exists, the entire classification phase is skipped, making the process efficient and idempotent.
2.  **On-Demand Creation:** If the map is missing, the `RagClient` orchestrates its creation:
    *   **List & Classify:** It lists all filenames from the source GCS directory and sends this list to `gemini-2.5-pro` with a prompt (from `prompt_config.json`) instructing it to classify each file into a BSI category based on naming conventions.
    *   **Robust Fallback:** This step is designed for resilience. If the AI call fails for any reason (e.g., API error, invalid response), the application does not halt. Instead, the `RagClient` logs a critical warning and generates a **fallback map**, classifying every document as "Sonstiges" (Miscellaneous). This ensures the pipeline can always proceed, with the known consequence of reduced precision in document selection.
3.  **Load into Memory:** The `RagClient` parses the final JSON map and loads it into an in-memory dictionary. This dictionary, mapping categories to lists of GCS paths, enables near-instantaneous lookup of document URIs for all subsequent audit tasks.

---

### **Phase 1: Staged, Contextual AI-Driven Generation (The Standard "Analysis" Workflow)**

**Objective:** To systematically execute the audit by first finding a focused set of documents and then providing them to the AI for deep analysis. This is the standard process for most subchapters.

1.  **Task Identification:** The `AuditController` initiates a stage runner (e.g., `Chapter3Runner`). The runner inspects its execution plan (derived from the `master_report_template.json`).
2.  **Document Lookup:** For a given subchapter key (e.g., `definitionDesInformationsverbundes`), the runner looks up the corresponding entry in `prompt_config.json` to find the required `source_categories`. It then asks the `RagClient` for the GCS URIs of all documents belonging to those categories.
3.  **Focused Analysis:** The `AiClient` is invoked with the prompt, the required output schema, and the **list of retrieved GCS URIs**. This is the core of the strategy: the model is given direct, full access to a small, highly relevant set of documents, allowing it to perform a deep, focused analysis without the distraction of irrelevant information.
4.  **Validation, Collection, and State Management:** The structured JSON response from the AI is validated against its schema. The `AuditController` extracts any findings and adds them to a central list. The result for the stage is saved to GCS (e.g., `output/results/Chapter-3.json`), ensuring the entire process is resumable.

---

### **Phase 1.5: Ground-Truth-Driven Audit Planning**
**Objective:** To create a realistic, accurate, and compliant audit plan that is based on the customer's actual, documented system structure, rather than a plausible hallucination.

1.  **Prerequisite:** This phase runs *after* the Ground Truth Map (`system_structure_map.json`) has been created by Phase 2, Step 1.
2.  **Load Ground Truth:** The `Chapter4Runner` loads the complete `system_structure_map.json` from GCS. This map contains the authoritative list of which `Bausteine` are applied to which `Zielobjekte`.
3.  **Inject Context into Prompt:** The entire ground-truth JSON map is serialized and injected directly into the prompt for the AI.
4.  **Constrained Instruction:** The prompt is explicitly updated with a critical instruction for the AI: it **MUST** create a plan where every selected `Baustein` is paired with a `Zielobjekt Kürzel` that it is actually mapped to in the provided ground-truth context.
5.  **Accurate Output:** The result is an audit plan (for Chapter 4.1.1, 4.1.2, etc.) that is guaranteed to be consistent with the customer's `Modellierung` document. This prevents downstream errors in Chapter 5, where the system looks up implementation details for the planned items.

This step is a crucial bridge between deep analysis (Chapter 3) and planning (Chapter 4), ensuring the entire audit process remains factually grounded.


---


### **Phase 2: Ground-Truth-Driven Extraction (via Document AI & Gemini)**

The goal of this stage is to convert the semi-structured `Grundschutz-Check` PDF into a clean, hierarchically-organized JSON structure grouped by Zielobjekte (target objects), using a robust four-phase approach combining deterministic logic with AI-powered analysis.

**Architecture:** The stage is implemented as a modular pipeline with four specialized components:
- **GroundTruthMapper**: Creates authoritative system structure from customer documents
- **DocumentProcessor**: Handles Document AI workflow for PDF processing  
- **BlockGrouper**: Groups content blocks by Zielobjekt using marker-based algorithm
- **AiRefiner**: Extracts structured requirements using AI with intelligent chunking

---

#### **Phase 2.1: Ground Truth Establishment**
**Processor:** The **GroundTruthMapper** uses targeted AI calls to extract the authoritative system structure from customer documents.

**Ground Truth Sources:**
- **Zielobjekte** (target objects) from the `Strukturanalyse` (A.1)
- **Baustein-to-Zielobjekt mappings** from the `Modellierung` (A.3)

**Output:** Creates a "Ground Truth" map (`system_structure_map.json`) of what we expect to find in the document, serving as the foundation for all subsequent processing.

---

#### **Phase 2.2: Document Layout Extraction**
**Processor:** The **DocumentProcessor** uses Google Cloud's **Document AI Layout Parser** to extract detailed document structure.

**Workflow:**
1. **PDF Chunking:** Large PDFs are split into manageable chunks (100 pages each) for optimal processing
2. **Parallel Processing:** All chunks are processed simultaneously with Document AI
3. **Intelligent Merging:** Results are merged with global block re-indexing and cleanup
4. **Structure Preservation:** Maintains nested blocks, text positioning, and hierarchical relationships

**Output:** Unified layout structure (`doc_ai_layout_parser_merged.json`) with globally consistent block IDs.

---

#### **Phase 2.2: Context-Aware Block Grouping**
**Processor:** The **BlockGrouper** uses a deterministic three-step algorithm to assign content to Zielobjekt contexts.

**Algorithm:**
1. **Block Flattening:** All document blocks (including deeply nested structures) are flattened for consistent processing
2. **Marker Detection:** Searches for exact Zielobjekt identifiers (e.g., "AC-001", "SRV-002") as section markers
3. **Position-Based Grouping:** Content blocks between consecutive markers are systematically assigned to the appropriate Zielobjekt

**Key Advantages:**
- **Hierarchical Structure Preservation:** Maintains document's natural block hierarchy while enabling precise content grouping
- **Deep Nested Search:** Finds markers even when buried multiple levels deep in document structure
- **Deterministic Logic:** Uses document position to systematically assign content to correct sections
- **Ground Truth Validation:** Only searches for Zielobjekte that actually exist in the customer's system

**Output:** Grouped blocks file (`zielobjekt_grouped_blocks.json`) with content organized by Zielobjekt context.

---

#### **Phase 2.3: AI-Powered Requirement Extraction**
**Processor:** The **AiRefiner** transforms grouped raw layout blocks into structured security requirements using advanced AI processing with intelligent chunking.

**Smart Chunking Strategy:**
- **Adaptive Sizing:** Automatically splits large block groups (>300 blocks) into manageable chunks
- **Context Preservation:** 8% overlap between chunks maintains semantic continuity at boundaries
- **Dynamic Overlap:** Overlap size scales with chunk size (2-20 blocks) for optimal context retention
- **Boundary Optimization:** Prevents requirement fragmentation across chunk boundaries

**Robust Processing Features:**
- **Per-Kürzel Caching:** Individual results are cached to enable efficient reruns and recovery
- **Content Preprocessing:** Cleans problematic characters and truncates oversized text blocks
- **Automatic Recovery:** Failed chunks are automatically split and reprocessed
- **JSON Validation:** Built-in validation and repair for malformed AI responses
- **Parallel Processing:** Multiple Zielobjekt groups processed concurrently for speed

**Error Handling & Recovery:**
- **Token Limit Detection:** Automatically detects and handles token limit errors
- **Recursive Splitting:** Oversized chunks are recursively split until processable
- **Graceful Degradation:** Failed extractions don't block overall pipeline progress
- **Comprehensive Logging:** Detailed progress tracking for debugging and monitoring

**Performance Optimizations:**
- **Test Mode Limiting:** Processes only subset of data during development/testing
- **Efficient Caching:** Skips already-processed Zielobjekte in incremental runs
- **Memory Management:** Optimized for large document processing

**Output:** Structured requirements file (`extracted_grundschutz_check_merged.json`) containing all security requirements with Zielobjekt context, ready for downstream analysis stages.

#### **Phase 2.4 : Refinement and Structuring with Gemini 2.5**

The entity-based JSON from Document AI is now used as high-quality input for the LLM, which performs targeted refinement tasks rather than open-ended analysis.

1.  **Input:** For each Zielobjekt the blocks pertaining to it from Stage 1 (`zielobjekt_grouped_blocks.json`), and a predefined output schema (`3.6.1_extraction_schema.json`) are send to gemini. One request per Zielobjekt with only the context required to convert the blocks into JSON.
2.  **Prompting Strategy:** Gemini is given a highly specific prompt that instructs it to act as a data refiner, not a reader. The prompt includes:
    *   **System Instruction:** "You are an expert system for refining BSI Grundschutz data. Your input is a JSON of entities extracted by a form parser. Your task is to assemble these entities into a final, structured list of requirements."
    *   **Rules:**
        *   "Group related entities (ID, title, status, explanation) into a single requirement object."
        *   "Using the provided `system_map.json`, infer the correct `Zielobjekt` for each requirement based on its ID prefix (e.g., an ID starting with `SYS.1.1` belongs to the `SYS.1.1` Zielobjekt)."
        *   "Normalize minor OCR errors (e.g., 'ertluterung' becomes 'Erläuterung')."
        *   "Your output MUST be a single JSON object that strictly conforms to the provided `3.6.1_extraction_schema.json`."
3.  **Output:** The LLM produces the final, clean intermediate files:
    *   **`extracted_grundschutz_check_merged.json`**: A structured, de-duplicated, and contextually complete list of all security requirements. This file becomes the reliable source of truth for all subsequent audit analysis in Chapter 3 and Chapter 5.

This hybrid approach ensures maximum accuracy and traceability by using the right tool for each job: Document AI for structured extraction and Gemini for contextual refinement and formatting.


---

### **Phase 3: Final Report Assembly**

**Objective:** To deterministically merge all validated JSON stubs and the collected findings into the final audit report. This phase is handled by the `ReportGenerator` and is strictly non-AI to ensure final-stage reliability.

1.  **Load All Components:** The `ReportGenerator` loads the `master_report_template.json`, all individual stage result files (e.g., `Chapter-3.json`, `Chapter-4.json`), and the central `all_findings.json`.
2.  **Merge and Populate:** It systematically traverses the master template's structure and injects the content from the corresponding stage results.
3.  **Populate Findings Tables:** It specifically populates the deviation and recommendation tables in Chapter 7.2 by iterating through the `all_findings.json` file.
4.  **Save Final Report:** The fully assembled, validated JSON report is saved to `output/final_audit_report.json`.
