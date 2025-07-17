# bsi-audit-automator/src/audit/stages/stage_gs_check_extraction.py
import logging
import json
import asyncio
import fitz # PyMuPDF
from typing import Dict, Any, List

from google.cloud.exceptions import NotFound

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.document_ai_client import DocumentAiClient
from src.clients.ai_client import AiClient
from src.clients.rag_client import RagClient

class GrundschutzCheckExtractionRunner:
    """
    A dedicated stage to process the Grundschutz-Check document using the
    Document AI Layout Parser. It implements a chunk-based workflow to handle
    large documents.
    """
    STAGE_NAME = "Grundschutz-Check-Extraction"
    
    # New paths for the layout parser workflow
    TEMP_PDF_CHUNK_PREFIX = "output/temp_pdf_chunks/"
    DOC_AI_CHUNK_RESULTS_PREFIX = "output/doc_ai_results/"
    FINAL_MERGED_LAYOUT_PATH = "output/results/intermediate/doc_ai_layout_parser_merged.json"
    
    PAGE_CHUNK_SIZE = 100 # Process 100 pages at a time

    def __init__(self, config: AppConfig, gcs_client: GcsClient, doc_ai_client: DocumentAiClient, ai_client: AiClient, rag_client: RagClient):
        self.config = config
        self.gcs_client = gcs_client
        self.doc_ai_client = doc_ai_client
        self.ai_client = ai_client # Kept for future Gemini stage
        self.rag_client = rag_client # Used to find the source document
        self.block_counter = 1
        logging.info(f"Initialized runner for stage: {self.STAGE_NAME}")

    def _reindex_and_prune_blocks(self, blocks: List[Dict[str, Any]]):
        """
        Recursively traverses a list of blocks to re-index blockId and remove pageSpan.
        This method modifies the list in place.
        """
        for block in blocks:
            # Remove pageSpan at the current level
            block.pop("pageSpan", None)
            
            # Re-index the current block
            block["blockId"] = str(self.block_counter)
            self.block_counter += 1
            
            # If there are nested blocks, recurse
            nested_text_block = block.get("textBlock", {})
            if "blocks" in nested_text_block:
                self._reindex_and_prune_blocks(nested_text_block["blocks"])
            
            nested_table_block = block.get("tableBlock", {})
            if "headerRows" in nested_table_block:
                for row in nested_table_block["headerRows"]:
                    for cell in row.get("cells", []):
                        if "blocks" in cell:
                            self._reindex_and_prune_blocks(cell["blocks"])
            if "bodyRows" in nested_table_block:
                for row in nested_table_block["bodyRows"]:
                    for cell in row.get("cells", []):
                        if "blocks" in cell:
                            self._reindex_and_prune_blocks(cell["blocks"])

    async def run(self, force_overwrite: bool = False) -> Dict[str, Any]:
        """
        Main execution method for the stage. It splits the source PDF, processes
        chunks in parallel with Document AI, and merges the results.
        """
        logging.info(f"Executing stage: {self.STAGE_NAME} with Layout Parser workflow.")
        
        # IDEMPOTENCY: Check if the final merged file already exists
        if not force_overwrite and self.gcs_client.blob_exists(self.FINAL_MERGED_LAYOUT_PATH):
            logging.info(f"Final merged layout file already exists at '{self.FINAL_MERGED_LAYOUT_PATH}'. Skipping stage.")
            return {"status": "skipped", "reason": "Final layout file already exists."}

        # 1. Find and download the source Grundschutz-Check PDF
        check_uris = self.rag_client.get_gcs_uris_for_categories(["Grundschutz-Check", "test.pdf"])
        if not check_uris:
            raise FileNotFoundError("Could not find document with category 'Grundschutz-Check' or 'test.pdf'.")
        
        source_blob_name = check_uris[0].replace(f"gs://{self.config.bucket_name}/", "")
        logging.info(f"Downloading source document: {source_blob_name}")
        pdf_bytes = self.gcs_client.download_blob_as_bytes(self.gcs_client.bucket.blob(source_blob_name))
        
        # 2. Split PDF into chunks and upload them
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        
        upload_tasks = []
        for i in range(0, pdf_doc.page_count, self.PAGE_CHUNK_SIZE):
            chunk_doc = fitz.open()
            start_page = i
            end_page = min(i + self.PAGE_CHUNK_SIZE, pdf_doc.page_count)
            chunk_doc.insert_pdf(pdf_doc, from_page=start_page, to_page=end_page - 1)
            
            chunk_bytes = chunk_doc.tobytes()
            chunk_doc.close()
            
            chunk_name = f"chunk_{i // self.PAGE_CHUNK_SIZE}.pdf"
            destination_blob_name = f"{self.TEMP_PDF_CHUNK_PREFIX}{chunk_name}"
            
            upload_tasks.append(
                self.gcs_client.upload_from_bytes_async(chunk_bytes, destination_blob_name)
            )
        
        await asyncio.gather(*upload_tasks)
        logging.info(f"Successfully split PDF into {len(upload_tasks)} chunks and uploaded to GCS.")

        # 3. Process chunks in parallel with Document AI
        num_chunks = len(upload_tasks)
        processing_tasks = []
        for i in range(num_chunks):
            chunk_name = f"chunk_{i}.pdf"
            gcs_input_uri = f"gs://{self.config.bucket_name}/{self.TEMP_PDF_CHUNK_PREFIX}{chunk_name}"
            processing_tasks.append(
                self.doc_ai_client.process_document_chunk_async(gcs_input_uri, self.DOC_AI_CHUNK_RESULTS_PREFIX)
            )

        processed_chunk_paths = await asyncio.gather(*processing_tasks)
        
        # 4. Merge, re-index, and prune the results
        merged_blocks = []
        # Ensure we process results in the correct order
        for i in range(num_chunks):
            chunk_json_path = f"{self.DOC_AI_CHUNK_RESULTS_PREFIX}chunk_{i}.json"
            if chunk_json_path not in processed_chunk_paths:
                logging.warning(f"Result for {chunk_json_path} not found. The merged document may be incomplete.")
                raise FileNotFoundError(f"Result for {chunk_json_path} not found. Processing cannot continue.")
            logging.info(f"Merging result from: {chunk_json_path}")
            chunk_data = await self.gcs_client.read_json_async(chunk_json_path)
            blocks_to_process = chunk_data.get("documentLayout", {}).get("blocks", [])
            self._reindex_and_prune_blocks(blocks_to_process)
            merged_blocks.extend(blocks_to_process)

        final_layout_json = {"documentLayout": {"blocks": merged_blocks}}
        
        # 5. Save the final merged file
        await self.gcs_client.upload_from_string_async(
            json.dumps(final_layout_json, indent=2, ensure_ascii=False),
            self.FINAL_MERGED_LAYOUT_PATH
        )
        logging.info(f"Successfully merged, re-indexed, and saved final layout to {self.FINAL_MERGED_LAYOUT_PATH}")

        # Note: As requested, temporary files in TEMP_PDF_CHUNK_PREFIX and DOC_AI_CHUNK_RESULTS_PREFIX are not deleted.

        return {"status": "success", "message": f"Successfully generated merged layout file."}