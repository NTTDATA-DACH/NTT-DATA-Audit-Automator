# src/clients/rag_client.py
import logging
import json
from typing import List, Dict, Any

from google.cloud import aiplatform
from google.cloud.aiplatform.matching_engine import MatchingEngineIndexEndpoint
from google.cloud.aiplatform.matching_engine.matching_engine_index_endpoint import Namespace

from google.cloud.exceptions import NotFound

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.ai_client import AiClient

DOC_MAP_PATH = "output/document_map.json"
# **NEW**: Constants for dynamic context filtering
SIMILARITY_THRESHOLD = 0.7  # Lower is stricter. Cosine distance of 0.7 is a reasonable starting point.
NEIGHBOR_POOL_SIZE = 10     # Fetch a larger pool of candidates to filter from.


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

    def get_context_for_query(self, queries: List[str], source_categories: List[str] = None) -> str:
        """
        Finds relevant document chunks for a list of queries. It queries for each,
        filters by similarity, de-duplicates the results, and returns a single
        consolidated context string.
        """
        if self.config.is_test_mode:
            logging.info(f"RAG_CLIENT_TEST_MODE: Sending {len(queries)} queries to vector DB.")

        context_str = ""
        try:
            # 1. Embed all queries in a single batch call.
            success, query_vectors = self.ai_client.get_embeddings(queries)
            if not success or not query_vectors:
                logging.error("Failed to generate embeddings for the RAG queries.")
                return "Error: Could not generate embeddings for queries."

            # 2. Build the filter once.
            search_filters: List[Namespace] = []
            if source_categories and self._document_category_map:
                allow_list_filenames = []
                for category in source_categories:
                    filenames = self._document_category_map.get(category, [])
                    allow_list_filenames.extend(filenames)
                if allow_list_filenames:
                    logging.info(f"Applying search filter for categories: {source_categories} ({len(allow_list_filenames)} files)")
                    namespace_filter = Namespace(name="source_document", allow_tokens=allow_list_filenames)
                    search_filters.append(namespace_filter)
                else:
                    logging.warning(f"No documents found for categories: {source_categories}. Searching all documents.")

            # 3. Gather unique, high-quality neighbors from all queries.
            unique_neighbors: Dict[str, Any] = {}
            for i, query_vector in enumerate(query_vectors):
                logging.info(f"Executing search for query {i+1}/{len(queries)}...")
                response = self.index_endpoint.find_neighbors(
                    deployed_index_id="bsi_deployed_index_kunde_x",
                    queries=[query_vector],
                    num_neighbors=NEIGHBOR_POOL_SIZE,
                    filter=search_filters
                )

                if not response or not response[0]:
                    logging.warning(f"Query {i+1} returned no initial neighbors.")
                    continue

                all_neighbors = response[0]
                quality_neighbors = [n for n in all_neighbors if n.distance <= SIMILARITY_THRESHOLD]
                
                logging.info(f"Query {i+1}: Filtered {len(all_neighbors)} neighbors down to {len(quality_neighbors)} by similarity.")
                
                for neighbor in quality_neighbors:
                    if neighbor.id not in unique_neighbors:
                        unique_neighbors[neighbor.id] = neighbor

            # 4. Build the final context string from the unique neighbors.
            if not unique_neighbors:
                logging.warning("No neighbors from any query met the similarity threshold.")
                return "No highly relevant context found in the documents."
            
            logging.info(f"Found {len(unique_neighbors)} unique, high-quality chunks across all queries.")
            for chunk_id, neighbor in unique_neighbors.items():
                context_info = self._chunk_lookup_map.get(chunk_id)
                if context_info:
                    context_str += f"-- CONTEXT FROM DOCUMENT: {context_info['source_document']} (Similarity: {1-neighbor.distance:.2%}) --\n"
                    context_str += f"{context_info['text_content']}\n\n"
                else:
                    logging.warning(f"Could not find text for chunk ID: {chunk_id}")
            
            return context_str

        except Exception as e:
            logging.error(f"Error querying Vector DB: {e}", exc_info=True)
            return "Error retrieving context from Vector DB."