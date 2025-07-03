# src/etl/processor.py
import logging
import json
import uuid
from typing import List, Dict, Any
from langchain.text_splitter import RecursiveCharacterTextSplitter
import fitz  # PyMuPDF

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

        if self.config.is_test_mode:
            logging.warning("TEST MODE: Limiting processing to the first source file only.")
            source_files = source_files[:1]

        if not source_files:
            logging.warning("No source files found. Exiting ETL process.")
            return

        # 2. Transform Part 1: Chunk documents
        all_chunks = self._chunk_documents(source_files)
        if not all_chunks:
            logging.error("No chunks were created from the source documents.")
            return

        # 3. Transform Part 2: Generate embeddings
        chunk_texts = [chunk['text_content'] for chunk in all_chunks]
        embeddings = self.ai_client.get_embeddings(chunk_texts)

        # 4. Transform Part 3: Format data for Vertex AI Index
        jsonl_content = self._format_for_indexing(all_chunks, embeddings)
        
        # 5. Load: Upload the final file to GCS
        # CRITICAL FIX: The destination path must match the Terraform configuration
        # for the Vertex AI Index's `contents_delta_uri`.
        destination_path = "vector_index_data/embeddings.jsonl"
        self.gcs_client.upload_from_string(jsonl_content, destination_path)
        
        logging.info(f"ETL process complete. Index data uploaded to gs://{self.config.bucket_name}/{destination_path}")

    def _chunk_documents(self, source_files: List[Any]) -> List[Dict[str, Any]]:
        """Reads files from GCS, extracts text with PyMuPDF, and chunks them."""
        all_chunks_with_metadata = []
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP
        )

        for blob in source_files:
            # We only process PDFs for now
            if not blob.name.lower().endswith(".pdf"):
                logging.info(f"Skipping non-PDF file: {blob.name}")
                continue

            logging.info(f"Processing document: {blob.name}")
            try:
                # Read the PDF file directly from GCS bytes into memory
                pdf_bytes = self.gcs_client.download_blob_as_bytes(blob)
                doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                
                # Extract text from all pages and concatenate
                full_text = "".join(page.get_text() for page in doc)
                doc.close()

                if not full_text.strip():
                    logging.warning(f"No text extracted from {blob.name}. It may be an image-only PDF.")
                    continue

                # Use LangChain to split the single large text string
                chunks = text_splitter.split_text(full_text)
                
                for i, chunk_text in enumerate(chunks):
                    # Create a readable, deterministic ID for better traceability
                    clean_filename = blob.name.split('/')[-1].replace('.pdf', '')
                    chunk_id = f"{clean_filename}_chunk_{i:04d}"
                    all_chunks_with_metadata.append({
                        "id": chunk_id,
                        "source_document": blob.name,
                        "chunk_index": i,
                        "text_content": chunk_text
                    })
                logging.info(f"Created {len(chunks)} chunks for {blob.name}")

            except Exception as e:
                logging.error(f"Failed to process {blob.name}: {e}", exc_info=True)
                continue # Move to the next file

        return all_chunks_with_metadata

    def _format_for_indexing(self, chunks: List[Dict], embeddings: List[List[float]]) -> str:
        """
        Formats chunks and embeddings into a rich JSONL format.
        This file serves two purposes:
        1. As input for the Vertex AI Vector Search indexer (which uses 'id', 'embedding', 'restricts').
        2. As the data source for our application's RAG lookup map (which uses 'id' and 'text_content').
        """
        jsonl_lines = []
        for chunk, embedding in zip(chunks, embeddings):
            json_obj = {
                # --- Data for BOTH Indexing and RAG Lookup ---
                "id": chunk['id'],
                "embedding": embedding,
                
                # --- Data exclusively for RAG Lookup (ignored by indexer) ---
                "text_content": chunk['text_content'],
                "source_document": chunk['source_document'],
                
                # --- Data exclusively for Vertex AI Indexing ---
                "restricts": [{
                    "namespace": "source_document",
                    "allow": [chunk['source_document']]
                }]
            }
            jsonl_lines.append(json.dumps(json_obj))
        
        return "\n".join(jsonl_lines)