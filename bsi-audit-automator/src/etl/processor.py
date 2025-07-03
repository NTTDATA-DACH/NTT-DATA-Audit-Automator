# src/etl/processor.py
import logging
import json
import uuid
import re
from typing import List, Dict, Any
import fitz  # PyMuPDF

from langchain.text_splitter import RecursiveCharacterTextSplitter

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.ai_client import AiClient

# Constants for ETL processing
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
MAX_FILES_TEST_MODE = 3
VECTOR_INDEX_PREFIX = "vector_index_data/"

class EtlProcessor:
    """
    Extracts text from source documents, chunks it, generates embeddings,
    and uploads the formatted output for each document as a separate JSON
    file for Vertex AI Vector Search indexing.
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

    def _sanitize_filename(self, filename: str) -> str:
        """Removes special characters to create a valid GCS object name."""
        # Get base name after the last '/'
        base_name = filename.split('/')[-1]
        # Replace invalid chars with underscores
        return re.sub(r'[^a-zA-Z0-9_.-]', '_', base_name)

    def _process_single_document(self, blob):
        """Runs the full ETL pipeline for a single GCS blob."""
        logging.info(f"Processing document: {blob.name}")
        
        # 1. Extract
        file_bytes = self.gcs_client.download_blob_as_bytes(blob)
        if blob.name.lower().endswith(".pdf"):
            document_text = self._extract_text_from_pdf(file_bytes, blob.name)
        else:
            logging.warning(f"Skipping non-PDF file: {blob.name}")
            return
        
        if not document_text:
            logging.warning(f"No text extracted from {blob.name}. Skipping.")
            return

        # 2. Chunk
        chunks = self.text_splitter.split_text(document_text)
        if not chunks:
            logging.warning(f"No chunks created for {blob.name}. Skipping.")
            return
        logging.info(f"Created {len(chunks)} chunks for {blob.name}.")

        # 3. Embed
        success, embeddings = self.ai_client.get_embeddings(chunks)
        if not success or len(embeddings) != len(chunks):
            logging.error(f"Embedding generation failed for {blob.name}. Skipping document.")
            return

        # 4. Format and Upload
        logging.info(f"Formatting data for {blob.name}...")
        jsonl_content = ""
        for i, embedding_vector in enumerate(embeddings):
            record = {
                "id": str(uuid.uuid4()),
                "embedding": embedding_vector,
                "text_content": chunks[i],
                "source_document": blob.name
            }
            jsonl_content += json.dumps(record) + "\n"
        
        output_filename = self._sanitize_filename(blob.name) + ".json"
        output_path = f"{VECTOR_INDEX_PREFIX}{output_filename}"
        
        self.gcs_client.upload_from_string(
            content=jsonl_content,
            destination_blob_name=output_path,
            content_type='application/json'
        )
        logging.info(f"Successfully uploaded embedding data for {blob.name} to gs://{self.config.bucket_name}/{output_path}")

    def run(self):
        """
        Executes the ETL pipeline, processing each source document
        and saving its embeddings to a separate file.
        """
        logging.info("Starting ETL run...")
        source_files = self.gcs_client.list_files()

        if self.config.is_test_mode:
            logging.warning(f"TEST MODE: Processing only the first {MAX_FILES_TEST_MODE} files.")
            source_files = source_files[:MAX_FILES_TEST_MODE]

        if not source_files:
            logging.warning("No source files found. ETL run is complete with no output.")
            return

        for blob in source_files:
            try:
                self._process_single_document(blob)
            except Exception as e:
                logging.error(f"An unexpected error occurred while processing {blob.name}. Skipping. Error: {e}", exc_info=True)
        
        logging.info("ETL run finished.")