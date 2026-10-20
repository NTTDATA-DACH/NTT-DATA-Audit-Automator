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
    ["Information", "Hauptsitz Musterstadt"],
    ["Abkürzung:", ""],
    ["Mitarbeiter:", "ca. 50"],
    ["Datum:", "07.10.2026"],
    ["Autor:", "Max Muster"],
    ["Version:", "2.6"],
    ["Vorgehensweise", "STANDARD"],
    ["Informationsverbund"],
    [None, None, None],
    ["Hauptsitz Musterstadt"],
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
    # The section named like the document's Informationsverbund gets the plan's fixed Kürzel.
    assert set(by_id) == {("ISMS.1.A1", "Informationsverbund"), ("ISMS.1.A2", "Informationsverbund"),
                          ("SYS.1.1.A1", "S001"), ("SYS.1.1.A2", "S001")}

    a1 = by_id[("ISMS.1.A1", "Informationsverbund")]
    assert a1["titel"].startswith("Übernahme der Gesamtverantwortung")
    assert a1["umsetzungsstatus"] == "Ja"
    assert "Managementbericht" in a1["umsetzungserlaeuterung"]
    assert a1["datumLetztePruefung"] == "2026-10-07"  # no check date in the export: document date
    assert a1["pagenumber"] == 15  # the row number stands in for the page
    assert a1["zielobjekt_name"] == "Hauptsitz Musterstadt"

    assert by_id[("ISMS.1.A2", "Informationsverbund")]["umsetzungsstatus"] == "Nein"

    s1 = by_id[("SYS.1.1.A1", "S001")]
    assert s1["titel"] == "Geeignete Aufstellung [IT-Betrieb]"  # ID and title in one cell
    assert s1["umsetzungsstatus"] == "teilweise"
    assert s1["datumLetztePruefung"] == "2026-09-01"  # an explicit check date wins
    assert s1["zielobjekt_name"] == "Fileserver"
    assert by_id[("SYS.1.1.A2", "S001")]["umsetzungsstatus"] == "entbehrlich"


def test_parse_rows_builds_zielobjekte_and_mapping_and_drops_empty_sections():
    result = parse_rows(ROWS)
    assert [(z["kuerzel"], z["name"]) for z in result["zielobjekte"]] == [
        ("Informationsverbund", "Hauptsitz Musterstadt"),
        ("S001", "Fileserver"),
    ]
    # The document's spellings stay on the Zielobjekt, so later stages can resolve them.
    assert result["zielobjekte"][1]["kuerzel_im_dokument"] == "S001"
    assert result["zielobjekte"][1]["aliases"] == ["S001", "Fileserver"]
    assert result["kuerzel_kollisionen"] == {}
    # "A.4 IT-Grundschutz-Check" and "Informationsverbund" are single-cell rows without requirements.
    assert all(z["name"] not in ("A.4 IT-Grundschutz-Check", "Informationsverbund") for z in result["zielobjekte"])
    assert result["baustein_to_zielobjekt_mapping"] == {"ISMS.1": ["Informationsverbund"], "SYS.1.1": ["S001"]}
    assert result["dokument"] == {"informationsverbund_name": "Hauptsitz Musterstadt", "datum": "2026-10-07",
                                  "version": "2.6", "autor": "Max Muster"}


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
    assert split_kuerzel("Hauptsitz Musterstadt") == ("Hauptsitz Musterstadt", "Hauptsitz Musterstadt")


def test_reconcile_uses_the_system_map_kuerzel_where_names_match():
    anforderungen = parse_rows(ROWS)["anforderungen"]
    system_map = {"zielobjekte": [{"kuerzel": "HQ-01", "name": "Hauptsitz Musterstadt"}, {"kuerzel": "SRV-01", "name": "Fileserver"}]}
    changed = reconcile_zielobjekte(anforderungen, system_map)
    # The Fileserver takes the map's Kürzel; the Informationsverbund keeps the plan's fixed one
    # even though the map lists it under HQ-01, because Chapter 4 and 5 address it that way.
    assert changed == 2
    assert {a["zielobjekt_kuerzel"] for a in anforderungen} == {"Informationsverbund", "SRV-01"}


def test_reconcile_renames_the_registry_entry_and_keeps_the_sheet_spellings_as_aliases():
    parsed = parse_rows(ROWS)
    system_map = {"zielobjekte": [{"kuerzel": "SRV-01", "name": "Datei-Server"}, {"kuerzel": "S001", "name": "Fileserver"}]}
    # "S001" matches by Kürzel first, so the map's own spelling for it wins over the second entry.
    reconcile_zielobjekte(parsed["anforderungen"], system_map, parsed["zielobjekte"])
    fileserver = next(z for z in parsed["zielobjekte"] if "Fileserver" in z["aliases"])
    assert fileserver["kuerzel"] == "S001" and fileserver["kuerzel_im_dokument"] == "S001"

    parsed = parse_rows(ROWS)
    system_map = {"zielobjekte": [{"kuerzel": "SRV-01", "name": "Fileserver"}]}
    assert reconcile_zielobjekte(parsed["anforderungen"], system_map, parsed["zielobjekte"]) == 2
    fileserver = next(z for z in parsed["zielobjekte"] if z["kuerzel"] == "SRV-01")
    assert fileserver["kuerzel_im_dokument"] == "S001"
    assert set(fileserver["aliases"]) == {"S001", "Fileserver", "SRV-01"}
    assert {a["zielobjekt_kuerzel"] for a in parsed["anforderungen"] if a["id"].startswith("SYS")} == {"SRV-01"}


def test_excel_carriage_return_escape_is_removed_from_cell_text():
    rows = VERINICE_ROWS[:9]
    rows[8] = [None, "Umsetzungserläuterung:", "Vom Vorstand getragen._x000D_\n_x000D_\nAudit: 08.06.2026"]
    result = parse_rows(rows)
    assert result["anforderungen"][0]["umsetzungserlaeuterung"] == "Vom Vorstand getragen.\n\nAudit: 08.06.2026"


def test_reconcile_never_writes_an_empty_kuerzel_from_the_map():
    anforderungen = [{"id": "SYS.1.1.A1", "zielobjekt_kuerzel": "S001", "zielobjekt_name": "Fileserver"}]
    system_map = {"zielobjekte": [{"kuerzel": "", "name": "Fileserver"}]}
    assert reconcile_zielobjekte(anforderungen, system_map) == 0
    assert anforderungen[0]["zielobjekt_kuerzel"] == "S001"


# A Verinice export: category rows in the first column, Zielobjekt rows with the Kürzel in the
# first and the name in the fourth column, Bausteine, requirements and labels indented by one.
VERINICE_ROWS = [
    ["A.4 IT-Grundschutz-Check"],
    ["Informationsverbund:", "Hauptsitz Musterstadt"],
    ["Datum:", "07.10.2026"],
    ["Informationsverbund"],
    [None, None, None, "Hauptsitz Musterstadt"],
    [None, "ISMS.1", "Sicherheitsmanagement"],
    [None, "ISMS.1.A1", None, "Übernahme der Gesamtverantwortung"],
    [None, "Umsetzungsstatus:", "Ja"],
    [None, "Umsetzungserläuterung:", "Vom Vorstand getragen."],
    ["Anwendungen"],
    ["A-002", None, None, "Active Directory Domain Services"],
    [None, "APP.2.2", "Active Directory Domain Services"],
    [None, "Beschreibung:", "Verzeichnisdienst."],
    [None, "APP.2.2.A1", None, "Planung des Active Directory"],
    [None, "Umsetzung durch:", "Umsetzung bis:", "7.7.2026"],
    [None, "Umsetzungsstatus:", "teilweise"],
    [None, "Umsetzungserläuterung:", "Planungsdokument liegt vor, Rollenmodell fehlt."],
    ["IT-Systeme"],
    ["S-007", None, None, "Sprungserver"],
    [None, "SYS.1.2.3", "Windows Server"],
    [None, "SYS.1.2.3.A1", None, "Planung von Windows Server"],
    [None, "Umsetzungsstatus:", "Ja"],
    [None, "Umsetzungserläuterung:", "Gehärtet nach CIS Benchmark."],
]


def test_verinice_layout_attaches_requirements_to_the_zielobjekt_not_the_category():
    result = parse_rows(VERINICE_ROWS)
    by_key = {(a["id"], a["zielobjekt_kuerzel"]): a for a in result["anforderungen"]}
    assert set(by_key) == {("ISMS.1.A1", "Informationsverbund"), ("APP.2.2.A1", "A-002"), ("SYS.1.2.3.A1", "S-007")}
    assert by_key[("APP.2.2.A1", "A-002")]["umsetzungserlaeuterung"] == "Planungsdokument liegt vor, Rollenmodell fehlt."
    assert by_key[("APP.2.2.A1", "A-002")]["zielobjekt_name"] == "Active Directory Domain Services"
    assert by_key[("SYS.1.2.3.A1", "S-007")]["umsetzungsstatus"] == "Ja"
    # The category rows opened no section with requirements and are gone.
    assert [z["kuerzel"] for z in result["zielobjekte"]] == ["Informationsverbund", "A-002", "S-007"]
    assert result["baustein_to_zielobjekt_mapping"] == {
        "ISMS.1": ["Informationsverbund"], "APP.2.2": ["A-002"], "SYS.1.2.3": ["S-007"],
    }


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


# A Verinice export in which the Kürzel column carries a type word and the name column the
# identifier (a customer export of October 2026): "Firewall" stands for four firewalls.
COLLIDING_ROWS = [
    ["A.4 IT-Grundschutz-Check"],
    ["Informationsverbund:", "Hauptsitz Musterstadt"],
    ["Datum:", "07.10.2026"],
    ["Informationsverbund"],
    [None, None, None, "Hauptsitz Musterstadt"],
    [None, "ISMS.1", "Sicherheitsmanagement"],
    [None, "ISMS.1.A1", None, "Übernahme der Gesamtverantwortung"],
    [None, "Umsetzungsstatus:", "Ja"],
    ["IT-Systeme"],
    ["Firewall", None, None, "FW-01"],
    [None, "NET.3.2", "Firewall"],
    [None, "NET.3.2.A1", None, "Erstellung einer Sicherheitsrichtlinie"],
    [None, "Umsetzungsstatus:", "Ja"],
    ["Firewall", None, None, "FW-02"],
    [None, "NET.3.2", "Firewall"],
    [None, "NET.3.2.A1", None, "Erstellung einer Sicherheitsrichtlinie"],
    [None, "Umsetzungsstatus:", "teilweise"],
    ["GP", None, None, "Portal"],
    [None, "SYS.1.1", "Allgemeiner Server"],
    [None, "SYS.1.1.A1", None, "Geeignete Aufstellung"],
    [None, "Umsetzungsstatus:", "Ja"],
]


def test_zielobjekte_sharing_a_kuerzel_are_kept_apart_under_kuerzel_plus_name():
    result = parse_rows(COLLIDING_ROWS)
    by_key = {(a["id"], a["zielobjekt_kuerzel"]): a for a in result["anforderungen"]}
    # Nothing is lost: the two firewalls keep their own NET.3.2.A1 with their own status.
    assert set(by_key) == {("ISMS.1.A1", "Informationsverbund"), ("NET.3.2.A1", "Firewall FW-01"),
                           ("NET.3.2.A1", "Firewall FW-02"), ("SYS.1.1.A1", "GP")}
    assert by_key[("NET.3.2.A1", "Firewall FW-01")]["umsetzungsstatus"] == "Ja"
    assert by_key[("NET.3.2.A1", "Firewall FW-02")]["umsetzungsstatus"] == "teilweise"
    assert by_key[("NET.3.2.A1", "Firewall FW-01")]["zielobjekt_name"] == "FW-01"
    # A unique Kürzel stays as the document wrote it, the Informationsverbund keeps the fixed one
    # (the export's category row "Informationsverbund" shares that Kürzel but carries nothing).
    its_002 = next(z for z in result["zielobjekte"] if z["kuerzel"] == "Firewall FW-01")
    assert its_002["kuerzel_im_dokument"] == "Firewall" and its_002["aliases"] == ["Firewall", "FW-01"]
    assert result["kuerzel_kollisionen"] == {"Firewall": ["FW-01", "FW-02"]}
    assert result["baustein_to_zielobjekt_mapping"] == {
        "ISMS.1": ["Informationsverbund"], "NET.3.2": ["Firewall FW-01", "Firewall FW-02"], "SYS.1.1": ["GP"],
    }


def test_collision_pass_is_idempotent():
    from src.audit.stages.gs_extraction.sheet_parser import assign_canonical_kuerzel

    result = parse_rows(COLLIDING_ROWS)
    before = ([dict(z) for z in result["zielobjekte"]], [dict(a) for a in result["anforderungen"]])
    assert assign_canonical_kuerzel(result["anforderungen"], result["zielobjekte"]) == {"Firewall": ["FW-01", "FW-02"]}
    assert (result["zielobjekte"], result["anforderungen"]) == before


def test_kuerzel_shared_across_sheets_is_resolved_over_the_whole_file():
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    first = workbook.active
    for row in COLLIDING_ROWS[:13]:  # up to and including Firewall | FW-01
        first.append(row)
    second = workbook.create_sheet("Teil 2")
    for row in COLLIDING_ROWS[:3] + COLLIDING_ROWS[13:17]:  # info block, then Firewall | FW-02
        second.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)

    result = parse_grundschutz_check(buffer.getvalue(), "source_documents/A.4.xlsx")
    assert {a["zielobjekt_kuerzel"] for a in result["anforderungen"] if a["id"] == "NET.3.2.A1"} == {"Firewall FW-01", "Firewall FW-02"}
    assert result["kuerzel_kollisionen"] == {"Firewall": ["FW-01", "FW-02"]}
    assert result["baustein_to_zielobjekt_mapping"]["NET.3.2"] == ["Firewall FW-01", "Firewall FW-02"]


def test_reconcile_never_writes_a_map_kuerzel_that_several_map_entries_share():
    parsed = parse_rows(COLLIDING_ROWS)
    # The system map (model-read from A.1) carries the same swapped columns.
    system_map = {"zielobjekte": [{"kuerzel": "Firewall", "name": "FW-01"}, {"kuerzel": "Firewall", "name": "FW-02"},
                                  {"kuerzel": "GP", "name": "Portal"}]}
    assert reconcile_zielobjekte(parsed["anforderungen"], system_map, parsed["zielobjekte"]) == 0
    assert {a["zielobjekt_kuerzel"] for a in parsed["anforderungen"]} == {"Informationsverbund", "Firewall FW-01", "Firewall FW-02", "GP"}


def test_reconcile_does_not_undo_a_canonical_kuerzel_through_the_name_hop():
    parsed = parse_rows(COLLIDING_ROWS)
    # Only one firewall in the map: "FW-01" matches by name and would pull the Kürzel back to "Firewall".
    system_map = {"zielobjekte": [{"kuerzel": "Firewall", "name": "FW-01"}]}
    assert reconcile_zielobjekte(parsed["anforderungen"], system_map, parsed["zielobjekte"]) == 0
    assert {a["zielobjekt_kuerzel"] for a in parsed["anforderungen"] if a["id"] == "NET.3.2.A1"} == {"Firewall FW-01", "Firewall FW-02"}


def test_reconcile_refuses_a_rewrite_that_would_collide_in_the_sheet():
    anforderungen = [
        {"id": "SYS.1.1.A1", "zielobjekt_kuerzel": "S001", "zielobjekt_name": "Fileserver"},
        {"id": "SYS.1.1.A1", "zielobjekt_kuerzel": "S002", "zielobjekt_name": "Backup-Server"},
    ]
    system_map = {"zielobjekte": [{"kuerzel": "S002", "name": "Fileserver"}]}  # the map's S002 is a different object
    assert reconcile_zielobjekte(anforderungen, system_map) == 0
    assert [a["zielobjekt_kuerzel"] for a in anforderungen] == ["S001", "S002"]
