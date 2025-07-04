# src/clients/rag_client.py
import logging
import json
from typing import List, Dict, Any

# Corrected import for the IndexEndpoint class
from google.cloud import aiplatform
from google.cloud.aiplatform.matching_engine import MatchingEngineIndexEndpoint

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.ai_client import AiClient

class RagClient:
    """Client for Retrieval-Augmented Generation using Vertex AI Vector Search."""

    def __init__(self, config: AppConfig, gcs_client: GcsClient, ai_client: AiClient):
        self.config = config
        self.gcs_client = gcs_client
        self.ai_client = ai_client # Inject the AI client

        aiplatform.init(
            project=config.gcp_project_id,
            location=config.vertex_ai_region
        )

        # Instantiate the class, providing the full context required to find the resource.
        self.index_endpoint = MatchingEngineIndexEndpoint(
            index_endpoint_name=self.config.index_endpoint_id,
        )
        logging.info(f"RAG Client connected to Index Endpoint: {self.config.index_endpoint_id}")

        # This lookup map is the key to retrieving text from a chunk ID.
        self._chunk_lookup_map = self._load_chunk_lookup_map()

    def _load_chunk_lookup_map(self) -> Dict[str, str]:
        """
        Downloads all embedding batch files from GCS and creates a mapping from
        chunk ID to its text content for fast lookups.
        """
        lookup_map = {}
        logging.info("Building chunk ID to text lookup map from all batch files...")

        try:
            # Use the GCS client to find all embedding files
            embedding_blobs = self.gcs_client.list_files(prefix="vector_index_data/")

            for blob in embedding_blobs:
                # Skip the placeholder file created by Terraform
                if "placeholder.json" in blob.name:
                    continue

                logging.debug(f"Processing batch file for lookup map: {blob.name}")
                jsonl_content = self.gcs_client.read_text_file(blob.name)
                
                for line in jsonl_content.strip().split('\n'):
                    try:
                        data = json.loads(line)
                        chunk_id = data.get("id")
                        chunk_text = data.get("text_content")
                        if chunk_id and chunk_text:
                            lookup_map[chunk_id] = f"-- CONTEXT FROM CHUNK {chunk_id} --\n{chunk_text}\n\n"
                    except json.JSONDecodeError:
                        logging.warning(f"Skipping invalid JSON line in {blob.name}: '{line}'")
                        continue
            
            logging.info(f"Successfully built lookup map with {len(lookup_map)} entries.")
            return lookup_map
        except Exception as e:
            logging.error(f"Failed to build chunk lookup map from batch files: {e}", exc_info=True)
            return {}

    def get_context_for_query(self, query: str, num_neighbors: int = 5) -> str:
        """
        Finds the most relevant document chunks for a query and returns their text.

        Args:
            query: The question or topic to search for.
            num_neighbors: The number of relevant chunks to retrieve.

        Returns:
            A single string containing the concatenated text of all found chunks.
        """
        
        context_str = ""
        try:
            # 1. Embed the text query into a numerical vector first.
            success, embeddings = self.ai_client.get_embeddings([query])
            if not success or not embeddings:
                logging.error("Failed to generate embedding for the RAG query.")
                return "Error: Could not generate embedding for query."
            
            if self.config.is_test_mode:
                logging.info(f"RAG_CLIENT_TEST_MODE: Sending query to vector DB: '{query}'")

            query_vector = embeddings[0]

            # 2. Use the numerical vector to find neighbors.
            response = self.index_endpoint.find_neighbors(
                deployed_index_id="bsi_deployed_index_kunde_x", # This must match the deployment
                queries=[query_vector],
                num_neighbors=num_neighbors,
            )

            if response and response[0]:
                neighbors = response[0]
                if self.config.is_test_mode:
                    neighbor_details = [f"(id={n.id}, dist={n.distance:.4f})" for n in neighbors]
                    logging.info(f"RAG_CLIENT_TEST_MODE: Found {len(neighbors)} neighbors: {', '.join(neighbor_details)}")

                for neighbor in neighbors:
                    chunk_id = neighbor.id
                    chunk_text = self._chunk_lookup_map.get(chunk_id)
                    if chunk_text:
                        context_str += chunk_text
                    else:
                        logging.warning(f"Could not find text for chunk ID: {chunk_id}")
                
                if self.config.is_test_mode:
                    logging.info(f"RAG_CLIENT_TEST_MODE: Final retrieved context string length: {len(context_str)} chars.")

                return context_str
            else:
                logging.warning("Vector DB query returned no neighbors.")
                return "No relevant context found in the documents."
                
        except Exception as e:
            logging.error(f"Error querying Vector DB: {e}", exc_info=True)
            return "Error retrieving context from Vector DB."