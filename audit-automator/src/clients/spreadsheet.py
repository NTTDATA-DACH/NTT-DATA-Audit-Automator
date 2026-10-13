# src/clients/spreadsheet.py
"""Reading spreadsheets (XLSX/XLSM/CSV) into rows, and rendering them as text for the model.

Gemini accepts PDFs, images and plain text as file parts, not office formats. A customer
spreadsheet therefore reaches the model as its row text, which is also the better input:
exact cell values instead of a page image, far fewer tokens, and row numbers the model can
cite as evidence. The Grundschutz-Check parser builds on the same row reader.
"""
import csv
import datetime
import io
import logging
from typing import Any, List, Sequence, Tuple

SHEET_SUFFIXES = (".xlsx", ".xlsm", ".csv")

# Above this many characters a sheet is still sent (the model takes a million tokens), but
# the log says so, because a sheet this size usually means an export of the wrong scope.
LARGE_SHEET_CHARS = 1_500_000


def cell_text(value: Any) -> str:
    """Cell value as text; dates become ISO so CSV and XLSX cells normalise the same way."""
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _csv_rows(data: bytes) -> List[List[str]]:
    """German Excel writes ';' and cp1252 unless 'CSV UTF-8' was chosen; both are accepted."""
    text = None
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("CSV is neither UTF-8 nor Windows-1252 encoded.")
    sample = text[:4096]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=";,\t").delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def read_sheet_rows(data: bytes, filename: str) -> List[Tuple[str, List[Sequence[Any]]]]:
    """(sheet name, rows) for every sheet of an XLSX/XLSM, or the single table of a CSV."""
    if filename.lower().endswith(".csv"):
        return [("csv", _csv_rows(data))]
    import openpyxl  # lazy: only spreadsheet input needs it

    workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        return [(ws.title, [list(r) for r in ws.iter_rows(values_only=True)]) for ws in workbook.worksheets]
    finally:
        workbook.close()


def render_sheet_text(data: bytes, filename: str) -> str:
    """One text block per sheet: 'Zeile N: cell | cell | ...', empty rows and trailing cells dropped.

    The row numbers are the spreadsheet's own, so a finding can cite 'Zeile 42' and an
    auditor finds it in the original file.
    """
    basename = filename.rsplit("/", 1)[-1]
    lines = [f"Dokument: {basename} (Tabellenkalkulation; jede Zeile als \"Zeile N: Zelle | Zelle | ...\")"]
    for sheet_name, rows in read_sheet_rows(data, filename):
        lines.append("")
        lines.append(f"## Tabellenblatt '{sheet_name}'")
        for row_no, row in enumerate(rows, start=1):
            cells = [cell_text(c) for c in row]
            while cells and not cells[-1]:
                cells.pop()
            if not any(cells):
                continue
            lines.append(f"Zeile {row_no}: " + " | ".join(cells))
    text = "\n".join(lines)
    if len(text) > LARGE_SHEET_CHARS:
        logging.warning(f"Spreadsheet '{basename}' renders to {len(text):,} characters (~{len(text) // 4:,} tokens): check the export scope.")
    return text
