# src/clients/gcs_client.py
import logging
from google.cloud import storage
from src.config import AppConfig

class GcsClient:
    """A client for all Google Cloud Storage interactions."""

    def __init__(self, config: AppConfig):
        """
        Initializes the GCS client.

        Args:
            config: The application configuration object.
        """
        self.config = config
        self.storage_client = storage.Client(project=config.gcp_project_id)
        # We derive the bucket name from the config, which should be set by an env var
        # that comes from the terraform output.
        if not config.bucket_name:
            raise ValueError("GCS Bucket name is not configured in the environment.")
        self.bucket = self.storage_client.bucket(config.bucket_name)
        logging.info(f"GCS Client initialized for bucket: gs://{self.bucket.name}")

    def list_source_files(self) -> list[storage.Blob]:
        """
        Lists all processable files from the customer's source GCS prefix.

        Returns:
            A list of GCS blob objects.
        """
        logging.info(f"Listing source files from prefix: {self.config.source_prefix}")
        blobs = self.storage_client.list_blobs(
            self.bucket.name, prefix=self.config.source_prefix
        )
        # Filter for common document types, ignore empty "directory" blobs
        files = [blob for blob in blobs if "." in blob.name]
        logging.info(f"Found {len(files)} source files to process.")
        return files

    def download_blob_as_bytes(self, blob: storage.Blob) -> bytes:
        """Downloads a blob from GCS into memory as bytes."""
        logging.debug(f"Downloading blob: {blob.name}")
        return blob.download_as_bytes()

    def upload_from_string(self, content: str, destination_blob_name: str):
        """
        Uploads a string content to a specified blob in GCS.

        Args:
            content: The string content to upload.
            destination_blob_name: The full path for the object in the bucket.
        """
        logging.info(f"Uploading content to gs://{self.bucket.name}/{destination_blob_name}")
        blob = self.bucket.blob(destination_blob_name)
        blob.upload_from_string(content, content_type='application/jsonl')
        logging.info("Upload complete.")