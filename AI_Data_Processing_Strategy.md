### AI Data Processing Strategy

The strategy is a two-phase process designed to maximize accuracy while ensuring the process is efficient and auditable.

*   **Phase 0: Knowledge Base Generation.** A one-time, upfront process where the AI reads all customer-provided source documents and transforms them into a single, structured, and validated JSON object. This becomes the "single source of truth" for the audit.
*   **Phase 1: Staged Audit Analysis.** The main audit process, where each stage uses the generated Knowledge Base as its primary context to perform its specific analysis against BSI standards.

---

### **Phase 0: Knowledge Base Generation**

This initial phase is the most important AI interaction. Its sole purpose is to convert unstructured customer documentation into a structured and comprehensive **Customer Knowledge Base (KB)**.

**Objective:**
To create a single `customer_knowledge_base.json` file that contains all relevant entities (IT systems, processes, policies, locations, etc.) extracted from the customer's source documents.

**Process Steps:**
1.  **List Source Files:** The `gcs_client` will list all customer documents (PDFs, DOCX, etc.) located in the `SOURCE_PREFIX`.
2.  **Prepare Multi-modal Prompt:** The `AuditController` (or a dedicated "Phase 0" module) will construct a multi-modal prompt for the `ai_client`. This prompt will contain:
    *   The content of the `initial_analysis_prompt.txt`.
    *   The list of GCS URIs for all customer documents.
3.  **Invoke Gemini 2.5 Pro:** The `ai_client` will make a single, asynchronous call to `client.aio.models.generate_content`. We will pass all document URIs directly to the model, taking full advantage of its large context window and multi-modal capabilities.
4.  **Enforce Structured Output:** The API call will include the `knowledge_base_schema.json` within the prompt and set `response_mime_type="application/json"` to ensure the model's output is structured JSON.
5.  **Validate and Save:** The `ai_client` will receive the JSON response. It will validate this output against the `knowledge_base_schema.json`. Upon successful validation, the `gcs_client` will save the resulting object as `customer_knowledge_base.json` in the `OUTPUT_PREFIX`.

**Key Components:**

**1. The Prompt (`assets/prompts/initial_analysis_prompt.txt`)**
This prompt will be engineered to guide the model precisely.

```text
You are an expert BSI Grundschutz and ISO 27001 security auditor. Your task is to perform an initial, comprehensive analysis of all customer documents provided below.

Read every document carefully and extract all entities relevant to a BSI Grundschutz audit. Consolidate your findings into a single, structured JSON object that strictly adheres to the provided JSON schema.

Your primary goal is to build a complete and accurate knowledge base.
- Identify all IT systems, applications, business processes, physical locations, network connections, and external service providers.
- For each entity, extract as much detail as possible, such as names, descriptions, owners, and relationships to other entities.
- If information for a specific field is not found in the documents, use a `null` value or an empty array `[]` as defined in the schema. Do not invent information.

The customer documents are located at the following GCS URIs:
[GCS_URI_1]
[GCS_URI_2]
...

Your output MUST be a single JSON object conforming to this schema:
```

**2. The Schema (`assets/schemas/knowledge_base_schema.json`)**
This schema is the quality gate. It will be detailed and precise, forcing the model to structure its output correctly.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "CustomerKnowledgeBase",
  "description": "A structured representation of a customer's environment for a BSI Grundschutz audit.",
  "type": "object",
  "properties": {
    "security_policies": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "document_name": {"type": "string"},
          "policy_title": {"type": "string"},
          "summary": {"type": "string"}
        },
        "required": ["document_name", "policy_title"]
      }
    },
    "business_processes": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "process_id": {"type": "string"},
          "process_name": {"type": "string"},
          "description": {"type": "string"},
          "owner": {"type": "string"},
          "related_applications": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["process_id", "process_name"]
      }
    },
    "it_systems": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "system_name": {"type": "string"},
          "type": {"enum": ["Server", "Client", "NetworkDevice", "Other"]},
          "os": {"type": "string"},
          "location": {"type": "string"},
          "description": {"type": "string"}
        },
        "required": ["system_name", "type"]
      }
    },
    "locations": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "location_name": {"type": "string"},
          "address": {"type": "string"},
          "is_data_center": {"type": "boolean"}
        },
        "required": ["location_name"]
      }
    }
  },
  "required": ["security_policies", "business_processes", "it_systems", "locations"]
}
```

---

### **Phase 1: Staged Audit (Leveraging the Knowledge Base)**

Once the `customer_knowledge_base.json` is created, it becomes the immutable context for the rest of the audit.

**Objective:**
To execute each audit stage efficiently by providing the AI with pre-processed, structured context, eliminating the need to re-read source documents.

**Process:**
For each stage of the audit (e.g., section "3.3.3 Liste der Geschäftsprozesse" from the *Muster_Auditbericht*):
1.  The `AuditController` loads the `customer_knowledge_base.json`.
2.  It constructs a prompt specific to that stage, including:
    *   The BSI requirements for that topic.
    *   The structure and questions from the *Muster_Auditbericht*.
    *   **The entire content of the `customer_knowledge_base.json`**.
    *   The JSON schema defining the expected output for *this specific stage*.
3.  The `ai_client` is called. Because the AI has the full context in a clean format, its task is simplified to *analysis and comparison* rather than extraction and structuring.
4.  The validated JSON "stub" for that stage is saved to GCS.

### **Rationale and Benefits**

*   **Efficiency:** We perform the heavy lifting of document parsing once. Each subsequent audit stage is faster and cheaper, as it operates on a much smaller, structured data object.
*   **Consistency:** Every audit stage works from the exact same understanding of the customer's environment, preventing contradictions.
*   **Reliability:** The schema-driven approach is used at both the macro (KB generation) and micro (stage generation) levels, ensuring data quality and structure throughout.
*   **Modularity:** This strategy perfectly separates the complex, one-time task of **data extraction** from the repeatable task of **audit analysis**.