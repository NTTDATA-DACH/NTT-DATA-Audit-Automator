# src/etl/processor.py
import logging
import json
import uuid
import io
from typing import List, Dict, Any
import fitz # PyMuPDF

from langchain.text_splitter import RecursiveCharacterTextSplitter

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.ai_client import AiClient

# Constants for ETL processing
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
MAX_FILES_TEST_MODE = 3

class EtlProcessor:
    """
    Extracts text from source documents, chunks it, generates embeddings,
    and formats the output for Vertex AI Vector Search indexing.
    """
    def __init__(self, config: AppConfig, gcs_client: GcsClient, ai_client: AiClient):
        self.config = config
        self.gcs_client = gcs_client
        self.ai_client = ai_client
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            length_function=len,
        )
        logging.info("ETL Processor initialized.")

    def _extract_text_from_pdf(self, pdf_bytes: bytes, source_filename: str) -> str:
        """Extracts text content from a PDF file in memory."""
        text_content = ""
        try:
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                for page_num, page in enumerate(doc):
                    text_content += f"--- Page {page_num + 1} of {source_filename} ---\n"
                    text_content += page.get_text() + "\n\n"
            return text_content
        except Exception as e:
            logging.error(f"Failed to extract text from {source_filename}: {e}", exc_info=True)
            return ""

    def run(self):
        """
        Executes the full ETL pipeline:
        1. Lists source files from GCS.
        2. Extracts text and chunks it.
        3. Generates embeddings for all chunks.
        4. Uploads the formatted JSONL to GCS.
        """
        logging.info("Starting ETL run...")
        source_files = self.gcs_client.list_source_files()

        if self.config.is_test_mode:
            logging.warning(f"TEST MODE: Processing only the first {MAX_FILES_TEST_MODE} files.")
            source_files = source_files[:MAX_FILES_TEST_MODE]
        
        if not source_files:
            logging.warning("No source files found. ETL run is complete with no output.")
            return

        all_chunks_text = []
        chunk_metadata = []

        # 1. & 2. Extract and Chunk
        for blob in source_files:
            logging.info(f"Processing document: {blob.name}")
            file_bytes = self.gcs_client.download_blob_as_bytes(blob)
            
            if blob.name.lower().endswith(".pdf"):
                document_text = self._extract_text_from_pdf(file_bytes, blob.name)
            else:
                logging.warning(f"Skipping non-PDF file: {blob.name}")
                continue

            if not document_text:
                logging.warning(f"No text extracted from {blob.name}. Skipping.")
                continue

            chunks = self.text_splitter.split_text(document_text)
            for i, chunk_text in enumerate(chunks):
                all_chunks_text.append(chunk_text)
                chunk_metadata.append({
                    "source_document": blob.name,
                    "chunk_index": i
                })
            logging.info(f"Created {len(chunks)} chunks for {blob.name}.")

        if not all_chunks_text:
            logging.error("No text chunks were created from any source document. Aborting.")
            return

        # 3. Generate Embeddings
        success, embeddings = self.ai_client.get_embeddings(all_chunks_text)
        if not success or len(embeddings) != len(all_chunks_text):
            logging.critical("Embedding generation failed or returned incomplete results. Aborting ETL.")
            raise RuntimeError("Failed to generate embeddings for all chunks.")

        # 4. Format and Upload
        logging.info("Formatting data for Vertex AI Vector Search...")
        jsonl_content = ""
        for i, embedding_vector in enumerate(embeddings):
            record = {
                "id": str(uuid.uuid4()),
                "embedding": embedding_vector,
                # Add metadata required by our RAG client for context retrieval
                "text_content": all_chunks_text[i],
                "source_document": chunk_metadata[i]["source_document"]
            }
            jsonl_content += json.dumps(record) + "\n"

        output_path = "vector_index_data/embeddings.json"
        self.gcs_client.upload_from_string(
            content=jsonl_content,
            destination_blob_name=output_path,
            content_type='application/json' 
        )
        logging.info(f"Successfully uploaded embedding data to gs://{self.config.bucket_name}/{output_path}")
        logging.info("ETL run finished.")