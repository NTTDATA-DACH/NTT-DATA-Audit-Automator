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

        try:
            self.processor_name = self.config.doc_ai_processor_name
            self.location = "eu"
            opts = ClientOptions(api_endpoint="eu-documentai.googleapis.com")
            self.client = documentai.DocumentProcessorServiceClient(client_options=opts)
            logging.info(f"DocumentAI Client initialized for processor in location '{self.location}'. processor name: {self.processor_name} ")
        except (IndexError, TypeError) as e:
            logging.error(f"Could not parse location from Document AI processor name: '{self.config.doc_ai_processor_name}'")
            raise ValueError("Invalid Document AI processor name format.") from e

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
        input_filename = gcs_input_uri.split('/')[-1]
        output_json_filename = input_filename.replace('.pdf', '.json')
        gcs_output_json_path = f"{gcs_output_prefix}{output_json_filename}"
        
        # IDEMPOTENCY: Check if the result for this specific chunk already exists.
        if self.gcs_client.blob_exists(gcs_output_json_path):
            logging.info(f"Result for chunk '{gcs_input_uri}' already exists. Skipping processing.")
            return gcs_output_json_path

        gcs_output_uri_for_api = f"gs://{self.config.bucket_name}/{gcs_output_prefix}"
        logging.info(f"Starting Document AI batch processing for chunk '{gcs_input_uri}'.")
        
        input_config = GcsDocument(gcs_uri=gcs_input_uri, mime_type="application/pdf")
        batch_input_config = BatchDocumentsInputConfig(gcs_documents=GcsDocuments(documents=[input_config]))
        
        gcs_output_config = DocumentOutputConfig.GcsOutputConfig(gcs_uri=gcs_output_uri_for_api)
        # DocumentOutputConfig.GcsOutputConfig(gcs_uri=gcs_output_uri)
        output_config = DocumentOutputConfig(gcs_output_config=gcs_output_config)

        request = documentai.BatchProcessRequest(
            name=self.processor_name,
            input_documents=batch_input_config,
            document_output_config=output_config,
        )

        # raise SystemExit(f"Full BatchProcessRequest dump:\n{request}")

        try:
            operation = self.client.batch_process_documents(request=request)
            logging.info(f"Waiting for Document AI operation for '{input_filename}' to complete...")
            await asyncio.to_thread(operation.result)
            logging.info(f"Document AI operation for '{input_filename}' completed.")

            # The API creates a folder structure. We need to find the JSON inside it.
            # e.g., output/prefix/123456789/0/chunk_0.json
            api_result_folder_prefix = gcs_output_uri_for_api.replace(f"gs://{self.config.bucket_name}/", "")
            output_blobs = self.gcs_client.list_files(prefix=api_result_folder_prefix)
            
            # Find the JSON that corresponds to our input chunk
            source_result_blob = next((b for b in output_blobs if output_json_filename in b.name and b.name.endswith('.json')), None)

            if not source_result_blob:
                logging.error(f"Could not find result JSON for '{input_filename}' in output path: {api_result_folder_prefix}")
                return None

            # Move the result to the clean, final path for this chunk
            await self.gcs_client.copy_blob_async(source_result_blob.name, gcs_output_json_path)
            logging.info(f"Saved final result for chunk to: {gcs_output_json_path}")
            
            return gcs_output_json_path

        except GoogleAPICallError as e:
            logging.error(f"Document AI processing for chunk '{gcs_input_uri}' failed with API error: {e}", exc_info=True)
            return None
        except Exception as e:
            logging.error(f"An unexpected error occurred during Document AI processing for chunk '{gcs_input_uri}': {e}", exc_info=True)
            return None