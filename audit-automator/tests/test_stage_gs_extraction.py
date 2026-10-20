"""Tests for the extraction stage's choice between the spreadsheet path and the PDF path.

Offline: GCS, the document finder and the AI client are fakes; the three PDF steps and the
ground-truth mapper are replaced by recorders. Importing the stage needs google-genai and
PyMuPDF (package-level imports); skip cleanly if either is absent.
"""
import asyncio
import io
import json
import types

import pytest

pytest.importorskip("google.genai")
pytest.importorskip("fitz")
openpyxl = pytest.importorskip("openpyxl")

from src.audit.stages.gs_extraction.sheet_parser import SHEET_PARSER_VERSION
from src.audit.stages.stage_gs_check_extraction import GrundschutzCheckExtractionRunner
from src.constants import EXTRACTED_CHECK_DATA_PATH

SYSTEM_MAP = {"zielobjekte": [{"kuerzel": "S001", "name": "Fileserver"}], "baustein_to_zielobjekt_mapping": {"SYS.1.1": ["S001"]}}


def _xlsx_bytes(rows):
    workbook = openpyxl.Workbook()
    for row in rows:
        workbook.active.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class FakeGcs:
    def __init__(self, files):
        self.files = files  # blob name -> bytes
        self.uploads = {}
        self.bucket = types.SimpleNamespace(blob=lambda name: name)

    def blob_exists(self, name):
        return name in self.uploads

    def read_json(self, name):
        return json.loads(self.uploads[name])

    def download_blob_as_bytes(self, blob):
        return self.files[blob]

    async def upload_from_string_async(self, content, destination_blob_name, content_type="application/json"):
        self.uploads[destination_blob_name] = content


class FakeRag:
    def __init__(self, names):
        self.names = names
        self._document_category_map = {"Grundschutz-Check": names}

    def get_blob_names_for_categories(self, categories=None, suffixes=None):
        return [n for n in self.names if not suffixes or n.lower().endswith(tuple(suffixes))]

    def get_gcs_uris_for_categories(self, categories=None):
        return [f"gs://b/{n}" for n in self.names if n.lower().endswith(".pdf")]


def _runner(gcs, rag):
    config = types.SimpleNamespace(bucket_name="b", is_test_mode=False, gcp_project_id="p", region="eu", doc_ai_processor_name="x")
    runner = GrundschutzCheckExtractionRunner(config, gcs, None, types.SimpleNamespace(), rag)
    calls = []

    async def ground_truth(force_overwrite):
        calls.append("ground_truth")
        return SYSTEM_MAP

    async def pdf_step(*args, **kwargs):
        calls.append("pdf_step")

    runner.ground_truth_mapper.create_system_structure_map = ground_truth
    runner.document_processor.execute_layout_parser_workflow = pdf_step
    runner.block_grouper.group_layout_blocks_by_zielobjekt = pdf_step
    runner.ai_refiner.refine_grouped_blocks_with_ai = pdf_step
    return runner, calls


def test_spreadsheet_replaces_the_pdf_path():
    rows = [["Datum:", "07.10.2026"], ["Fileserver"], [None, "SYS.1.1", "Allgemeiner Server"],
            [None, "SYS.1.1.A1", "Geeignete Aufstellung"], [None, "Umsetzungsstatus:", "Ja"],
            [None, "Umsetzungserläuterung:", "Rack abschließbar."]]
    gcs = FakeGcs({"source_documents/A.4.xlsx": _xlsx_bytes(rows)})
    runner, calls = _runner(gcs, FakeRag(["source_documents/A.4.xlsx", "source_documents/Strukturanalyse.pdf"]))

    result = asyncio.run(runner.run(force_overwrite=True))
    assert result["status"] == "success"
    assert calls == ["ground_truth"]  # Document AI, grouper and refiner never ran

    written = json.loads(gcs.uploads[EXTRACTED_CHECK_DATA_PATH])
    assert written["quelle"]["verfahren"] == "spreadsheet-parser"
    [entry] = written["anforderungen"]
    assert entry["id"] == "SYS.1.1.A1"
    assert entry["zielobjekt_kuerzel"] == "S001"  # "Fileserver" matched the system map by name
    assert entry["umsetzungsstatus"] == "Ja"
    assert entry["datumLetztePruefung"] == "2026-10-07"
    # The Zielobjekt registry with the sheet's spellings as aliases, and the parser version.
    [zielobjekt] = written["zielobjekte"]
    assert zielobjekt["kuerzel"] == "S001" and set(zielobjekt["aliases"]) == {"Fileserver", "S001"}
    assert written["kuerzel_kollisionen"] == {}
    assert written["quelle"]["parser_version"] == SHEET_PARSER_VERSION


def test_without_a_spreadsheet_the_pdf_path_runs():
    runner, calls = _runner(FakeGcs({}), FakeRag(["source_documents/Grundschutz-Check.pdf"]))
    result = asyncio.run(runner.run(force_overwrite=True))
    assert result["status"] == "success"
    assert calls == ["ground_truth", "pdf_step", "pdf_step", "pdf_step"]


def test_unparseable_spreadsheet_fails_the_stage_instead_of_passing_empty_data():
    gcs = FakeGcs({"source_documents/A.4.xlsx": _xlsx_bytes([["Nur Text"], ["ohne", "Anforderungen"]])})
    runner, calls = _runner(gcs, FakeRag(["source_documents/A.4.xlsx"]))
    # The stage raises (exit 1) instead of returning a status dict: a swallowed error here
    # would let every later stage audit empty data and report success.
    with pytest.raises(ValueError, match="yielded no requirements"):
        asyncio.run(runner.run(force_overwrite=True))
    assert EXTRACTED_CHECK_DATA_PATH not in gcs.uploads


SHEET_ROWS = [["Datum:", "07.10.2026"], ["Fileserver"], [None, "SYS.1.1", "Allgemeiner Server"],
              [None, "SYS.1.1.A1", "Geeignete Aufstellung"], [None, "Umsetzungsstatus:", "Ja"]]


def test_a_result_written_by_an_older_parser_version_is_reparsed_without_force():
    gcs = FakeGcs({"source_documents/A.4.xlsx": _xlsx_bytes(SHEET_ROWS)})
    gcs.uploads[EXTRACTED_CHECK_DATA_PATH] = json.dumps(
        {"anforderungen": [], "quelle": {"verfahren": "spreadsheet-parser", "parser_version": SHEET_PARSER_VERSION - 1}}
    )
    runner, _ = _runner(gcs, FakeRag(["source_documents/A.4.xlsx"]))
    asyncio.run(runner.run(force_overwrite=False))
    written = json.loads(gcs.uploads[EXTRACTED_CHECK_DATA_PATH])
    assert written["quelle"]["parser_version"] == SHEET_PARSER_VERSION
    assert [a["id"] for a in written["anforderungen"]] == ["SYS.1.1.A1"]


def test_a_result_of_the_current_parser_version_is_reused_without_force():
    gcs = FakeGcs({"source_documents/A.4.xlsx": _xlsx_bytes(SHEET_ROWS)})
    stored = json.dumps({"anforderungen": [{"id": "ALT"}], "quelle": {"verfahren": "spreadsheet-parser", "parser_version": SHEET_PARSER_VERSION}})
    gcs.uploads[EXTRACTED_CHECK_DATA_PATH] = stored
    runner, _ = _runner(gcs, FakeRag(["source_documents/A.4.xlsx"]))
    asyncio.run(runner.run(force_overwrite=False))
    assert gcs.uploads[EXTRACTED_CHECK_DATA_PATH] == stored


def test_requirements_lost_in_deduplication_are_reported(monkeypatch, caplog):
    import logging
    from src.audit.stages import stage_gs_check_extraction as module

    rows = SHEET_ROWS + [[None, "SYS.1.1.A2", "Zutritt"], [None, "Umsetzungsstatus:", "Ja"]]
    gcs = FakeGcs({"source_documents/A.4.xlsx": _xlsx_bytes(rows)})
    runner, _ = _runner(gcs, FakeRag(["source_documents/A.4.xlsx"]))
    # A sheet yields one row per (requirement, Zielobjekt); anything the dedup removes means
    # two Zielobjekte collapsed. Simulate that collapse to pin the warning.
    monkeypatch.setattr(module.DataProcessor, "deduplicate_requirements", staticmethod(lambda items: items[:-1]))
    with caplog.at_level(logging.WARNING):
        asyncio.run(runner.run(force_overwrite=True))
    assert "Deduplication removed 1 of 2" in caplog.text


def test_controller_skip_check_knows_about_the_parser_version():
    gcs = FakeGcs({})
    assert GrundschutzCheckExtractionRunner.is_extraction_current(gcs) is False  # nothing stored yet
    gcs.uploads["output/intermediate/gs_extraction/system_structure_map.json"] = "{}"
    gcs.uploads[EXTRACTED_CHECK_DATA_PATH] = json.dumps({"quelle": {"verfahren": "spreadsheet-parser"}})  # pre-versioned
    assert GrundschutzCheckExtractionRunner.is_extraction_current(gcs) is False
    gcs.uploads[EXTRACTED_CHECK_DATA_PATH] = json.dumps({"quelle": {"verfahren": "spreadsheet-parser", "parser_version": SHEET_PARSER_VERSION}})
    assert GrundschutzCheckExtractionRunner.is_extraction_current(gcs) is True
    gcs.uploads[EXTRACTED_CHECK_DATA_PATH] = json.dumps({"quelle": {"verfahren": "document-ai"}})  # the PDF path has no version
    assert GrundschutzCheckExtractionRunner.is_extraction_current(gcs) is True
