# src/clients/rag_client.py
import logging
import json
import os
from typing import List, Dict, Any

from google.cloud import aiplatform
from google.cloud.aiplatform.matching_engine import MatchingEngineIndexEndpoint
from google.cloud.exceptions import NotFound
from google.api_core import exceptions as api_core_exceptions

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.ai_client import AiClient

DOC_MAP_PATH = "output/document_map.json"
SIMILARITY_THRESHOLD = 0.95
NEIGHBOR_POOL_SIZE = 15


class RagClient:
    """Client for Retrieval-Augmented Generation using Vertex AI Vector Search."""

    def __init__(self, config: AppConfig, gcs_client: GcsClient, ai_client: AiClient):
        self.config = config
        self.gcs_client = gcs_client
        self.ai_client = ai_client

        aiplatform.init(
            project=config.gcp_project_id,
            location=config.vertex_ai_region
        )

        if config.index_endpoint_public_domain:
            logging.info(f"Connecting to PUBLIC Vector Search Endpoint: {config.index_endpoint_public_domain}")
            self.index_endpoint = MatchingEngineIndexEndpoint.from_public_endpoint(
                project_id=config.gcp_project_id,
                region=config.vertex_ai_region,
                public_endpoint_domain_name=config.index_endpoint_public_domain
            )
        else:
            logging.info(f"Connecting to PRIVATE Vector Search Endpoint: {config.index_endpoint_id}")
            self.index_endpoint = MatchingEngineIndexEndpoint(
                index_endpoint_name=self.config.index_endpoint_id,
            )

        self._chunk_lookup_map = self._load_chunk_lookup_map()
        self._document_category_map = self._load_document_category_map()

    def _load_document_category_map(self) -> Dict[str, str]:
        """
        Loads the document classification map from GCS. The map provides a
        lookup from document category to a list of filenames.
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
            logging.error(f"CRITICAL: Document map file not found at '{DOC_MAP_PATH}'. The ETL process must be run first.")
            raise
        except Exception as e:
            logging.error(f"Failed to build document category map: {e}", exc_info=True)
            return {}

    def _load_chunk_lookup_map(self) -> Dict[str, Dict[str, str]]:
        """
        Downloads all embedding batch files from GCS and creates a mapping from
        chunk ID to its text content and source document for fast lookups.
        """
        lookup_map: Dict[str, Dict[str, str]] = {}
        logging.info("Building chunk ID to text lookup map from all batch files...")

        try:
            embedding_blobs = self.gcs_client.list_files(prefix="vector_index_data/")

            for blob in embedding_blobs:
                if "placeholder.json" in blob.name:
                    continue

                jsonl_content = self.gcs_client.read_text_file(blob.name)
                
                for line in jsonl_content.strip().split('\n'):
                    try:
                        data = json.loads(line)
                        chunk_id = data.get("id")
                        chunk_text = data.get("text_content")
                        source_doc = data.get("source_document")
                        if chunk_id and chunk_text and source_doc:
                            lookup_map[chunk_id] = {
                                "text_content": chunk_text,
                                "source_document": source_doc
                            }
                    except json.JSONDecodeError:
                        logging.warning(f"Skipping invalid JSON line in {blob.name}: '{line}'")
                        continue
            
            logging.info(f"Successfully built lookup map with {len(lookup_map)} entries.")
            return lookup_map
        except Exception as e:
            logging.error(f"Failed to build chunk lookup map from batch files: {e}", exc_info=True)
            return {}

    def get_context_for_query(self, query: str, source_categories: List[str] = None) -> str:
        """
        Finds relevant document chunks for a query, with enhanced, specific error handling
        around the Vector Search API call.
        """
        try:
            # 1. Embed the query (now a synchronous call)
            success, embeddings = self.ai_client.get_embeddings([query])
            if not success or not embeddings:
                logging.error("Failed to generate embedding for the RAG query.")
                return "Error: Could not generate embedding for query."
            
            query_vector = embeddings[0]
            if self.config.is_test_mode:
                logging.info(f"TEST_MODE_LOG: Generated query embedding vector with {len(query_vector)} dimensions.")


            # 2. Build the filter if categories are provided
            filter_restriction = None
            if source_categories and self._document_category_map:
                allow_list_filenames = [
                    filename
                    for category in source_categories
                    for filename in self._document_category_map.get(category, [])
                ]
                
                if allow_list_filenames:
                    basenames = [os.path.basename(f) for f in allow_list_filenames]
                    logging.info(f"Applying search filter for categories: {source_categories} ({len(basenames)} files)")
                    if self.config.is_test_mode:
                        logging.info(f"TEST_MODE_LOG: Filtering search to the following {len(basenames)} files: {basenames}")
                    filter_restriction = aiplatform.IndexDatapoint.Restriction(
                        namespace="source_document", allow_list=allow_list_filenames
                    )
                else:
                    logging.warning(f"No documents found for categories: {source_categories}. Searching all documents.")

            # 3. Perform the search with specific error handling
            response = None
            try:
                response = self.index_endpoint.find_neighbors(
                    deployed_index_id="bsi_deployed_index_kunde_x",
                    queries=[query_vector],
                    num_neighbors=NEIGHBOR_POOL_SIZE,
                    filter=[filter_restriction] if filter_restriction else []
                )
                if self.config.is_test_mode and response and response[0]:
                    logging.info(f"TEST_MODE_LOG: find_neighbors API call successful. Received {len(response[0])} raw neighbors.")
            except api_core_exceptions.GoogleAPICallError as e:
                logging.error(f"Vector search API call failed with code {e.code}: {e.message}", exc_info=self.config.is_test_mode)
                return f"Error: Vector search API call failed. See logs for details."
            except Exception as e:
                logging.error(f"An unexpected error occurred during vector search: {e}", exc_info=True)
                return "Error: Unexpected error during vector search."

            # 4. Process the response
            if not response or not response[0]:
                logging.warning("Vector DB query returned no initial neighbors.")
                return "No relevant context found in the documents."

            all_neighbors = response[0]
            quality_neighbors = [n for n in all_neighbors if n.distance <= SIMILARITY_THRESHOLD]
            
            logging.info(f"Retrieved {len(all_neighbors)} initial neighbors. Found {len(quality_neighbors)} that meet similarity threshold (<= {SIMILARITY_THRESHOLD}).")

            if not quality_neighbors:
                logging.warning(f"No neighbors met the similarity threshold of {SIMILARITY_THRESHOLD}. Consider adjusting the query or the threshold.")
                return "No highly relevant context found in the documents."

            context_str = ""
            for neighbor in quality_neighbors:
                chunk_id = neighbor.id
                context_info = self._chunk_lookup_map.get(chunk_id)
                if context_info:
                    context_str += f"-- CONTEXT FROM DOCUMENT: {os.path.basename(context_info['source_document'])} (Similarity: {1-neighbor.distance:.2%}) --\n"
                    context_str += f"{context_info['text_content']}\n\n"
                else:
                    logging.warning(f"Could not find text for chunk ID: {chunk_id}")
            
            return context_str.strip()

        except Exception as e:
            logging.error(f"A general error occurred in get_context_for_query: {e}", exc_info=True)
            return "Error retrieving context from Vector DB."