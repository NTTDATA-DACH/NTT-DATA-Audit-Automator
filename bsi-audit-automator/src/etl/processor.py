# src/etl/processor.py
import logging
import json
import uuid
import re
import time
from typing import List, Dict, Any
import fitz  # PyMuPDF

from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.docstore.document import Document

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.ai_client import AiClient

# Constants for ETL processing
CHUNK_SIZE = 350
CHUNK_OVERLAP = 70
MAX_FILES_TEST_MODE = 3
VECTOR_INDEX_PREFIX = "vector_index_data/"
DOC_MAP_PATH = "output/document_map.json"
EMBEDDING_DIMENSIONS = 3072

class EtlProcessor:
    """
    Extracts text from source documents, cleans and chunks it, generates embeddings,
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
        logging.info(f"ETL Processor initialized with chunk size {CHUNK_SIZE} and overlap {CHUNK_OVERLAP}.")

    def _load_asset_text(self, path: str) -> str:
        with open(path, 'r', encoding='utf-8') as f: return f.read()

    def _load_asset_json(self, path: str) -> dict:
        with open(path, 'r', encoding='utf-8') as f: return json.load(f)

    async def _classify_source_documents(self, filenames: List[str]) -> None:
        """
        Uses an AI model to classify source documents into predefined BSI categories
        based on their filenames. Saves the result to a map file in GCS.
        Falls back to classifying all documents as 'Sonstiges' on failure.
        """
        logging.info("Starting AI-driven document classification based on filenames.")

        if self.gcs_client.blob_exists(DOC_MAP_PATH):
            logging.info(f"Document map already exists at '{DOC_MAP_PATH}'. Skipping classification.")
            return

        prompt_template = self._load_asset_text("assets/prompts/etl_classify_documents.txt")
        schema = self._load_asset_json("assets/schemas/etl_classify_documents_schema.json")
        
        filenames_json = json.dumps(filenames, indent=2)
        prompt = prompt_template.format(filenames_json=filenames_json)

        try:
            classification_result = await self.ai_client.generate_json_response(prompt, schema)
            content_to_upload = json.dumps(classification_result, indent=2, ensure_ascii=False)
            logging.info("Successfully created document map via AI.")
        except Exception as e:
            logging.critical(
                f"AI-driven document classification failed: {e}. "
                f"Creating a fallback map with all documents as 'Sonstiges'. "
                "RAG filtering will be impaired.",
                exc_info=True
            )
            fallback_map = {"document_map": [{"filename": fname, "category": "Sonstiges"} for fname in filenames]}
            content_to_upload = json.dumps(fallback_map, indent=2, ensure_ascii=False)
        
        self.gcs_client.upload_from_string(
            content=content_to_upload,
            destination_blob_name=DOC_MAP_PATH
        )
        logging.info(f"Saved document map to '{DOC_MAP_PATH}'.")

    def _clean_text(self, text: str) -> str:
        """
        Removes common non-informative text patterns and reduces excessive whitespace.
        """
        text = re.sub(r'Page\s+\d+', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def _extract_text_from_pdf(self, pdf_bytes: bytes, source_filename: str) -> List[Document]:
        """
        Extracts text from a PDF and returns it as a list of LangChain
        Document objects, one per page, with cleaned text and metadata.
        """
        documents = []
        try:
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                for page_num, page in enumerate(doc):
                    page_text = page.get_text()
                    cleaned_text = self._clean_text(page_text)
                    if cleaned_text:
                        documents.append(Document(
                            page_content=cleaned_text,
                            metadata={
                                "source_document": source_filename,
                                "page_number": page_num + 1
                            }
                        ))
            return documents
        except Exception as e:
            logging.error(f"Failed to extract text from {source_filename}: {e}", exc_info=True)
            return []

    def _sanitize_filename(self, filename: str) -> str: 
        """
        Removes special characters from a filename to create a valid GCS object name.
        """
        base_name = filename.split('/')[-1]
        return re.sub(r'[^a-zA-Z0-9_.-]', '_', base_name)

    def _get_status_blob_path_base(self, source_blob_name: str) -> str:
        """
        Constructs the base path for the status marker blob, without an extension.
        """
        sanitized_name = self._sanitize_filename(source_blob_name)
        return f"{self.config.etl_status_prefix}{sanitized_name}"

    def _process_single_document(self, blob: Any) -> None:
        """
        Runs the full ETL pipeline for a single source document from GCS, including
        extraction, cleaning, chunking, embedding, and uploading. This method is
        designed to be self-contained and idempotent.
        """
        start_time = time.time()
        logging.info(f"Processing document: {blob.name}")
        status_blob_base = self._get_status_blob_path_base(blob.name)

        try:
            # 1. Extract and Clean
            file_bytes = self.gcs_client.download_blob_as_bytes(blob)
            if not blob.name.lower().endswith(".pdf"):
                logging.warning(f"Skipping non-PDF file: {blob.name}")
                return
            
            documents = self._extract_text_from_pdf(file_bytes, blob.name)
            if not documents:
                raise ValueError("No text could be extracted from document.")

            # 2. Chunk
            chunks = self.text_splitter.split_documents(documents)
            if not chunks:
                raise ValueError("Document text could not be split into chunks.")
            logging.info(f"Created {len(chunks)} chunks for {blob.name}.")

            # 3. Embed
            chunk_contents = [chunk.page_content for chunk in chunks]
            success, embeddings = self.ai_client.get_embeddings(chunk_contents)
            if not success or len(embeddings) != len(chunks):
                raise RuntimeError(f"Embedding failed: Got {len(embeddings)} embeddings for {len(chunks)} chunks.")

            # 4. Validate Embeddings
            for i, emb in enumerate(embeddings):
                if len(emb) != EMBEDDING_DIMENSIONS:
                    raise ValueError(f"Invalid embedding dimension for chunk {i}. Expected {EMBEDDING_DIMENSIONS}, got {len(emb)}.")

            # 5. Format and Upload
            logging.info(f"Formatting data for {blob.name}...")
            jsonl_lines = [
                json.dumps({
                    "id": str(uuid.uuid4()),
                    "embedding": embeddings[i],
                    "text_content": chunk.page_content,
                    "source_document": chunk.metadata["source_document"],
                    "page_number": chunk.metadata["page_number"]
                }) for i, chunk in enumerate(chunks)
            ]
            jsonl_content = "\n".join(jsonl_lines)
            
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
            duration = time.time() - start_time
            logging.info(f"Successfully processed {blob.name} in {duration:.2f} seconds.")

        except Exception as e:
            logging.error(f"Processing failed for {blob.name}. Creating '.failed' marker.", exc_info=True)
            self.gcs_client.upload_from_string(
                content=str(e),
                destination_blob_name=f"{status_blob_base}.failed",
                content_type='text/plain'
            )

    async def run(self) -> None:
        """
        Executes the main ETL pipeline. It first classifies all documents, then
        processes each new document sequentially, checking its status to ensure idempotency.
        """
        logging.info("Starting ETL run...")
        source_files = self.gcs_client.list_files()
        
        if not source_files:
            logging.warning("No source files found. ETL run is complete with no output.")
            return
            
        source_filenames = [blob.name for blob in source_files]
        await self._classify_source_documents(source_filenames)

        files_to_process = source_files
        if self.config.is_test_mode:
            logging.warning(f"TEST MODE: Limiting processing to first {MAX_FILES_TEST_MODE} files.")
            files_to_process = source_files[:MAX_FILES_TEST_MODE]

        processed_count = 0
        for blob in files_to_process:
            status_blob_base = self._get_status_blob_path_base(blob.name)
            if self.gcs_client.blob_exists(f"{status_blob_base}.success"):
                logging.info(f"Skipping already processed file: {blob.name}")
                continue
            if self.gcs_client.blob_exists(f"{status_blob_base}.failed"):
                logging.warning(f"Skipping previously failed file: {blob.name}")
                continue

            # This synchronous call contains all logic, including its own error handling.
            self._process_single_document(blob)
            processed_count += 1
        
        logging.info(f"ETL run finished. Processed {processed_count} new documents.")