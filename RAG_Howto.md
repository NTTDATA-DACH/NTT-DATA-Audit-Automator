# Implementing RAG with Vertex AI Vector Search: A Step-by-Step Guide

### **1. Introduction: From Static Prompts to Evidence-Based Auditing**

The core value of the Vector Database (VDB) we created in the ETL phase is its ability to function as a highly efficient, semantic search engine for the customer's entire documentation set.

Currently, our audit stage runners ask the AI questions in a vacuum. The AI has no access to the customer's specific policies, network diagrams, or process documents. This forces it to "guess" or "hallucinate" answers based on its general training.

By implementing the RAG pattern, we will change this workflow entirely. For every question we ask, we will first retrieve the most relevant snippets of text from the customer's documentation and provide them to the AI as **evidence**. The AI's new task is not to *answer from memory*, but to *synthesize an answer based on the provided context*.

This approach provides two immense benefits:
1.  **Accuracy & Reduced Hallucination:** The model's answers are grounded in the customer's actual documents, drastically increasing factual accuracy.
2.  **Auditability & Traceability:** For every finding the AI generates, we can log exactly which document chunks were used as its source of information. This is a powerful feature for validating the audit report.

### **2. The RAG Workflow at a Glance**

The process for a single audit task (e.g., answering one question in a subchapter) will now follow these steps:

1.  **Formulate a Query:** The stage runner defines a clear, specific question or topic to investigate (e.g., "What is the process for user de-provisioning?").
2.  **Search the VDB:** This query is sent to our Vertex AI Vector Search endpoint.
3.  **Retrieve Chunk IDs:** The endpoint returns a list of IDs for the document chunks that are most semantically similar to our query.
4.  **Fetch Full Text:** We use the retrieved IDs to look up the full text of these chunks from our source data.
5.  **Construct a Contextual Prompt:** A new, rich prompt is built, containing the original question AND the full text of the retrieved chunks as evidence.
6.  **Generate a Grounded Response:** This context-rich prompt is sent to the Gemini model, which now provides an answer based on the supplied evidence.

### **3. Detailed Implementation Steps**

Here is the technical breakdown of how to implement this workflow using Python and the `google-genai` / `google-cloud-aiplatform` libraries.

#### **Prerequisite: A New Client Module**

To keep our code clean, we will encapsulate the RAG logic in a new client module. Let's imagine a new file: `src/clients/rag_client.py`.

#### **Step 1: Connect to the Vertex AI Index Endpoint**

First, we need a client that can communicate with the deployed Vector Search endpoint. This requires the `google-cloud-aiplatform` SDK.

```python
# src/clients/rag_client.py (or a new RAG-specific client)

import logging
from google.cloud import aiplatform
from src.config import AppConfig

class RagClient:
    def __init__(self, config: AppConfig):
        self.config = config
        
        # Initialize the AI Platform client with the correct region
        aiplatform.init(
            project=config.gcp_project_id,
            location=config.vertex_ai_region
        )
        
        # Get a reference to the specific Index Endpoint
        self.index_endpoint = aiplatform.IndexEndpoint(
            index_endpoint_name=self.config.index_endpoint_id,
            project=self.config.gcp_project_id,
            location=self.config.vertex_ai_region
        )
        logging.info(f"RAG Client connected to Index Endpoint: {self.index_endpoint.display_name}")

        # Placeholder for the chunk lookup map (see Step 3)
        self._chunk_lookup_map = self._load_chunk_lookup_map()
```

#### **Step 2: Execute a Query ("Find Neighbors")**

The core of the search operation is the `find_neighbors` method. It takes our text query, converts it to an embedding internally (using the same model as our ETL process), and finds the most similar vectors in the index.

```python
# In src/clients/rag_client.py, within the RagClient class

    def find_relevant_chunks(self, query: str, num_neighbors: int = 5) -> list[dict]:
        """
        Queries the vector index to find the most relevant document chunks.

        Args:
            query: The question or topic to search for.
            num_neighbors: The number of relevant chunks to retrieve.

        Returns:
            A list of neighbor objects, each containing the 'id' of the chunk.
        """
        logging.info(f"Querying VDB for: '{query}'")
        try:
            # The 'find_neighbors' method handles embedding the query string
            response = self.index_endpoint.find_neighbors(
                # The ID of the index deployed to this endpoint. This is static.
                deployed_index_id="bsi_deployed_index_kunde_x",
                queries=[query],
                num_neighbors=num_neighbors,
            )
            
            if response and response[0]:
                neighbors = response[0]
                logging.info(f"Found {len(neighbors)} relevant chunks.")
                return neighbors
            else:
                logging.warning("VDB query returned no neighbors.")
                return []
                
        except Exception as e:
            logging.error(f"Error querying Vector DB: {e}", exc_info=True)
            raise

```

#### **Step 3: Retrieve the Original Text Content**

The `find_neighbors` response gives us a list of matching chunk IDs (e.g., `['uuid-123', 'uuid-456']`) and their distance scores. It **does not** give us the text itself. We need a way to look up the text using the ID.

The simplest and most effective way to do this is to load the `embeddings.jsonl` file we created during ETL and build a lookup dictionary.

```python
# In src/clients/rag_client.py, within the RagClient class

    def _load_chunk_lookup_map(self) -> dict[str, str]:
        """
        Downloads the embeddings.jsonl file from GCS and creates a
        mapping from chunk ID to its text content.
        This is a crucial step for the 'Retrieval' part of RAG.
        """
        import json
        # This requires adding the GcsClient to the RagClient
        # self.gcs_client = GcsClient(self.config) # Add this to __init__
        
        lookup_map = {}
        try:
            logging.info("Building chunk ID to text lookup map...")
            jsonl_content = self.gcs_client.read_text_file(
                "vector_index_data/embeddings.jsonl"
            )
            for line in jsonl_content.strip().split('\n'):
                data = json.loads(line)
                # The ETL process must have saved the text alongside the embedding ID
                chunk_id = data.get("id")
                chunk_text = data.get("text_content") # IMPORTANT: We need to modify the ETL to save this!
                if chunk_id and chunk_text:
                    lookup_map[chunk_id] = chunk_text
            
            logging.info(f"Successfully built lookup map with {len(lookup_map)} entries.")
            return lookup_map
        except Exception as e:
            logging.error(f"Failed to build chunk lookup map: {e}", exc_info=True)
            return {}

    def get_text_for_chunks(self, neighbors: list[dict]) -> str:
        """
        Looks up the full text for a list of neighbor chunks.

        Args:
            neighbors: A list of neighbor objects from find_neighbors().

        Returns:
            A single string containing the concatenated text of all found chunks.
        """
        context_str = ""
        for neighbor in neighbors:
            chunk_id = neighbor.id
            chunk_text = self._chunk_lookup_map.get(chunk_id)
            if chunk_text:
                # Add metadata for auditability
                context_str += f"--- CONTEXT FROM DOCUMENT CHUNK {chunk_id} ---\n"
                context_str += f"{chunk_text}\n\n"
            else:
                logging.warning(f"Could not find text for chunk ID: {chunk_id}")
        return context_str
```
**Note:** This reveals a necessary modification: our ETL process in `etl/processor.py` must be updated to save the `text` content in the `embeddings.jsonl` file alongside the `id` and `embedding`.

#### **Step 4: Putting It All Together in a Stage Runner**

Now we can see how a stage runner, like for Chapter 3.2, would use this new RAG capability.

```python
# Example of a refactored subchapter processing function
# in src/audit/stages/stage_3_dokumentenpruefung.py

    async def _process_single_subchapter(self, name: str, definition: dict) -> Dict[str, Any]:
        """Generates content for a single subchapter using RAG."""
        logging.info(f"Starting RAG generation for subchapter: {definition['key']} ({name})")
        
        # The prompt template now needs a {context} placeholder
        prompt_template = self._load_asset_text(definition["prompt_path"])
        schema = self._load_asset_json(definition["schema_path"])

        # 1. Formulate a specific query for this subchapter
        # This could be derived from the subchapter title or a dedicated field.
        rag_query = f"Analyse der Dokumente bezüglich: {definition['title']}"

        # 2. Use the RagClient to find relevant evidence
        # (Assuming rag_client is passed to the runner's __init__)
        relevant_neighbors = self.rag_client.find_relevant_chunks(rag_query)
        
        # 3. Retrieve the full text of the evidence
        context_evidence = self.rag_client.get_text_for_chunks(relevant_neighbors)

        if not context_evidence:
            logging.warning(f"No context found for subchapter {definition['key']}. Proceeding without evidence.")
            context_evidence = "No specific context could be retrieved from the provided documents."

        # 4. Construct the final, context-rich prompt
        final_prompt = prompt_template.format(
            customer_id=self.config.customer_id,
            context=context_evidence # Inject the retrieved evidence
        )

        try:
            # 5. Call the AI with the grounded prompt
            generated_data = await self.ai_client.generate_json_response(final_prompt, schema)
            
            # 6. (Optional but recommended) Add sources to the output for traceability
            generated_data['evidence_sources'] = [n.id for n in relevant_neighbors]

            logging.info(f"Successfully generated data for subchapter {definition['key']}")
            return {name: generated_data}
        except Exception as e:
            logging.error(f"Failed to generate data for subchapter {definition['key']}: {e}", exc_info=True)
            return {name: None}
```

This updated workflow demonstrates how the embeddings are used to find relevant information, which is then used to ground the AI's response, leading to a far more accurate and auditable result that aligns with the project's architectural goals.