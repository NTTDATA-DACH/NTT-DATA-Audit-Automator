# src/clients/document_ai_client.py
import logging
import asyncio
import json
from typing import Dict, Any, Optional
from google.cloud import documentai_v1 as documentai
from google.cloud.documentai_v1.types import (
    BatchDocumentsInputConfig,
    BatchProcessRequest,
    DocumentOutputConfig,
    GcsDocument,
    GcsDocuments
)

from google.api_core.client_options import ClientOptions
from google.api_core.exceptions import GoogleAPICallError

from src.config import AppConfig
from src.clients.gcs_client import GcsClient

class DocumentAiClient:
    """A client for handling interactions with Google Cloud Document AI."""

    def __init__(self, config: AppConfig, gcs_client: GcsClient):
        self.config = config
        self.gcs_client = gcs_client

        if not self.config.doc_ai_processor_name:
            raise ValueError("Document AI processor name is not configured.")

        # Parse and validate name
        self.processor_name = self.config.doc_ai_processor_name.strip()  # Trim any extras
        parts = self.processor_name.split('/')
        if len(parts) != 6 or parts[0] != 'projects' or parts[2] != 'locations' or parts[4] != 'processors':
            raise ValueError(f"Invalid processor name format: '{self.processor_name}'. Expected 'projects/{project}/locations/{location}/processors/{processor}'.")

        self.location = parts[3]
        if self.location == 'us':
            opts = ClientOptions(api_endpoint="documentai.googleapis.com")
        else:
            opts = ClientOptions(api_endpoint=f"{self.location}-documentai.googleapis.com")
        self.client = documentai.DocumentProcessorServiceClient(client_options=opts)
        logging.info(f"DocumentAI Client initialized for processor '{self.processor_name}' in location '{self.location}'.")

    async def process_document_chunk_async(self, gcs_input_uri: str, gcs_output_prefix: str) -> Optional[str]:
        """
        Processes a single document chunk from GCS using batch processing and saves the result.
        This method is now idempotent on a per-chunk basis.

        Args:
            gcs_input_uri: The 'gs://' path to the input PDF document chunk.
            gcs_output_prefix: The GCS prefix where the output JSON should be stored.

        Returns:
            The GCS path to the generated JSON result file, or None on failure.
        """
        input_filename = gcs_input_uri.split('/')[-1]  # e.g., 'chunk_0.pdf'
        chunk_basename = input_filename.replace('.pdf', '')  # e.g., 'chunk_0'
        output_json_filename = f"{chunk_basename}.json"  # e.g., 'chunk_0.json'
        gcs_output_json_path = f"{gcs_output_prefix}{output_json_filename}"
        
        # IDEMPOTENCY: Check if the result for this specific chunk already exists.
        if self.gcs_client.blob_exists(gcs_output_json_path):
            logging.info(f"Result for chunk '{gcs_input_uri}' already exists. Skipping processing.")
            return gcs_output_json_path

        gcs_output_uri_for_api = f"gs://{self.config.bucket_name}/{gcs_output_prefix}"
        if not gcs_output_uri_for_api.endswith('/'):
            gcs_output_uri_for_api += '/'  # Ensure trailing slash for directory prefix
        logging.info(f"Starting Document AI batch processing for chunk '{gcs_input_uri}'.")
        
        input_config = GcsDocument(gcs_uri=gcs_input_uri, mime_type="application/pdf")
        batch_input_config = BatchDocumentsInputConfig(gcs_documents=GcsDocuments(documents=[input_config]))
        
        gcs_output_config = DocumentOutputConfig.GcsOutputConfig(gcs_uri=gcs_output_uri_for_api)
        output_config = DocumentOutputConfig(gcs_output_config=gcs_output_config)

        request = BatchProcessRequest(
            name=self.processor_name,
            input_documents=batch_input_config,
            document_output_config=output_config,
        )

        try:
            operation = self.client.batch_process_documents(request=request)
            logging.info(f"Waiting for Document AI operation for '{input_filename}' to complete...")
            await asyncio.to_thread(operation.result)
            logging.info(f"Document AI operation for '{input_filename}' completed.")
            
            # Get precise output folder from metadata (e.g., output/doc_ai_results/{op_id}/0/)
            from google.cloud.documentai_v1 import BatchProcessMetadata
            metadata = BatchProcessMetadata(operation.metadata)
            if not metadata.individual_process_statuses:
                logging.error(f"No process statuses found for operation {operation.name}")
                return None
            # Since one input document, take the first status
            output_gcs_destination = metadata.individual_process_statuses[0].output_gcs_destination
            output_folder = output_gcs_destination.replace(f"gs://{self.config.bucket_name}/", "")
            
            # List all JSON shards in this operation's output folder
            output_blobs = self.gcs_client.list_files(prefix=output_folder)
            shard_blobs = [b for b in output_blobs if b.name.endswith('.json') and chunk_basename in b.name.split('/')[-1]]
            
            if not shard_blobs:
                logging.error(f"No result JSONs found in output path: {output_folder}")
                return None
            
            # Merge shards if multiple (sort by name for page order)
            merged_data = {"documentLayout": {"blocks": []}}
            for blob in sorted(shard_blobs, key=lambda b: b.name):
                shard_content = json.loads(await asyncio.to_thread(blob.download_as_text))
                if "documentLayout" in shard_content and "blocks" in shard_content["documentLayout"]:
                    merged_data["documentLayout"]["blocks"].extend(shard_content["documentLayout"]["blocks"])
                else:
                    logging.warning(f"Shard {blob.name} missing expected 'documentLayout.blocks'; skipping.")
            
            if not merged_data["documentLayout"]["blocks"]:
                logging.error(f"No valid blocks found after merging shards for '{input_filename}'")
                return None
            
            # Upload merged result to clean path
            merged_json_str = json.dumps(merged_data, ensure_ascii=False)
            await self.gcs_client.upload_from_string_async(merged_json_str, gcs_output_json_path)
            logging.info(f"Saved merged result for chunk to: {gcs_output_json_path}")
            
            # Clean up: Delete the raw shard files and any other blobs in the output folder
            blobs_to_delete = [blob.name for blob in output_blobs]
            if blobs_to_delete:
                self.gcs_client.bucket.delete_blobs(blobs_to_delete)
                logging.info(f"Deleted {len(blobs_to_delete)} raw shard files from {output_folder}")
            
            return gcs_output_json_path

        except GoogleAPICallError as e:
            logging.error(f"Document AI processing for chunk '{gcs_input_uri}' failed with API error: {e}", exc_info=True)
            return None
        except Exception as e:
            logging.error(f"An unexpected error occurred during Document AI processing for chunk '{gcs_input_uri}': {e}", exc_info=True)
            return None