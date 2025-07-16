# bsi-audit-automator/src/clients/document_ai_client.py
import logging
import asyncio
import json
from typing import Dict, Any, Optional

from google.cloud import documentai
from google.api_core.client_options import ClientOptions
from google.api_core.exceptions import GoogleAPICallError

from src.config import AppConfig
from src.clients.gcs_client import GcsClient

class DocumentAiClient:
    """A client for handling interactions with Google Cloud Document AI."""

    def __init__(self, config: AppConfig, gcs_client: GcsClient):
        """
        Initializes the Document AI client.

        Args:
            config: The application configuration object.
            gcs_client: An instance of the GCS client for reading results.
        """
        self.config = config
        self.gcs_client = gcs_client

        if not self.config.doc_ai_processor_name:
            raise ValueError("Document AI processor name is not configured.")

        # The processor name is 'projects/PROJECT/locations/LOCATION/processors/PROCESSOR_ID'
        # The client needs the location for its regional endpoint.
        try:
            self.processor_name = self.config.doc_ai_processor_name
            self.location = self.processor_name.split('/')[3]
            opts = ClientOptions(api_endpoint=f"{self.location}-documentai.googleapis.com")
            self.client = documentai.DocumentProcessorServiceClient(client_options=opts)
            logging.info(f"DocumentAI Client initialized for processor in location '{self.location}'.")
        except (IndexError, TypeError) as e:
            logging.error(f"Could not parse location from Document AI processor name: '{self.processor_name}'")
            raise ValueError("Invalid Document AI processor name format.") from e


    async def process_document_async(self, gcs_input_uri: str) -> Optional[Dict[str, Any]]:
        """
        Processes a single document from GCS using batch processing and returns the
        structured Document object as a dictionary.

        Args:
            gcs_input_uri: The 'gs://' path to the input PDF document.

        Returns:
            The parsed JSON content of the processed document, or None on failure.
        """
        file_name = gcs_input_uri.split('/')[-1]
        # Create a unique output location for this specific processing job
        gcs_output_uri = f"gs://{self.config.bucket_name}/{self.config.output_prefix}doc_ai_results/{file_name}/"
        
        logging.info(f"Starting Document AI batch processing for '{gcs_input_uri}'.")
        logging.info(f"Output will be stored in '{gcs_output_uri}'.")

        input_config = documentai.GcsDocument(gcs_uri=gcs_input_uri, mime_type="application/pdf")
        batch_input_config = documentai.BatchDocumentsInputConfig(gcs_documents=documentai.GcsDocuments(documents=[input_config]))
        output_config = documentai.DocumentOutputConfig(gcs_output_config=documentai.GcsOutputConfig(gcs_uri=gcs_output_uri))

        request = documentai.BatchProcessRequest(
            name=self.processor_name,
            input_documents=batch_input_config,
            document_output_config=output_config,
        )

        try:
            operation = self.client.batch_process_documents(request=request)
            
            # Use asyncio.to_thread to run the blocking 'result()' call in a separate thread
            logging.info("Waiting for Document AI batch operation to complete... This may take several minutes.")
            await asyncio.to_thread(operation.result)
            logging.info("Document AI batch operation completed successfully.")

            # After completion, find the resulting JSON file in the output GCS path
            output_blobs = self.gcs_client.list_files(prefix=gcs_output_uri.replace(f"gs://{self.config.bucket_name}/", ""))
            json_results = [blob for blob in output_blobs if blob.name.endswith(".json")]

            if not json_results:
                logging.error(f"No JSON result file found in Document AI output path: {gcs_output_uri}")
                return None
            
            # For a single input document, we expect a single result JSON
            result_blob_name = json_results[0].name
            logging.info(f"Found Document AI result file: {result_blob_name}")

            # Read the file and return its content
            document_data = self.gcs_client.read_json(result_blob_name)
            return document_data

        except GoogleAPICallError as e:
            logging.error(f"Document AI batch processing failed with an API error: {e}", exc_info=True)
            return None
        except Exception as e:
            logging.error(f"An unexpected error occurred during Document AI processing: {e}", exc_info=True)
            return None