# src/etl/processor.py
import logging
import json
import uuid
from pathlib import Path
import tempfile
from typing import List, Dict, Any
from langchain.text_splitter import RecursiveCharacterTextSplitter

# The UnstructuredFileLoader is deprecated. Use the new UnstructuredLoader instead.
from langchain_unstructured import UnstructuredLoader
from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.ai_client import AiClient

# Constants for ETL processing
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 100

class EtlProcessor:
    """Orchestrates the ETL process for the RAG pipeline."""

    def __init__(self, config: AppConfig, gcs_client: GcsClient, ai_client: AiClient):
        self.config = config
        self.gcs_client = gcs_client
        self.ai_client = ai_client
        logging.info("ETL Processor initialized.")

    def run(self):
        """Executes the full ETL pipeline."""
        # 1. Extract: Get file list from GCS
        source_files = self.gcs_client.list_source_files()
        if not source_files:
            logging.warning("No source files found. Exiting ETL process.")
            return

        # Use a temporary directory to avoid polluting the local filesystem
        with tempfile.TemporaryDirectory() as temp_dir:
            logging.info(f"Created temporary directory for processing: {temp_dir}")
            # 2. Transform Part 1: Chunk documents
            all_chunks = self._chunk_documents(source_files, temp_dir)
            if not all_chunks:
                logging.error("No chunks were created from the source documents.")
                return

        # 3. Transform Part 2: Generate embeddings
        chunk_texts = [chunk['text'] for chunk in all_chunks]
        embeddings = self.ai_client.get_embeddings(chunk_texts)

        # 4. Transform Part 3: Format data for Vertex AI Index
        jsonl_content = self._format_for_indexing(all_chunks, embeddings)
        
        # 5. Load: Upload the final file to GCS
        destination_path = f"{self.config.customer_id}/vector_index_data/embeddings.jsonl"
        self.gcs_client.upload_from_string(jsonl_content, destination_path)
        
        logging.info(f"ETL process complete. Index data uploaded to gs://{self.config.bucket_name}/{destination_path}")

    def _chunk_documents(self, source_files: List[Any], temp_dir: str) -> List[Dict[str, Any]]:
        """Reads files from GCS, chunks them, and adds metadata."""
        all_chunks_with_metadata = []
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP
        )
        temp_path = Path(temp_dir)

        for blob in source_files:
            logging.info(f"Processing document: {blob.name}")
            try:
                local_file_path = temp_path / Path(blob.name).name
                with open(local_file_path, "wb") as temp_file:
                    temp_file.write(self.gcs_client.download_blob_as_bytes(blob))
                
                # Use the modern, non-deprecated loader
                loader = UnstructuredLoader(str(local_file_path))
                docs = loader.load()
                
                chunks = text_splitter.split_documents(docs)
                
                for i, chunk in enumerate(chunks):
                    all_chunks_with_metadata.append({
                        "id": str(uuid.uuid4()), # A unique ID for each chunk
                        "source_document": blob.name,
                        "text": chunk.page_content
                    })
                logging.info(f"Created {len(chunks)} chunks for {blob.name}")
            except Exception as e:
                logging.error(f"Failed to process {blob.name}: {e}", exc_info=True)
                continue # Move to the next file

        return all_chunks_with_metadata

    def _format_for_indexing(self, chunks: List[Dict], embeddings: List[List[float]]) -> str:
        """Formats the chunks and embeddings into the required JSONL format."""
        jsonl_lines = []
        for chunk, embedding in zip(chunks, embeddings):
            json_obj = {
                "id": chunk['id'],
                "embedding": embedding,
                # Restricts allow for metadata filtering during search
                "restricts": [{
                    "namespace": "source_document",
                    "allow": [chunk['source_document']]
                }]
            }
            jsonl_lines.append(json.dumps(json_obj))
        
        return "\n".join(jsonl_lines)