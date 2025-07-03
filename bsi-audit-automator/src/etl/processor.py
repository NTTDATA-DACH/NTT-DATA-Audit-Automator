# src/etl/processor.py
import logging
import json
from typing import List, Dict, Any
from google.cloud.exceptions import NotFound
from langchain.text_splitter import RecursiveCharacterTextSplitter
import fitz  # PyMuPDF

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.ai_client import AiClient

# Constants for ETL processing
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 100
STATE_FILE_PATH = "output/etl_state.json"
FINAL_OUTPUT_PATH = "vector_index_data/embeddings.jsonl"

class EtlProcessor:
    """Orchestrates the ETL process for the RAG pipeline."""

    def __init__(self, config: AppConfig, gcs_client: GcsClient, ai_client: AiClient):
        self.config = config
        self.gcs_client = gcs_client
        self.ai_client = ai_client
        logging.info("ETL Processor initialized.")

    def _load_state(self) -> dict:
        """Loads the last saved ETL state from GCS."""
        try:
            state_content = self.gcs_client.read_text_file(STATE_FILE_PATH)
            state = json.loads(state_content)
            logging.info(f"Resuming ETL from saved state. {len(state.get('processed_files', []))} files already complete.")
            return state
        except NotFound:
            logging.info("No saved ETL state found. Starting a new ETL process.")
            return {"processed_files": [], "completed_embeddings_jsonl": []}

    def _save_state(self, state: dict):
        """Saves the current ETL state to GCS."""
        self.gcs_client.upload_from_string(
            content=json.dumps(state, indent=2),
            destination_blob_name=STATE_FILE_PATH
        )
        logging.info(f"Successfully saved ETL state for {len(state['processed_files'])} processed files.")

    def run(self) -> None:
        """Executes the full ETL pipeline."""
        state = self._load_state()
        processed_files = set(state.get("processed_files", []))
        aggregated_jsonl_lines = state.get("completed_embeddings_jsonl", [])

        source_files = self.gcs_client.list_source_files()
        files_to_process = [f for f in source_files if f.name not in processed_files]

        if self.config.is_test_mode:
            logging.warning("TEST MODE: Limiting processing to the first 2 new files only.")
            files_to_process = files_to_process[:2]

        if not files_to_process:
            logging.info("All source files have already been processed. ETL is complete.")
            self.gcs_client.upload_from_string("\n".join(aggregated_jsonl_lines), FINAL_OUTPUT_PATH)
            return

        for blob in files_to_process:
            logging.info(f"--- Processing new document: {blob.name} ---")
            
            # 1. Chunk the new document
            doc_chunks = self._chunk_single_document(blob)
            if not doc_chunks:
                logging.warning(f"No chunks created for {blob.name}, skipping.")
                processed_files.add(blob.name)
                self._save_state({"processed_files": list(processed_files), "completed_embeddings_jsonl": aggregated_jsonl_lines})
                continue

            # 2. Generate embeddings for the new chunks
            chunk_texts = [chunk['text_content'] for chunk in doc_chunks]
            success, embeddings = self.ai_client.get_embeddings(chunk_texts)

            if not success:
                logging.critical("ETL process failed during embedding generation. State has been saved for completed files. Please check quotas and re-run.")
                exit(1)

            # 3. Format and update state
            jsonl_lines_for_doc = self._format_for_indexing(doc_chunks, embeddings)
            aggregated_jsonl_lines.extend(jsonl_lines_for_doc)
            processed_files.add(blob.name)

            # 4. Save state after each successful file
            self._save_state({"processed_files": list(processed_files), "completed_embeddings_jsonl": aggregated_jsonl_lines})

        logging.info("ETL process complete. Uploading final aggregated embeddings file.")
        self.gcs_client.upload_from_string("\n".join(aggregated_jsonl_lines), FINAL_OUTPUT_PATH)

    def _chunk_single_document(self, blob: Any) -> List[Dict[str, Any]]:
        """Reads one file from GCS, extracts text with PyMuPDF, and chunks it."""
        all_chunks_with_metadata = []
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP
        )

        if not blob.name.lower().endswith(".pdf"):
            logging.info(f"Skipping non-PDF file: {blob.name}")
            return []

        try:
            pdf_bytes = self.gcs_client.download_blob_as_bytes(blob)
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            full_text = "".join(page.get_text() for page in doc)
            doc.close()

            if not full_text.strip():
                logging.warning(f"No text extracted from {blob.name}. It may be image-only.")
                return []

            chunks = text_splitter.split_text(full_text)
            for i, chunk_text in enumerate(chunks):
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

        return all_chunks_with_metadata

    def _format_for_indexing(self, chunks: List[Dict], embeddings: List[List[float]]) -> List[str]:
        """
        Formats chunks and embeddings into a rich JSONL format.
        """
        jsonl_lines = []
        for chunk, embedding in zip(chunks, embeddings):
            json_obj = {
                "id": chunk['id'],
                "embedding": embedding,
                "text_content": chunk['text_content'],
                "source_document": chunk['source_document'],
                "restricts": [{
                    "namespace": "source_document",
                    "allow": [chunk['source_document']]
                }]
            }
            jsonl_lines.append(json.dumps(json_obj))
        return jsonl_lines