"""Tests for the per-category format decision: what reaches the model, in which form.

Offline: the document finder is built via __new__ with a prepared category map, the AI
client gets a fake GCS client that serves an in-memory workbook. Importing these modules
needs google-genai (and openpyxl for the workbook); skip cleanly if absent.
"""
import asyncio
import io
import json
import types

import pytest

pytest.importorskip("google.genai")
openpyxl = pytest.importorskip("openpyxl")

from src.clients.ai_client import AiClient
from src.clients.rag_client import RagClient, select_model_documents
from src.clients.spreadsheet import render_sheet_text
from src.constants import DOCUMENT_INVENTORY_PATH

FILES = [
    "source_documents/A.1 Strukturanalyse.pdf",
    "source_documents/A.1 Strukturanalyse.xlsx",
    "source_documents/Netzplan.pdf",
    "source_documents/Notizen.docx",
]


def _xlsx(rows):
    workbook = openpyxl.Workbook()
    for row in rows:
        workbook.active.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_sheet_replaces_pdf_twin_and_unreadable_formats_are_skipped():
    decisions = {name: (selected, reason) for name, selected, reason in select_model_documents(FILES)}
    assert decisions["source_documents/A.1 Strukturanalyse.xlsx"][0] is True
    assert decisions["source_documents/A.1 Strukturanalyse.pdf"] == (False, "a spreadsheet with the same name is sent instead")
    assert decisions["source_documents/Netzplan.pdf"][0] is True  # no sheet twin: the PDF goes
    assert decisions["source_documents/Notizen.docx"][0] is False
    assert "PDF or XLSX" in decisions["source_documents/Notizen.docx"][1]


def _rag():
    rag = RagClient.__new__(RagClient)
    rag.config = types.SimpleNamespace(bucket_name="b", is_test_mode=False)
    rag._document_category_map = {"Strukturanalyse": FILES}
    rag._all_source_files = list(FILES)
    rag.gcs_client = types.SimpleNamespace(uploads={}, upload_from_string=lambda content, destination_blob_name, content_type="application/json": rag.gcs_client.uploads.__setitem__(destination_blob_name, content))
    return rag


def test_document_finder_returns_model_ready_uris_only():
    rag = _rag()
    uris = rag.get_gcs_uris_for_categories(["Strukturanalyse"])
    assert uris == ["gs://b/source_documents/A.1 Strukturanalyse.xlsx", "gs://b/source_documents/Netzplan.pdf"]
    assert rag.get_blob_names_for_categories(["Strukturanalyse"], suffixes=(".xlsx",)) == ["source_documents/A.1 Strukturanalyse.xlsx"]


def test_inventory_lists_every_file_with_its_decision_and_is_persisted():
    rag = _rag()
    inventory = rag.document_inventory()
    entries = {e["datei"]: e for e in inventory["Strukturanalyse"]}
    assert entries["source_documents/A.1 Strukturanalyse.xlsx"]["format"] == "sheet"
    assert entries["source_documents/A.1 Strukturanalyse.pdf"]["an_modell"] is False
    assert entries["source_documents/Notizen.docx"]["format"] == "other"
    rag._report_document_inventory()
    assert json.loads(rag.gcs_client.uploads[DOCUMENT_INVENTORY_PATH]) == inventory


def test_render_sheet_text_keeps_row_numbers_and_drops_empty_cells():
    data = _xlsx([["Zielobjekt", "Name", None, None], [], ["S001", "Fileserver", "", None], ["", "", ""]])
    text = render_sheet_text(data, "source_documents/A.1 Strukturanalyse.xlsx")
    assert text.startswith("Dokument: A.1 Strukturanalyse.xlsx")
    assert "## Tabellenblatt 'Sheet'" in text
    assert "Zeile 1: Zielobjekt | Name" in text
    assert "Zeile 3: S001 | Fileserver" in text
    assert "Zeile 2" not in text and "Zeile 4" not in text


def test_ai_client_sends_pdfs_as_file_parts_and_sheets_as_text():
    client = AiClient.__new__(AiClient)
    client._rendered_docs = {}
    downloads = []

    class FakeBucket:
        name = "b"

        def blob(self, name):
            return name

    def download(blob_name):
        downloads.append(blob_name)
        return _xlsx([["S001", "Fileserver"]])

    client.gcs_client = types.SimpleNamespace(bucket=FakeBucket(), download_blob_as_bytes=download)

    async def scenario():
        first = await client._build_contents("Frage?", ["gs://b/source_documents/A.1.pdf", "gs://b/source_documents/A.1.xlsx"])
        second = await client._build_contents("Frage?", ["gs://b/source_documents/A.1.xlsx"])
        return first, second

    first, second = asyncio.run(scenario())
    # Documents first, the varying prompt last: implicit caching discounts the repeated prefix.
    assert first[-1] == "Frage?"
    assert first[0].file_data.file_uri == "gs://b/source_documents/A.1.pdf"
    assert "Zeile 1: S001 | Fileserver" in first[1].text
    assert downloads == ["source_documents/A.1.xlsx"]  # rendered once, reused by the second call
    assert second[0].text == first[1].text and second[-1] == "Frage?"
    with pytest.raises(ValueError, match="Unsupported document format"):
        asyncio.run(client._build_contents("x", ["gs://b/source_documents/Notizen.docx"]))
