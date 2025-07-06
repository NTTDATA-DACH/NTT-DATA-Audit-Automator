# src/clients/rag_client.py
import logging
import json
import os
from typing import List, Dict, Any

from google.cloud import aiplatform
from google.cloud.aiplatform.matching_engine import MatchingEngineIndexEndpoint
from google.cloud.aiplatform.matching_engine.matching_engine_index_endpoint import (Namespace,)
from google.cloud.aiplatform_v1.types import IndexDatapoint
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

    def get_context_for_query(self, queries: List[str], source_categories: List[str] = None) -> str:
        """
        Finds relevant document chunks for a list of queries. It queries for each,
        filters by similarity, de-duplicates the results, and returns a single
        consolidated context string.
        """
        try:
            # 1. Embed all queries in a single batch call.
            success, query_vectors = self.ai_client.get_embeddings(queries)
            if not success or not query_vectors:
                logging.error("Failed to generate embeddings for the RAG queries.")
                return "Error: Could not generate embeddings for queries."

            if self.config.is_test_mode:
                logging.info(f"TEST_MODE_LOG: Generated {len(query_vectors)} query embedding vectors.")

            # 2. Build the filter if categories are provided
            filter_restriction = None
            if source_categories and self._document_category_map:
                allow_list_filenames = [
                    filename
                    for category in source_categories
                    for filename in self._document_category_map.get(category, [])
                ]
                
                if allow_list_filenames:
                    # when embedding, the filenames are transformed in a funny way, for the filter to work,
                    # we need to have a string EXACTLY like them!
                    filenames_string = "["
                    for i, fname in enumerate(allow_list_filenames):
                        escaped   = os.path.basename(fname)                           # keep just the file name
                        
                        escaped    = json.dumps(escaped, ensure_ascii=True)[1:-1]     # \u-escape non-ASCII chars
                        logging.warning(f"ESCAPED 1: {escaped}")
                        #escaped    = escaped.replace(r" ", "_")
                        escaped    = escaped.replace(r"\\\\", "\\")
                        escaped   = f"source_documents/{escaped}"                    # add the folder prefix
                        logging.warning(f"ESCAPED 2: {escaped}")
                        filenames_string += f"'{escaped}',"
                        
                    filenames_string += "]"
                    
                    if self.config.is_test_mode:
                        logging.info(f"TEST_MODE_LOG: Filtering search to the following files: {filenames_string}")
                    
                    filter_restriction = Namespace(                # ✅ helper dataclass
                        name="source_document",                    # the metadata key you indexed
                        allow_tokens=filenames_string,         # the values to keep
                        # deny_tokens=[]                           # optional
                    )
                else:
                    logging.warning(f"No documents found for categories: {source_categories}. Searching all documents.")

            # 3. Perform the search for all queries at once
            response = None
            try:
                response = self.index_endpoint.find_neighbors(
                    deployed_index_id="bsi_deployed_index_kunde_x",
                    queries=query_vectors,
                    num_neighbors=NEIGHBOR_POOL_SIZE,
                    filter=[filter_restriction] if filter_restriction else []
                )
                if self.config.is_test_mode and response:
                    logging.info(f"TEST_MODE_LOG: find_neighbors API call successful. Received results for {len(response)} queries.")
            except api_core_exceptions.GoogleAPICallError as e:
                logging.error(f"Vector search API call failed with code {e.code}: {e.message}", exc_info=self.config.is_test_mode)
                return f"Error: Vector search API call failed. See logs for details."
            except Exception as e:
                logging.error(f"An unexpected error occurred during vector search: {e}", exc_info=True)
                return "Error: Unexpected error during vector search."

            # 4. Process and aggregate the response from all queries
            if not response:
                logging.warning("Vector DB query returned no response object.")
                return "No relevant context found in the documents."

            unique_neighbors: Dict[str, Any] = {}
            for neighbor_list_for_query in response:
                if not neighbor_list_for_query: continue
                quality_neighbors = [n for n in neighbor_list_for_query if n.distance <= SIMILARITY_THRESHOLD]
                for neighbor in quality_neighbors:
                    if neighbor.id not in unique_neighbors:
                        unique_neighbors[neighbor.id] = neighbor
            
            if not unique_neighbors:
                logging.warning(f"No neighbors met the similarity threshold of {SIMILARITY_THRESHOLD}. Consider adjusting the query or the threshold.")
                return "No highly relevant context found in the documents."
            
            logging.info(f"Aggregated {len(unique_neighbors)} unique, high-quality neighbors across all queries.")

            context_str = ""
            sorted_neighbors = sorted(list(unique_neighbors.values()), key=lambda n: n.distance)
            for neighbor in sorted_neighbors:
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