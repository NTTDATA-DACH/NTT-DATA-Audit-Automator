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

## **Phase 2: Ground-Truth-Driven Semantic Chunking (Document AI + Gemini)**

The goal is to convert the semi-structured `Grundschutz-Check` PDF into structured JSON requirements grouped by Zielobjekte, using a four-phase pipeline that combines deterministic logic with AI-powered semantic analysis.

**Architecture:** Modular pipeline with specialized processors:
- **GroundTruthMapper**: Creates authoritative system structure from customer documents
- **DocumentProcessor**: Handles Document AI Layout Parser workflow
- **BlockGrouper**: Groups layout blocks by Zielobjekt using marker-based algorithm  
- **AiRefiner**: Extracts structured requirements with intelligent chunking and model fallback

---

### **Phase 2.1: Ground Truth Establishment**
**GroundTruthMapper** extracts authoritative system structure using targeted AI calls:
- **Zielobjekte** from `Strukturanalyse` (A.1) 
- **Baustein-to-Zielobjekt mappings** from `Modellierung` (A.3)

**Output:** `system_structure_map.json` - the foundation for all subsequent processing.

### **Phase 2.2: Document Layout Extraction** 
**DocumentProcessor** uses Document AI Layout Parser:
1. **PDF Chunking:** Split into 100-page chunks for optimal processing
2. **Parallel Processing:** All chunks processed simultaneously
3. **Intelligent Merging:** Global block re-indexing with structure preservation

**Output:** `merged_layout_parser_result.json` with consistent block hierarchy.

### **Phase 2.3: Semantic Block Grouping**
**BlockGrouper** uses deterministic marker-based algorithm:
1. **Marker Detection:** Finds exact Zielobjekt identifiers as section markers
2. **Position-Based Assignment:** Content between markers assigned to appropriate Zielobjekt
3. **Hierarchical Preservation:** Maintains document's natural block structure

**Output:** `zielobjekt_grouped_blocks.json` with content organized by context.

### **Phase 2.4: AI-Powered Semantic Extraction**
**AiRefiner** transforms grouped blocks into structured requirements:

**Smart Chunking:**
- **Adaptive Sizing:** Auto-splits large groups (>200 blocks) with 10% overlap
- **Context Continuity:** Overlap prevents requirement fragmentation at boundaries
- **Model Fallback:** Flash-lite → Ground Truth model on JSON parsing failures

**Robust Processing:**
- **Per-Zielobjekt Caching:** Individual results cached for efficient reruns
- **Content Preprocessing:** Cleans problematic characters, truncates oversized blocks  
- **Deduplication:** Quality-based selection when overlapping chunks create duplicates
- **Parallel Processing:** Multiple Zielobjekte processed concurrently

**Output:** `extracted_grundschutz_check_merged.json` - structured, deduplicated requirements ready for audit analysis in Chapters 3 and 5.

---

### **Phase 3: Final Report Assembly**

**Objective:** To deterministically merge all validated JSON stubs and the collected findings into the final audit report. This phase is handled by the `ReportGenerator` and is strictly non-AI to ensure final-stage reliability.

1.  **Load All Components:** The `ReportGenerator` loads the `master_report_template.json`, all individual stage result files (e.g., `Chapter-3.json`, `Chapter-4.json`), and the central `all_findings.json`.
2.  **Merge and Populate:** It systematically traverses the master template's structure and injects the content from the corresponding stage results.
3.  **Populate Findings Tables:** It specifically populates the deviation and recommendation tables in Chapter 7.2 by iterating through the `all_findings.json` file.
4.  **Save Final Report:** The fully assembled, validated JSON report is saved to `output/final_audit_report.json`.

## **Phase 1: Staged AI-Driven Analysis & Report Generation**

The audit generation follows a structured approach where each chapter has specialized logic combining AI analysis with deterministic processing.

---

### **Chapter 3: Dokumentenprüfung (Document Review)**

**Approach:** Dynamic execution plan with hybrid AI/deterministic logic and custom processing for complex analysis.

**Key Features:**
- **Template-Driven Planning:** Execution plan auto-generated from `master_report_template.json` structure
- **Multi-Modal Processing:** 
  - **AI-Driven Subchapters:** Standard document analysis with source category filtering
  - **Custom Logic:** Complex analysis for IT-Grundschutz-Check using pre-computed data
  - **Summary Sections:** Consolidates findings from dependent subchapters

**IT-Grundschutz-Check Analysis (3.6.1):**
- **Data Source:** Uses pre-extracted `extracted_grundschutz_check_merged.json` from Phase 2
- **Hybrid Logic:** Combines deterministic checks with targeted AI analysis
- **Coverage Validation:** Ensures all modeled Zielobjekte have corresponding requirements
- **Quality Assessments:** 
  - Status coverage completeness
  - MUSS-requirement compliance (using BSI Control Catalog)
  - Date recency validation (<12 months)
  - Cross-reference with Risikoanalyse and Realisierungsplan

**Output:** Comprehensive document review with structured findings and compliance assessment.

---

### **Chapter 4: Erstellung eines Prüfplans (Audit Planning)**

**Approach:** Ground-truth constrained AI planning with audit-type-specific logic.

**Key Features:**
- **Audit Type Configuration:** Different Baustein selection based on `AUDIT_TYPE` environment variable
  - `Zertifizierungsaudit` → 4.1.1 (Initial certification, minimum 6 Bausteine)
  - `1. Überwachungsaudit` → 4.1.2 (First surveillance, minimum 3 Bausteine) 
  - `2. Überwachungsaudit` → 4.1.3 (Second surveillance, avoid repeats from first)
- **Ground Truth Integration:** Injects complete `system_structure_map.json` into AI prompts
- **Compliance Enforcement:** AI instructed to only select valid Baustein-Zielobjekt combinations
- **Mixed Generation:** 
  - **AI-Driven:** Baustein selection and risk analysis measures
  - **Deterministic:** Standard location selection

**Critical Constraint:** Every selected Baustein must be paired with a Zielobjekt Kürzel that it's actually mapped to in the ground truth, preventing downstream errors in Chapter 5.

**Output:** Realistic, compliant audit plan based on customer's actual system structure.

---

### **Chapter 5: Vor-Ort-Audit (On-Site Audit)**

**Approach:** Deterministic checklist generation consuming data from previous stages.

**Key Features:**
- **Data Integration:** Combines Chapter 4 plan + ground truth map + extracted check data
- **Robust Zielobjekt Mapping:** Uses Kürzel from audit plan (not fragile name lookups)
- **Control Checklist Generation (5.5.2):**
  - Loads BSI Control Catalog for each planned Baustein
  - Enriches with customer implementation details from extracted data
  - Creates structured audit checklist with empty fields for manual completion
- **Risk Analysis Checklist (5.6.2):** 
  - Processes measures selected in Chapter 4.1.5
  - Generates verification checklist for additional risk controls

**Data Flow:**
1. **Chapter 4 Results** → Selected Bausteine and Zielobjekte for audit
2. **BSI Control Catalog** → Standard requirements for each Baustein  
3. **Extracted Check Data** → Customer's current implementation status and explanations
4. **Output** → Pre-populated audit checklists ready for on-site verification

**Output:** Comprehensive manual audit checklists with customer context pre-filled.

---

### **Chapter 7: Anhang (Appendix)**

**Approach:** Deterministic file listing with centralized findings integration.

**Key Features:**
- **Reference Documents (7.1):** Auto-generated from GCS source file listing
- **Findings Integration (7.2):** Populated by `ReportGenerator` from central `all_findings.json`

**Process:**
1. **File Discovery:** Lists all source documents from GCS with metadata
2. **Document Table:** Creates structured reference with version info and change notes
3. **Findings Consolidation:** `ReportGenerator` handles centralized finding population with:
   - **Categorical Sorting:** AG (Minor), AS (Major), E (Recommendations)
   - **Numerical Ordering:** Findings sorted by ID number within category
   - **Status Tracking:** Preserves status from previous audits or sets defaults

**Output:** Complete appendix with document references and consolidated findings tables.

---

### **Cross-Chapter Integration**

**Central Findings Collection:**
- All stages contribute structured findings to `all_findings.json`
- `AuditController` manages finding ID assignment and deduplication
- Previous audit findings preserved with existing IDs
- New findings get sequential IDs per category

**State Management:**
- Each chapter result saved independently to GCS
- Resumable execution with dependency checking
- Force overwrite capability for development iterations

**Report Assembly:**
- `ReportGenerator` combines all stage outputs into final audit report
- Schema validation ensures compliance with BSI audit structure
- Non-destructive updates preserve baseline data from previous reports