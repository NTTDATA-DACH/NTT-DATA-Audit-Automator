# bsi-audit-automator/src/audit/stages/gs_extraction/document_processor.py
import logging
import json
import asyncio
import fitz  # PyMuPDF
from typing import Dict, Any, List

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.document_ai_client import DocumentAiClient
from src.clients.rag_client import RagClient


class DocumentProcessor:
    """
    Handles Document AI Layout Parser workflow for processing the Grundschutz-Check PDF.
    Splits large PDFs into chunks, processes them with Document AI, and merges results.
    """
    
    TEMP_PDF_CHUNK_PREFIX = "output/temp_pdf_chunks/"
    DOC_AI_CHUNK_RESULTS_PREFIX = "output/doc_ai_results/"
    FINAL_MERGED_LAYOUT_PATH = "output/results/intermediate/doc_ai_layout_parser_merged.json"
    PAGE_CHUNK_SIZE = 100

    def __init__(self, gcs_client: GcsClient, doc_ai_client: DocumentAiClient, rag_client: RagClient, config: AppConfig):
        self.gcs_client = gcs_client
        self.doc_ai_client = doc_ai_client
        self.rag_client = rag_client
        self.config = config
        self.block_counter = 1

    async def execute_layout_parser_workflow(self, force_overwrite: bool):
        """
        Execute the full Document AI Layout Parser workflow.
        
        Args:
            force_overwrite: If True, reprocess even if output already exists
        """
        if not force_overwrite and self.gcs_client.blob_exists(FINAL_MERGED_LAYOUT_PATH):
            logging.info(f"Merged layout file already exists. Skipping Layout Parser workflow.")
            return

        logging.info("Starting Document AI Layout Parser workflow...")
        
        # Find the Grundschutz-Check document
        check_uris = self.rag_client.get_gcs_uris_for_categories(["Grundschutz-Check", "test.pdf"])
        if not check_uris:
            raise FileNotFoundError("Could not find 'Grundschutz-Check' or 'test.pdf' document.")
        
        # Download and split PDF
        source_blob_name = check_uris[0].replace(f"gs://{self.config.bucket_name}/", "")
        pdf_bytes = self.gcs_client.download_blob_as_bytes(self.gcs_client.bucket.blob(source_blob_name))
        
        chunk_count = await self._split_and_upload_pdf(pdf_bytes)
        
        # Process all chunks with Document AI
        await self._process_pdf_chunks(chunk_count)
        
        # Merge and finalize results
        await self._merge_and_save_results(chunk_count)

    async def _split_and_upload_pdf(self, pdf_bytes: bytes) -> int:
        """Split PDF into chunks and upload to GCS."""
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        upload_tasks = []
        
        for i in range(0, pdf_doc.page_count, self.PAGE_CHUNK_SIZE):
            chunk_doc = fitz.open()
            end_page = min(i + self.PAGE_CHUNK_SIZE, pdf_doc.page_count) - 1
            chunk_doc.insert_pdf(pdf_doc, from_page=i, to_page=end_page)
            
            destination_blob_name = f"{self.TEMP_PDF_CHUNK_PREFIX}chunk_{i // self.PAGE_CHUNK_SIZE}.pdf"
            upload_tasks.append(
                self.gcs_client.upload_from_bytes_async(chunk_doc.tobytes(), destination_blob_name)
            )
            chunk_doc.close()
        
        await asyncio.gather(*upload_tasks)
        pdf_doc.close()
        
        chunk_count = len(upload_tasks)
        logging.info(f"Split PDF into {chunk_count} chunks and uploaded to GCS.")
        return chunk_count

    async def _process_pdf_chunks(self, chunk_count: int):
        """Process all PDF chunks with Document AI."""
        processing_tasks = [
            self.doc_ai_client.process_document_chunk_async(
                f"gs://{self.config.bucket_name}/{self.TEMP_PDF_CHUNK_PREFIX}chunk_{i}.pdf", 
                self.DOC_AI_CHUNK_RESULTS_PREFIX
            ) for i in range(chunk_count)
        ]
        await asyncio.gather(*processing_tasks)
        logging.info(f"Processed {chunk_count} chunks with Document AI.")

    async def _merge_and_save_results(self, chunk_count: int):
        """Merge all chunk results and save final layout."""
        merged_blocks = []
        merged_text = ""
        
        # Collect results from all chunks
        for i in range(chunk_count):
            chunk_json_path = f"{self.DOC_AI_CHUNK_RESULTS_PREFIX}chunk_{i}.json"
            chunk_data = await self.gcs_client.read_json_async(chunk_json_path)
            merged_text += chunk_data.get("text", "")
            merged_blocks.extend(chunk_data.get("documentLayout", {}).get("blocks", []))

        # Re-index block IDs globally and clean up
        self.block_counter = 1
        self._reindex_and_prune_blocks(merged_blocks)
        
        # Create final layout structure
        final_layout_json = {
            "text": merged_text, 
            "documentLayout": {"blocks": merged_blocks}
        }
        
        # Save to GCS
        await self.gcs_client.upload_from_string_async(
            json.dumps(final_layout_json, indent=2, ensure_ascii=False),
            FINAL_MERGED_LAYOUT_PATH
        )
        logging.info(f"Successfully merged, re-indexed, and saved final layout to {FINAL_MERGED_LAYOUT_PATH}")

    def _reindex_and_prune_blocks(self, blocks: List[Dict[str, Any]]):
        """Recursively re-index blockId globally and remove pageSpan."""
        for block in blocks:
            # Remove page span information (not needed after merging)
            block.pop("pageSpan", None)
            
            # Assign new global block ID
            block["blockId"] = str(self.block_counter)
            self.block_counter += 1
            
            # Process nested text blocks
            if "blocks" in block.get("textBlock", {}):
                self._reindex_and_prune_blocks(block["textBlock"]["blocks"])
            
            # Process table blocks
            for row_type in ["headerRows", "bodyRows"]:
                for row in block.get("tableBlock", {}).get(row_type, []):
                    for cell in row.get("cells", []):
                        if "blocks" in cell:
                            self._reindex_and_prune_blocks(cell["blocks"])