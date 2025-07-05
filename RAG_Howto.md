# Implementing a High-Precision RAG with Vertex AI: A Step-by-Step Guide

### **1. Introduction: From Broad Searches to Precision Retrieval**

The core value of our Vector Database (VDB) is its ability to function as a semantic search engine for the customer's documentation. The initial RAG approach was to search this entire database for every query. While functional, this can lead to imprecise results, as the AI receives a mix of highly relevant and only vaguely related evidence, forcing it to guess.

To achieve superior accuracy and auditability, we evolve this strategy. Instead of searching *everything*, we will implement **scoped searching**. For each audit question, we will first determine the *type* of document that should contain the answer (e.g., "Netzplan", "Sicherheitsleitlinie") and instruct our RAG client to **only search within those specific files**.

This is accomplished by adding a new "Document Classification" step to our ETL pipeline, which uses the AI to categorize every source document. This pre-filtering dramatically reduces noise, provides higher-quality context to the AI, and leads to more precise, evidence-based findings.

### **2. The High-Precision RAG Workflow**

The process for a single audit task now follows these more intelligent steps:

1.  **(ETL Phase) Classify Documents:** At the start of the ETL run, an AI-powered process analyzes all source filenames and creates a `document_map.json`, mapping each file to a BSI category (e.g., `{"filename": "HiSolutions_Netzplan_v4.5.pdf", "category": "Netzplan"}`).
2.  **Define a Scoped Query:** The stage runner defines a clear question *and* the document categories it expects the answer to be in (e.g., Question: "Is the network plan current?", Categories: `['Netzplan']`).
3.  **Filter and Search the VDB:** The query is sent to our `RagClient`. The client uses the `document_map.json` to get the specific filenames for the requested categories and tells the Vector Search to restrict its search to only those documents.
4.  **Retrieve Relevant Chunks:** The Vector Search returns the most semantically similar text chunks *from within the filtered document set*.
5.  **Construct a Contextual Prompt:** A rich prompt is built, containing the original question and the high-quality, targeted evidence retrieved.
6.  **Generate a Grounded Response:** This context-rich prompt is sent to the Gemini model, which synthesizes an answer based on the focused evidence.

### **3. Detailed Implementation Steps**

Here is the technical breakdown of how to implement this high-precision workflow.

#### **Prerequisite: AI-Powered Document Classification in ETL**

The foundation of this strategy is the `document_map.json` file. This is generated during the ETL phase by the `EtlProcessor`, which uses a dedicated prompt and schema to classify documents by their filenames. The `RagClient` depends on this file existing.

#### **Step 1: The `RagClient` Loads the Category Map**

The `RagClient`'s first task is to load both the chunk-to-text lookup map and the new document category map into memory.

```python
# src/clients/rag_client.py

import logging
import json
from google.cloud import aiplatform
from google.cloud.exceptions import NotFound
# ... other imports

DOC_MAP_PATH = "output/document_map.json"

class RagClient:
    def __init__(self, config: AppConfig, gcs_client: GcsClient, ai_client: AiClient):
        # ... (standard initializations) ...
        self._chunk_lookup_map = self._load_chunk_lookup_map()
        # NEW: Load the document category map.
        self._document_category_map = self._load_document_category_map()

    def _load_document_category_map(self) -> Dict[str, str]:
        """
        Loads the document classification map from GCS, creating a lookup from
        a category (e.g., "Netzplan") to a list of its associated filenames.
        """
        logging.info(f"Loading document category map from '{DOC_MAP_PATH}'...")
        category_map = {}
        try:
            map_data = self.gcs_client.read_json(DOC_MAP_PATH)
            doc_map_list = map_data.get("document_map", [])
            for item in doc_map_list:
                category = item.get("category")
                filename = item.get("filename")
                if category and filename:
                    if category not in category_map:
                        category_map[category] = []
                    category_map[category].append(filename)
            
            logging.info(f"Successfully built document category map with {len(category_map)} categories.")
            return category_map
        except NotFound:
            logging.error(f"CRITICAL: Document map file not found. ETL must be run first.")
            raise
        # ... error handling ...
```

#### **Step 2: Execute a Filtered Query**

The core search method, `get_context_for_query`, is updated to accept a list of `source_categories`. It uses this to build a filter for the Vector Search API call.

```python
# In src/clients/rag_client.py, within the RagClient class

    def get_context_for_query(self, query: str, num_neighbors: int = 5, source_categories: List[str] = None) -> str:
        """
        Finds relevant document chunks, optionally filtering the search
        to specific document categories for higher precision.
        """
        try:
            # 1. Embed the text query into a numerical vector first.
            success, embeddings = self.ai_client.get_embeddings([query])
            if not success or not embeddings:
                logging.error("Failed to generate embedding for the RAG query.")
                return "Error: Could not generate embedding for query."
            
            query_vector = embeddings[0]

            # 2. NEW: Build the filter based on the requested categories.
            filters = None
            if source_categories and self._document_category_map:
                # Collect all filenames belonging to the requested categories.
                allow_list_filenames = []
                for category in source_categories:
                    filenames = self._document_category_map.get(category, [])
                    allow_list_filenames.extend(filenames)
                
                if allow_list_filenames:
                    logging.info(f"Applying search filter for categories: {source_categories}")
                    # Create a restriction filter for the vector search.
                    filters = aiplatform.IndexDatapoint.Restriction(
                        namespace="source_document", # Must match the key in the index data
                        allow_list=allow_list_filenames
                    )
            
            # 3. Use the vector and filter to find neighbors.
            response = self.index_endpoint.find_neighbors(
                deployed_index_id="bsi_deployed_index_kunde_x",
                queries=[query_vector],
                num_neighbors=num_neighbors,
                # The find_neighbors call expects a list of restriction objects.
                filter=[filters] if filters else []
            )

            # ... (rest of the logic to retrieve text content remains the same) ...

        except Exception as e:
            logging.error(f"Error querying Vector DB: {e}", exc_info=True)
            return "Error retrieving context from Vector DB."

```

#### **Step 3: Putting It All Together in a Stage Runner**

The stage runners are updated to use this new capability. Their subchapter definitions now include natural language questions for `rag_query` and a list of `source_categories` to search within.

```python
# Example from src/audit/stages/stage_3_dokumentenpruefung.py

class Chapter3Runner:
    def _load_subchapter_definitions(self) -> Dict[str, Any]:
        """Loads definitions including specific RAG queries and source categories."""
        return {
            "bereinigterNetzplan": {
                "key": "3.3.2",
                "prompt_path": "assets/prompts/stage_3_3_2_netzplan.txt",
                "schema_path": "assets/schemas/stage_3_3_2_netzplan_schema.json",
                # The query is now a clear, natural language question.
                "rag_query": "Liegt ein aktueller und vollständiger Netzplan vor und sind alle Komponenten korrekt bezeichnet?",
                # The search is now scoped to only documents classified as "Netzplan".
                "source_categories": ["Netzplan"]
            },
            # ... other subchapter definitions
        }

    async def _process_rag_subchapter(self, name: str, definition: dict) -> Dict[str, Any]:
        """Generates content for a single subchapter using the scoped RAG pipeline."""
        
        # ... (load prompt and schema) ...

        # The call to the RagClient now includes the categories for filtering.
        context_evidence = self.rag_client.get_context_for_query(
            query=definition["rag_query"],
            source_categories=definition.get("source_categories")
        )
        
        prompt = prompt_template.format(context=context_evidence)

        # ... (call AI and return result) ...
