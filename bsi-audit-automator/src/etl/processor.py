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
EMBEDDING_BATCH_SIZE = 25 # Process and save state every 25 chunks.
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
            state = self.gcs_client.read_json(STATE_FILE_PATH)
            # Ensure keys exist for backwards compatibility
            state.setdefault('files', {})
            state.setdefault('completed_embeddings_jsonl', [])
            logging.info(f"Resuming ETL from saved state. {len(state.get('files', {}))} files tracked.")
            return state
        except NotFound:
            logging.info("No saved ETL state found. Starting a new ETL process.")
            return {"files": {}, "completed_embeddings_jsonl": []}

    def _save_state(self, state: dict):
        """Saves the current ETL state to GCS."""
        self.gcs_client.upload_from_string(
            content=json.dumps(state, indent=2),
            destination_blob_name=STATE_FILE_PATH
        )
        logging.debug(f"Successfully saved ETL state.")

    def run(self) -> None:
        """Executes the full ETL pipeline."""
        state = self._load_state()
        file_states = state.get("files", {})
        aggregated_jsonl_lines = state.get("completed_embeddings_jsonl", [])

        source_files = self.gcs_client.list_source_files()
        files_to_process = [f for f in source_files if file_states.get(f.name, {}).get('status') != 'completed']

        if self.config.is_test_mode:
            logging.warning("TEST MODE: Limiting processing to the first 2 new files only.")
            files_to_process = files_to_process[:2]

        if not files_to_process:
            logging.info("All source files have been successfully processed. ETL is complete.")
            self.gcs_client.upload_from_string("\n".join(aggregated_jsonl_lines), FINAL_OUTPUT_PATH)
            return

        for blob in files_to_process:
            logging.info(f"--- Processing document: {blob.name} ---")

            # 1. Chunk the entire document first to get a total count
            all_doc_chunks = self._chunk_single_document(blob)
            if not all_doc_chunks:
                logging.warning(f"No chunks created for {blob.name}, marking as complete and skipping.")
                file_states[blob.name] = {"status": "completed", "processed_chunks": 0}
                self._save_state({"files": file_states, "completed_embeddings_jsonl": aggregated_jsonl_lines})
                continue

            # 2. Determine where to resume from
            start_index = file_states.get(blob.name, {}).get("processed_chunks", 0)
            if start_index > 0:
                logging.info(f"Resuming '{blob.name}' from chunk {start_index + 1}/{len(all_doc_chunks)}")

            chunks_to_process = all_doc_chunks[start_index:]
            current_processed_count = start_index

            # 3. Process the remaining chunks in batches
            for i in range(0, len(chunks_to_process), EMBEDDING_BATCH_SIZE):
                batch = chunks_to_process[i:i + EMBEDDING_BATCH_SIZE]
                logging.info(f"Processing batch of {len(batch)} chunks for '{blob.name}' (starting from chunk {current_processed_count + 1})")
                
                batch_texts = [chunk['text_content'] for chunk in batch]
                success, embeddings = self.ai_client.get_embeddings(batch_texts)

                if not success:
                    logging.critical("ETL process failed during embedding generation. State has been saved for the last successful batch. Please check quotas and re-run.")
                    exit(1)

                # 4. Format, append results, and save state after each successful batch
                jsonl_lines_for_batch = self._format_for_indexing(batch, embeddings)
                aggregated_jsonl_lines.extend(jsonl_lines_for_batch)
                current_processed_count += len(batch)

                file_states[blob.name] = {"status": "in_progress", "processed_chunks": current_processed_count}
                self._save_state({"files": file_states, "completed_embeddings_jsonl": aggregated_jsonl_lines})

            # 5. Once all batches for a file are done, mark it as completed
            logging.info(f"--- Document '{blob.name}' completed successfully. ---")
            file_states[blob.name] = {"status": "completed", "processed_chunks": current_processed_count}
            self._save_state({"files": file_states, "completed_embeddings_jsonl": aggregated_jsonl_lines})

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