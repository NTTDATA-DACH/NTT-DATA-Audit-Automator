# src/clients/rag_client.py
import logging
import json
import asyncio
from typing import List, Dict, Any, Optional

from google.cloud.exceptions import NotFound

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.ai_client import AiClient

DOC_MAP_PATH = "output/document_map.json"
MAX_FILES_TEST_MODE = 3

class RagClient:
    """
    Client to find relevant documents for audit tasks. It manages a map of
    document filenames to BSI categories, creating this map on-demand if it
    doesn't exist. This client is the replacement for the Vector Search RAG pipeline.
    Its name is kept for consistency in the project structure.
    """

    def __init__(self, config: AppConfig, gcs_client: GcsClient, ai_client: AiClient):
        self.config = config
        self.gcs_client = gcs_client
        self.ai_client = ai_client
        self._document_category_map: Optional[Dict[str, List[str]]] = None
        self._all_source_files: List[str] = []

    @classmethod
    async def create(cls, config: AppConfig, gcs_client: GcsClient, ai_client: AiClient, force_remap: bool = False):
        """Asynchronous factory to create and initialize the client."""
        instance = cls(config, gcs_client, ai_client)
        await instance._initialize(force_remap=force_remap)
        return instance

    async def _initialize(self, force_remap: bool = False):
        """Initializes the client by ensuring the document map is ready."""
        logging.info("Initializing Document Finder (RagClient)...")
        self._all_source_files = [blob.name for blob in self.gcs_client.list_files()]
        await self._ensure_document_map_exists(force_remap=force_remap)

    def _load_asset_text(self, path: str) -> str:
        with open(path, 'r', encoding='utf-8') as f: return f.read()

    def _load_asset_json(self, path: str) -> dict:
        with open(path, 'r', encoding='utf-8') as f: return json.load(f)

    async def _create_document_map(self) -> None:
        """
        Uses an AI model to classify source documents into predefined BSI categories
        based on their filenames. Saves the result to a map file in GCS.
        The map stores the full GCS object path for each file.
        Falls back to classifying all documents as 'Sonstiges' on failure.
        """
        logging.info("Starting AI-driven document classification...")
        
        # Create a mapping from basename to the full GCS path for later use.
        basename_to_fullpath_map = {name.split('/')[-1]: name for name in self._all_source_files}
        filenames = list(basename_to_fullpath_map.keys())

        if not filenames:
            logging.warning("No source files found to classify.")
            # Create an empty map to prevent repeated attempts
            self.gcs_client.upload_from_string("{}", DOC_MAP_PATH)
            return

        prompt_template = self._load_asset_text("assets/prompts/etl_classify_documents.txt")
        schema = self._load_asset_json("assets/schemas/etl_classify_documents_schema.json")
        
        filenames_json = json.dumps(filenames, indent=2)
        prompt = prompt_template.format(filenames_json=filenames_json)

        try:
            classification_result = await self.ai_client.generate_json_response(
                prompt,
                schema,
                request_context_log="Document Classification"
            )
            
            # Replace basenames in the result with their full GCS paths before saving.
            for item in classification_result.get("document_map", []):
                basename = item.get("filename")
                if basename in basename_to_fullpath_map:
                    item["filename"] = basename_to_fullpath_map[basename]
                else:
                    logging.warning(f"AI returned a filename '{basename}' not found in the source file list. It will be ignored.")

            content_to_upload = json.dumps(classification_result, indent=2, ensure_ascii=False)
            logging.info("Successfully created document map via AI with full file paths.")

        except Exception as e:
            logging.critical(
                f"AI-driven document classification failed: {e}. "
                f"Creating a fallback map with all documents as 'Sonstiges'. "
                "Document selection will be impaired.",
                exc_info=True
            )
            # The fallback map should use the full paths directly from the source file list.
            fallback_map = {"document_map": [{"filename": full_path, "category": "Sonstiges"} for full_path in self._all_source_files]}
            content_to_upload = json.dumps(fallback_map, indent=2, ensure_ascii=False)
        
        self.gcs_client.upload_from_string(
            content=content_to_upload,
            destination_blob_name=DOC_MAP_PATH
        )
        logging.info(f"Saved document map to '{DOC_MAP_PATH}'.")

    async def _ensure_document_map_exists(self, force_remap: bool = False) -> None:
        """
        Loads the document classification map from GCS. If it doesn't exist,
        or if `force_remap` is True, it triggers the creation process.
        """
        # If force is requested, or if the map simply doesn't exist, create it.
        if force_remap or not self.gcs_client.blob_exists(DOC_MAP_PATH):
            if force_remap:
                logging.info("--force flag is set. Re-creating document classification map.")
            else:
                logging.warning(f"Document map not found at '{DOC_MAP_PATH}'. Triggering creation.")
            await self._create_document_map()
        else:
             logging.info(f"Using existing document map from '{DOC_MAP_PATH}'.")

        # Now, load the map that is guaranteed to exist and build the internal lookup.
        try:
            map_data = self.gcs_client.read_json(DOC_MAP_PATH)
        except NotFound:
            logging.critical(f"FATAL: Document map '{DOC_MAP_PATH}' could not be loaded, even after creation attempt. Cannot proceed.")
            raise
        
        category_map = {}
        doc_map_list = map_data.get("document_map", [])
        for item in doc_map_list:
            category = item.get("category")
            # The map now correctly stores the full GCS path (e.g. 'source_documents/file.pdf'), not just the basename.
            filename = item.get("filename")
            if category and filename:
                if category not in category_map:
                    category_map[category] = []
                category_map[category].append(filename)
        
        self._document_category_map = category_map
        logging.info(f"Successfully built document category map with {len(category_map)} categories.")

    def get_gcs_uris_for_categories(self, source_categories: List[str] = None) -> List[str]:
        """
        Finds the GCS URIs for documents belonging to the specified categories.

        Args:
            source_categories: A list of BSI categories (e.g., 'Strukturanalyse').
                               If None, all source document URIs are returned.

        Returns:
            A list of 'gs://...' URIs for the model to use as context.
        """
        if self._document_category_map is None:
            # This should not happen due to the async initializer, but as a safeguard:
            raise RuntimeError("Document map has not been initialized. Call `await RagClient.create()`.")
            
        selected_filenames = set()
        
        if source_categories:
            for category in source_categories:
                filenames = self._document_category_map.get(category, [])
                selected_filenames.update(filenames)
            if not selected_filenames:
                 logging.warning(f"No documents found for categories: {source_categories}. Returning all documents as a fallback.")
                 selected_filenames.update(self._all_source_files)
        else:
            # If no categories are specified, use all source files
            selected_filenames.update(self._all_source_files)

        # The filename (fname) is now the full path relative to the bucket root (e.g., 'source_documents/file.pdf')
        uris = [f"gs://{self.config.bucket_name}/{fname}" for fname in sorted(list(selected_filenames))]
        
        if self.config.is_test_mode and len(uris) > MAX_FILES_TEST_MODE:
            logging.warning(f"TEST MODE: Limiting context files from {len(uris)} to {MAX_FILES_TEST_MODE}.")
            return uris[:MAX_FILES_TEST_MODE]
            
        return uris