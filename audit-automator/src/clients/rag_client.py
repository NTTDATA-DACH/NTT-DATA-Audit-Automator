# src/clients/rag_client.py
import logging
import json
from typing import List, Dict, Any, Optional, Tuple

from google.cloud.exceptions import NotFound

from src.assets_loader import load_asset_json
from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.ai_client import AiClient
from src.clients.spreadsheet import SHEET_SUFFIXES
from src.constants import (
    DOCUMENT_CATEGORY_MAP_PATH,
    DOCUMENT_INVENTORY_PATH,
    EXTRACTION_THINKING_LEVEL,
    PROMPT_CONFIG_PATH,
)

DOC_MAP_PATH = DOCUMENT_CATEGORY_MAP_PATH
MAX_FILES_TEST_MODE = 3
PDF_SUFFIX = ".pdf"
# What the model can read: PDFs as file parts, spreadsheets as rendered row text. Office
# formats like DOCX are not readable and are reported in the inventory instead.
MODEL_FORMATS = {PDF_SUFFIX: "pdf", **{s: "sheet" for s in SHEET_SUFFIXES}}


def _format_of(filename: str) -> str:
    lower = filename.lower()
    for suffix, fmt in MODEL_FORMATS.items():
        if lower.endswith(suffix):
            return fmt
    return "other"


# Suffixes an export tool leaves in the name of the sheet it printed: "A.1 Struktur.xlsx.pdf"
# is the print of "A.1 Struktur.xlsx", so the twin match has to look through them. Only
# sheet suffixes: "A.6 Plan.docx.pdf" is a Word document, not a print of "A.6 Plan.xlsx".
EXPORT_SOURCE_SUFFIXES = SHEET_SUFFIXES + (".xls",)


def _stem(filename: str) -> str:
    """The name without its format, lower-cased, for the sheet-replaces-PDF twin match.

    One trailing format is stripped, then one sheet suffix if the export tool kept it,
    so "A.1 Struktur.xlsx.pdf", "A.1 Struktur.xlsx" and "A.1 Struktur.pdf" share a stem.
    """
    base = filename.rsplit("/", 1)[-1].lower()
    if "." in base:
        base = base.rsplit(".", 1)[0]
    for suffix in EXPORT_SOURCE_SUFFIXES:
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def select_model_documents(names: List[str]) -> List[Tuple[str, bool, str]]:
    """Decide per file whether it goes to the model: (name, selected, reason).

    A spreadsheet replaces a PDF of the same name, because the PDF is usually a print of
    that very sheet and the row text is the more exact, cheaper input. PDFs without a sheet
    twin go as they are; anything else is not readable by the model.
    """
    sheet_stems = {_stem(n) for n in names if _format_of(n) == "sheet"}
    decisions = []
    for name in sorted(names):
        fmt = _format_of(name)
        if fmt == "sheet":
            decisions.append((name, True, "spreadsheet, sent as row text"))
        elif fmt == "pdf" and _stem(name) in sheet_stems:
            decisions.append((name, False, "a spreadsheet with the same name is sent instead"))
        elif fmt == "pdf":
            decisions.append((name, True, "PDF, sent as file part"))
        else:
            decisions.append((name, False, "format not readable by the model (deliver as PDF or XLSX)"))
    return decisions


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
        self.prompt_config = load_asset_json(PROMPT_CONFIG_PATH)

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


    async def _classify_files(self, files_to_classify: List[str]) -> List[Dict[str, str]]:
        """
        Classifies the given GCS object paths into BSI categories via the AI model.

        Filenames the model returns that are not in `files_to_classify` are dropped:
        a hallucinated or drifted name would otherwise become a dead 'gs://' URI that
        fails every retry of every AI call selecting that category.

        Returns:
            A list of {'filename': <full GCS path>, 'category': …} items.

        Raises:
            Exception: propagated from the AI client so callers can decide on a fallback.
        """
        basename_to_fullpath_map = {name.split('/')[-1]: name for name in files_to_classify}
        filenames = list(basename_to_fullpath_map.keys())

        etl_config = self.prompt_config["stages"]["ETL"]["classify_documents"]
        prompt_template = etl_config["prompt"]
        schema = load_asset_json(etl_config["schema_path"])
        
        filenames_json = json.dumps(filenames, indent=2)
        prompt = prompt_template.format(filenames_json=filenames_json)

        classification_result = await self.ai_client.generate_json_response(
            prompt,
            schema,
            request_context_log="Document Classification",
            thinking_level=EXTRACTION_THINKING_LEVEL,
        )

        validated_items = []
        for item in classification_result.get("document_map", []):
            basename = item.get("filename")
            if basename in basename_to_fullpath_map:
                item["filename"] = basename_to_fullpath_map[basename]
                validated_items.append(item)
            else:
                logging.warning(
                    f"AI returned a filename '{basename}' that is not in the source file list. "
                    "It is dropped from the document map."
                )
        return validated_items

    async def _create_document_map(self) -> None:
        """
        Uses an AI model to classify source documents into predefined BSI categories
        based on their filenames. Saves the result to a map file in GCS.
        The map stores the full GCS object path for each file.
        Falls back to classifying all documents as 'Sonstiges' on failure.
        """
        logging.info("Starting AI-driven document classification...")

        if not self._all_source_files:
            logging.warning("No source files found to classify.")
            self.gcs_client.upload_from_string("{}", DOC_MAP_PATH)
            return

        try:
            document_map = await self._classify_files(self._all_source_files)
            logging.info("Successfully created document map via AI with full file paths.")
        except Exception as e:
            logging.critical(
                f"AI-driven document classification failed: {e}. "
                f"Creating a fallback map with all documents as 'Sonstiges'. "
                "Document selection will be impaired.",
                exc_info=True
            )
            document_map = [{"filename": full_path, "category": "Sonstiges"} for full_path in self._all_source_files]

        self.gcs_client.write_json({"document_map": document_map}, DOC_MAP_PATH)
        logging.info(f"Saved document map to '{DOC_MAP_PATH}'.")

    async def _ensure_document_map_exists(self, force_remap: bool = False) -> None:
        """
        Loads the document classification map from GCS. If it doesn't exist,
        or if `force_remap` is True, it triggers the creation process.

        A cached map is reconciled against the current bucket contents: entries for
        objects that no longer exist are dropped and documents uploaded after the map
        was written are classified incrementally, so later runs never audit a stale
        view of the evidence.
        """
        if force_remap or not self.gcs_client.blob_exists(DOC_MAP_PATH):
            if force_remap:
                logging.info("--force flag is set. Re-creating document classification map.")
            else:
                logging.warning(f"Document map not found at '{DOC_MAP_PATH}'. Triggering creation.")
            await self._create_document_map()
        else:
             logging.info(f"Using existing document map from '{DOC_MAP_PATH}'.")

        try:
            map_data = self.gcs_client.read_json(DOC_MAP_PATH)
        except NotFound:
            logging.critical(f"FATAL: Document map '{DOC_MAP_PATH}' could not be loaded, even after creation attempt. Cannot proceed.")
            raise

        doc_map_list = await self._reconcile_map_with_bucket(map_data.get("document_map", []))

        category_map = {}
        for item in doc_map_list:
            category = item.get("category")
            filename = item.get("filename")
            if category and filename:
                if category not in category_map:
                    category_map[category] = []
                category_map[category].append(filename)

        self._document_category_map = category_map
        logging.info(f"Successfully built document category map with {len(category_map)} categories.")
        self._report_document_inventory()

    async def _reconcile_map_with_bucket(self, doc_map_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Reconciles a loaded document map against the freshly listed source files.

        Drops entries whose object is gone (a poisoned map from an earlier run heals
        itself) and classifies files that are in the bucket but not yet in the map.
        Returns the reconciled list; persists it only when something changed.
        """
        source_files = set(self._all_source_files)
        if not source_files:
            return doc_map_list

        kept, stale = [], []
        for item in doc_map_list:
            (kept if item.get("filename") in source_files else stale).append(item)
        if stale:
            logging.warning(
                f"Dropping {len(stale)} document map entries that no longer exist in the bucket: "
                f"{[item.get('filename') for item in stale]}"
            )

        unclassified = sorted(source_files - {item.get("filename") for item in kept})
        if unclassified:
            logging.warning(
                f"{len(unclassified)} source document(s) are not in the document map "
                f"(uploaded after it was created). Classifying them now: {unclassified}"
            )
            try:
                kept.extend(await self._classify_files(unclassified))
            except Exception as e:
                logging.error(
                    f"Incremental classification of {len(unclassified)} new document(s) failed: {e}. "
                    "They are categorised as 'Sonstiges' so they remain visible to the audit.",
                    exc_info=True
                )
                kept.extend({"filename": name, "category": "Sonstiges"} for name in unclassified)

        if stale or unclassified:
            self.gcs_client.write_json({"document_map": kept}, DOC_MAP_PATH)
            logging.info(f"Updated document map at '{DOC_MAP_PATH}' after reconciliation with the bucket.")

        return kept

    def document_inventory(self) -> Dict[str, List[Dict[str, Any]]]:
        """Per category: every file, its format, and whether/why it reaches the model."""
        inventory: Dict[str, List[Dict[str, Any]]] = {}
        for category, names in sorted((self._document_category_map or {}).items()):
            inventory[category] = [
                {"datei": name, "format": _format_of(name), "an_modell": selected, "grund": reason}
                for name, selected, reason in select_model_documents(names)
            ]
        return inventory

    def _report_document_inventory(self) -> None:
        """Log one line per category and persist the inventory as part of the QS trail."""
        inventory = self.document_inventory()
        for category, entries in inventory.items():
            formats = sorted({e["format"] for e in entries})
            sent = [e["datei"].rsplit("/", 1)[-1] for e in entries if e["an_modell"]]
            skipped = [e["datei"].rsplit("/", 1)[-1] for e in entries if not e["an_modell"]]
            logging.info(
                f"Documents '{category}': {len(entries)} file(s), formats {formats}; "
                f"to the model: {sent or 'none'}" + (f"; not sent: {skipped}" if skipped else "")
            )
        unreadable = [e["datei"] for entries in inventory.values() for e in entries if e["format"] == "other"]
        if unreadable:
            logging.warning(f"{len(unreadable)} file(s) in a format the model cannot read; ask for PDF or XLSX: {unreadable}")
        try:
            self.gcs_client.upload_from_string(json.dumps(inventory, indent=2, ensure_ascii=False), DOCUMENT_INVENTORY_PATH)
        except Exception as e:  # noqa: BLE001 — the inventory is a trail, not a dependency
            logging.warning(f"Could not save the document inventory to {DOCUMENT_INVENTORY_PATH}: {e}")

    def _select_filenames(self, source_categories: List[str] = None) -> List[str]:
        """Blob names of the documents in the given categories (all documents if None).

        Empty when the requested categories hold no documents — callers must handle that
        case (attaching unrelated documents instead would make the model answer about the
        wrong evidence).
        """
        if self._document_category_map is None:
            raise RuntimeError("Document map has not been initialized. Call `await RagClient.create()`.")

        selected_filenames = set()

        if source_categories:
            for category in source_categories:
                filenames = self._document_category_map.get(category, [])
                selected_filenames.update(filenames)
            if not selected_filenames:
                 logging.warning(f"No documents found for categories: {source_categories}. Returning no context documents.")
        else:
            selected_filenames.update(self._all_source_files)

        return sorted(selected_filenames)

    def get_blob_names_for_categories(self, source_categories: List[str] = None, suffixes: tuple = None) -> List[str]:
        """Blob names (not URIs) in the given categories, optionally limited to file suffixes.

        This is the accessor for files the pipeline parses itself, e.g. a Grundschutz-Check
        delivered as a spreadsheet; `get_gcs_uris_for_categories` serves the model and only
        ever returns PDFs.
        """
        names = self._select_filenames(source_categories)
        if suffixes:
            lowered = tuple(s.lower() for s in suffixes)
            names = [n for n in names if n.lower().endswith(lowered)]
        return names

    def get_gcs_uris_for_categories(self, source_categories: List[str] = None) -> List[str]:
        """
        Finds the GCS URIs for documents belonging to the specified categories.

        Args:
            source_categories: A list of BSI categories (e.g., 'Strukturanalyse').
                               If None, all source document URIs are returned.

        Returns:
            A list of 'gs://...' URIs for the model to use as context: PDFs (attached as
            file parts) and spreadsheets (rendered as row text by the AI client). A
            spreadsheet replaces a PDF of the same name; other formats are skipped.
        """
        names = self._select_filenames(source_categories)
        decisions = select_model_documents(names)
        chosen = [name for name, selected, _ in decisions if selected]
        for name, selected, reason in decisions:
            if not selected:
                logging.info(f"Not sent to the model for {source_categories or 'all categories'}: '{name}' ({reason}).")

        uris = [f"gs://{self.config.bucket_name}/{fname}" for fname in chosen]

        if self.config.is_test_mode and len(uris) > MAX_FILES_TEST_MODE:
            logging.warning(f"TEST MODE: Limiting context files from {len(uris)} to {MAX_FILES_TEST_MODE}.")
            return uris[:MAX_FILES_TEST_MODE]

        return uris