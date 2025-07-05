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
DOC_MAP_PATH = "output/document_map.json"

class EtlProcessor:
    """
    Extracts text from source documents, chunks it, generates embeddings,
    and uploads the formatted output for each document as a separate JSON
    file for Vertex AI Vector Search indexing. This process is idempotent,
    tracking completion status in GCS.
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

    def _load_asset_text(self, path: str) -> str:
        with open(path, 'r', encoding='utf-8') as f: return f.read()

    def _load_asset_json(self, path: str) -> dict:
        with open(path, 'r', encoding='utf-8') as f: return json.load(f)

    async def _classify_source_documents(self, filenames: List[str]) -> None:
        """
        Uses an AI model to classify source documents into predefined BSI categories
        based on their filenames. Saves the result to a map file in GCS.
        """
        logging.info("Starting AI-driven document classification based on filenames.")

        # Check if the map already exists to avoid reprocessing
        if self.gcs_client.blob_exists(DOC_MAP_PATH):
            logging.info(f"Document map already exists at '{DOC_MAP_PATH}'. Skipping classification.")
            return

        prompt_template = self._load_asset_text("assets/prompts/etl_classify_documents.txt")
        schema = self._load_asset_json("assets/schemas/etl_classify_documents_schema.json")
        
        # Format the list of filenames as a JSON string for the prompt
        filenames_json = json.dumps(filenames, indent=2)
        prompt = prompt_template.format(filenames_json=filenames_json)

        try:
            classification_result = await self.ai_client.generate_json_response(prompt, schema)
            
            # The result is the entire object, we just need to serialize it.
            self.gcs_client.upload_from_string(
                content=json.dumps(classification_result, indent=2, ensure_ascii=False),
                destination_blob_name=DOC_MAP_PATH
            )
            logging.info(f"Successfully created and saved document map to '{DOC_MAP_PATH}'.")
        except Exception as e:
            logging.error(f"Failed to classify source documents: {e}", exc_info=True)
            # We raise here because the map is critical for subsequent stages.
            raise

    def _extract_text_from_pdf(self, pdf_bytes: bytes, source_filename: str) -> str:
        """
        Extracts text content from a PDF file provided as bytes.

        Args:
            pdf_bytes: The byte content of the PDF file.
            source_filename: The original name of the file for logging purposes.

        Returns:
            The extracted text content as a single string.
        """
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
        """
        Removes special characters from a filename to create a valid GCS object name.

        Args:
            filename: The original filename, which may contain paths or special chars.

        Returns:
            A sanitized string suitable for use as a GCS object name.
        """
        # Get base name after the last '/'
        base_name = filename.split('/')[-1]
        # Replace invalid chars with underscores
        return re.sub(r'[^a-zA-Z0-9_.-]', '_', base_name)

    def _get_status_blob_path_base(self, source_blob_name: str) -> str:
        """
        Constructs the base path for the status marker blob, without an extension.

        Args:
            source_blob_name: The full name of the source document blob.

        Returns:
            The GCS path prefix for the corresponding status marker file.
        """
        sanitized_name = self._sanitize_filename(source_blob_name)
        return f"{self.config.etl_status_prefix}{sanitized_name}"

    def _process_single_document(self, blob: Any) -> None:
        """
        Runs the full ETL pipeline for a single source document from GCS.

        Args:
            blob: The GCS blob object representing the source document.
        """
        logging.info(f"Processing document: {blob.name}")
        status_blob_base = self._get_status_blob_path_base(blob.name)

        # 1. Extract
        file_bytes = self.gcs_client.download_blob_as_bytes(blob)
        if blob.name.lower().endswith(".pdf"):
            document_text = self._extract_text_from_pdf(file_bytes, blob.name)
        else:
            logging.warning(f"Skipping non-PDF file: {blob.name}")
            return
        
        if not document_text:
            logging.warning(f"No text extracted from {blob.name}. Skipping.")
            raise ValueError("No text could be extracted from document.")

        # 2. Chunk
        chunks = self.text_splitter.split_text(document_text)
        if not chunks:
            logging.warning(f"No chunks created for {blob.name}. Skipping.")
            raise ValueError("Document text could not be split into chunks.")
        logging.info(f"Created {len(chunks)} chunks for {blob.name}.")

        # 3. Embed
        success, embeddings = self.ai_client.get_embeddings(chunks)
        if not success or len(embeddings) != len(chunks):
            logging.error(f"Embedding generation failed for {blob.name}. Skipping document.")
            raise RuntimeError("Embedding generation failed.")

        # 4. Format and Upload
        logging.info(f"Formatting data for {blob.name}...")
        jsonl_content = ""
        for i, embedding_vector in enumerate(embeddings):
            record = {
                "id": str(uuid.uuid4()),
                "embedding": embedding_vector,
                "text_content": chunks[i],
                # The 'source_document' field must contain the full blob name for filtering.
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
        # Upon success, create the status marker file.
        self.gcs_client.upload_from_string(
            content="",
            destination_blob_name=f"{status_blob_base}.success",
            content_type='text/plain'
        )

        logging.info(f"Successfully uploaded embedding data for {blob.name} to gs://{self.config.bucket_name}/{output_path}")

    async def run(self) -> None:
        """
        Executes the main ETL pipeline. It first classifies all documents, then
        processes each new document, checking its status to ensure idempotency.
        """
        logging.info("Starting ETL run...")
        source_files = self.gcs_client.list_files()
        
        if not source_files:
            logging.warning("No source files found. ETL run is complete with no output.")
            return
            
        # Extract just the filenames for the classification step
        source_filenames = [blob.name for blob in source_files]
        
        # **NEW**: Run classification step first.
        await self._classify_source_documents(source_filenames)

        if self.config.is_test_mode:
            logging.warning(f"TEST MODE: Processing only the first {MAX_FILES_TEST_MODE} files for embedding.")
            source_files = source_files[:MAX_FILES_TEST_MODE]

        for blob in source_files:
            status_blob_base = self._get_status_blob_path_base(blob.name)
            success_marker = f"{status_blob_base}.success"
            failed_marker = f"{status_blob_base}.failed"
            
            if self.gcs_client.blob_exists(success_marker):
                logging.info(f"'.success' marker found for {blob.name}. Skipping.")
                continue
            
            if self.gcs_client.blob_exists(failed_marker):
                logging.warning(f"'.failed' marker found for {blob.name}. Skipping to prevent repeated errors.")
                continue

            try:
                self._process_single_document(blob)
            except Exception as e:
                logging.error(f"An unexpected error occurred while processing {blob.name}. Creating '.failed' marker. Error: {e}", exc_info=True)
                self.gcs_client.upload_from_string(
                    content=str(e),
                    destination_blob_name=failed_marker,
                    content_type='text/plain'
                )
        
        logging.info("ETL run finished.")