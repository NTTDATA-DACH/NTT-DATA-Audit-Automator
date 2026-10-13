"""Tests for the deterministic Grundschutz-Check spreadsheet parser.

The row-level state machine is pure Python and runs everywhere; the XLSX round trip needs
openpyxl (a project dependency) and the CSV variant exercises the German-Excel defaults:
semicolons, Windows-1252, dates as dd.mm.yyyy, multi-line quoted cells.
"""
import datetime
import io

import pytest

# The parser itself is pure Python, but its package imports PyMuPDF at package level.
pytest.importorskip("fitz")

from src.audit.stages.gs_extraction.sheet_parser import (
    normalize_date,
    normalize_status,
    parse_grundschutz_check,
    parse_rows,
    reconcile_zielobjekte,
    split_kuerzel,
)

# Mirrors the customer export: info block, a Zielobjekt header row with one cell, Baustein and
# requirement rows with the ID in one cell and the title in the next, label/value rows below.
ROWS = [
    ["A.4 IT-Grundschutz-Check"],
    ["Information", "Hauptsitz Rostock"],
    ["Abkürzung:", ""],
    ["Mitarbeiter:", "ca. 50"],
    ["Datum:", "07.10.2026"],
    ["Autor:", "Patrick Nimmich"],
    ["Version:", "2.6"],
    ["Vorgehensweise", "STANDARD"],
    ["Informationsverbund"],
    [None, None, None],
    ["Hauptsitz Rostock"],
    [None, "ISMS.1", "Sicherheitsmanagement"],
    [None, "Beschreibung:", "Planungs-, Lenkungs- und Kontrollaufgabe."],
    [None, "Hauptverantwortlicher:", None],
    [None, "ISMS.1.A1", "Übernahme der Gesamtverantwortung für Informationssicherheit durch die Leitungsebene [Institutionsleitung]"],
    [None, "Beschreibung:", "Leitungsebene der Institution muss den Sicherheitsprozess initiieren."],
    [None, "Umsetzung durch", "Umsetzung bis 7.7.2026"],
    [None, "Umsetzungsstatus:", "Ja"],
    [None, "Umsetzungserläuterung:", "Die Informationssicherheit wird durch die Geschäftsführung getragen.\nEs gibt einen Managementbericht."],
    [None, "ISMS.1.A2", "Festlegung der Sicherheitsziele und -strategie [Institutionsleitung]"],
    [None, "Umsetzungsstatus:", "nicht umgesetzt"],
    [None, "Umsetzungserläuterung:", ""],
    [None, "S001 Fileserver"],
    [None, "SYS.1.1", "Allgemeiner Server"],
    [None, "SYS.1.1.A1 Geeignete Aufstellung [IT-Betrieb]"],
    [None, "Umsetzungsstatus:", "teilweise"],
    [None, "Letzte Prüfung:", datetime.datetime(2026, 9, 1)],
    [None, "Umsetzungserläuterung:", "Rack ist abschließbar."],
    [None, "SYS.1.1.A2"],
    [None, "Umsetzungsstatus:", "entbehrlich"],
    [None, "Umsetzungserläuterung:", "Kein Benutzerbetrieb."],
]


def test_parse_rows_extracts_requirements_with_their_zielobjekt():
    result = parse_rows(ROWS)
    by_id = {(a["id"], a["zielobjekt_kuerzel"]): a for a in result["anforderungen"]}
    assert set(by_id) == {("ISMS.1.A1", "Hauptsitz Rostock"), ("ISMS.1.A2", "Hauptsitz Rostock"),
                          ("SYS.1.1.A1", "S001"), ("SYS.1.1.A2", "S001")}

    a1 = by_id[("ISMS.1.A1", "Hauptsitz Rostock")]
    assert a1["titel"].startswith("Übernahme der Gesamtverantwortung")
    assert a1["umsetzungsstatus"] == "Ja"
    assert "Managementbericht" in a1["umsetzungserlaeuterung"]
    assert a1["datumLetztePruefung"] == "2026-10-07"  # no check date in the export: document date
    assert a1["pagenumber"] == 15  # the row number stands in for the page
    assert a1["zielobjekt_name"] == "Hauptsitz Rostock"

    assert by_id[("ISMS.1.A2", "Hauptsitz Rostock")]["umsetzungsstatus"] == "Nein"

    s1 = by_id[("SYS.1.1.A1", "S001")]
    assert s1["titel"] == "Geeignete Aufstellung [IT-Betrieb]"  # ID and title in one cell
    assert s1["umsetzungsstatus"] == "teilweise"
    assert s1["datumLetztePruefung"] == "2026-09-01"  # an explicit check date wins
    assert s1["zielobjekt_name"] == "Fileserver"
    assert by_id[("SYS.1.1.A2", "S001")]["umsetzungsstatus"] == "entbehrlich"


def test_parse_rows_builds_zielobjekte_and_mapping_and_drops_empty_sections():
    result = parse_rows(ROWS)
    assert result["zielobjekte"] == [
        {"kuerzel": "Hauptsitz Rostock", "name": "Hauptsitz Rostock"},
        {"kuerzel": "S001", "name": "Fileserver"},
    ]
    # "A.4 IT-Grundschutz-Check" and "Informationsverbund" are single-cell rows without requirements.
    assert all(z["name"] not in ("A.4 IT-Grundschutz-Check", "Informationsverbund") for z in result["zielobjekte"])
    assert result["baustein_to_zielobjekt_mapping"] == {"ISMS.1": ["Hauptsitz Rostock"], "SYS.1.1": ["S001"]}
    assert result["dokument"] == {"informationsverbund_name": "Hauptsitz Rostock", "datum": "2026-10-07",
                                  "version": "2.6", "autor": "Patrick Nimmich"}


def test_helpers_normalise_status_dates_and_kuerzel():
    assert normalize_status("ja") == "Ja"
    assert normalize_status("Umgesetzt") == "Ja"
    assert normalize_status("nicht umgesetzt") == "Nein"
    assert normalize_status("Partiell") == "teilweise"
    assert normalize_status("nicht relevant") == "entbehrlich"
    assert normalize_status("") == ""
    assert normalize_status("in Planung") == "in Planung"  # unknown values pass through for the audit
    assert normalize_date("7.7.2026") == "2026-07-07"
    assert normalize_date("Umsetzung bis 07.10.2026") == "2026-10-07"
    assert normalize_date("2026-10-07") == "2026-10-07"
    assert normalize_date("") == ""
    assert split_kuerzel("S001 Fileserver") == ("S001", "Fileserver")
    assert split_kuerzel("SVR-01 - Datei-Server") == ("SVR-01", "Datei-Server")
    assert split_kuerzel("Datei-Server (SVR-01)") == ("SVR-01", "Datei-Server")
    assert split_kuerzel("Hauptsitz Rostock") == ("Hauptsitz Rostock", "Hauptsitz Rostock")


def test_reconcile_uses_the_system_map_kuerzel_where_names_match():
    anforderungen = parse_rows(ROWS)["anforderungen"]
    system_map = {"zielobjekte": [{"kuerzel": "HQ-01", "name": "Hauptsitz Rostock"}, {"kuerzel": "S001", "name": "Fileserver"}]}
    changed = reconcile_zielobjekte(anforderungen, system_map)
    assert changed == 2
    assert {a["zielobjekt_kuerzel"] for a in anforderungen} == {"HQ-01", "S001"}


def test_xlsx_round_trip_matches_the_row_parse():
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in ROWS:
        sheet.append(row)
    sheet.merge_cells("A11:F11")  # the grey Zielobjekt header is a merged row in the export
    buffer = io.BytesIO()
    workbook.save(buffer)

    result = parse_grundschutz_check(buffer.getvalue(), "source_documents/A.4 Grundschutz-Check.xlsx")
    assert [a["id"] for a in result["anforderungen"]] == ["ISMS.1.A1", "ISMS.1.A2", "SYS.1.1.A1", "SYS.1.1.A2"]
    assert result["anforderungen"][2]["datumLetztePruefung"] == "2026-09-01"  # datetime cell
    assert result["dokument"]["datum"] == "2026-10-07"


def test_csv_in_german_excel_defaults_is_parsed():
    lines = []
    for row in ROWS:
        cells = []
        for value in row:
            if isinstance(value, datetime.datetime):
                value = value.strftime("%d.%m.%Y")
            text = "" if value is None else str(value)
            cells.append('"' + text.replace('"', '""') + '"' if ("\n" in text or ";" in text) else text)
        lines.append(";".join(cells))
    data = "\r\n".join(lines).encode("cp1252")

    result = parse_grundschutz_check(data, "source_documents/A.4.csv")
    by_id = {a["id"]: a for a in result["anforderungen"]}
    assert set(by_id) == {"ISMS.1.A1", "ISMS.1.A2", "SYS.1.1.A1", "SYS.1.1.A2"}
    assert by_id["ISMS.1.A1"]["titel"].startswith("Übernahme der Gesamtverantwortung")  # umlaut survived cp1252
    assert "Managementbericht" in by_id["ISMS.1.A1"]["umsetzungserlaeuterung"]  # multi-line quoted cell
    assert by_id["SYS.1.1.A1"]["datumLetztePruefung"] == "2026-09-01"
