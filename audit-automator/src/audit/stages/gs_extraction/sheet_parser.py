# bsi-audit-automator/src/audit/stages/gs_extraction/sheet_parser.py
"""Deterministic parser for a Grundschutz-Check (A.4) delivered as a spreadsheet.

GRC tools export the check as a vertical key/value layout rather than a flat table. Every
field the extraction stage needs has its own cell, so no AI is involved: the result is
computed, which is a stronger evidence class than generated.

Layout assumptions (taken from a customer export of October 2026; a tool that exports
differently shows up as "0 requirements" in the log and fails the stage, never as wrong data):

* An info block at the top: label/value rows such as "Datum:", "Version:", "Autor:".
* A Zielobjekt section is opened by a row with a single non-empty cell ("Hauptsitz Rostock",
  "S001 Fileserver") or by an outermost row (first column) that is neither a label nor an ID
  and carries the Kürzel in the first and the name in a later cell ("AE-002" | "Active Directory
  Domain Services" in a Verinice export). In a single cell a leading or bracketed token like
  S001 or SVR-01 is the Kürzel; without one, the name doubles as Kürzel until the system map
  supplies the real one. The Informationsverbund itself (the section named like the document's
  "Informationsverbund:" header) gets the fixed Kürzel the audit plan uses for it.
* Category rows of the export ("Anwendungen", "IT-Systeme", "Räume") look like Zielobjekt
  headers too; they open a section no requirement falls into and are dropped.
* A row starting with a Baustein ID (ISMS.1, SYS.1.2.2) opens that Baustein under the current
  Zielobjekt; a row starting with a requirement ID (ISMS.1.A1) opens a requirement.
* The rows below a requirement are label/value pairs: "Umsetzungsstatus:",
  "Umsetzungserläuterung:" and, if the tool exports one, a check date ("Letzte Prüfung").
  "Umsetzung bis" is a target date, not a check date, and is ignored.
* A value sits in the first non-empty cell after its label. A requirement without a check
  date gets the document date from the info block.
"""
import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.clients.spreadsheet import cell_text as _cell_text, read_sheet_rows
from src.constants import INFORMATIONSVERBUND_KUERZEL

# "ISMS.1.A1  Titel" / "ISMS.1.A1" alone (title in the next cell). Checked before the Baustein
# pattern, which would otherwise match the "ISMS.1" prefix of a requirement ID.
_ANFORDERUNG_RE = re.compile(r"^(?P<id>[A-Z]{2,6}(?:\.\d+)+\.A\d+)\b[\s:\-–]*(?P<rest>.*)$", re.S)
_BAUSTEIN_RE = re.compile(r"^(?P<id>[A-Z]{2,6}(?:\.\d+)+)\b[\s:\-–]*(?P<rest>.*)$", re.S)
# "S001 Fileserver", "SVR-01 - Datei-Server", "Fileserver (S001)"
_KUERZEL_LEAD_RE = re.compile(r"^(?P<k>[A-Z]{1,6}[-_]?\d{1,4}[A-Za-z0-9\-]*)\s*[\-–:]?\s+(?P<name>.+)$")
_KUERZEL_TAIL_RE = re.compile(r"^(?P<name>.+?)\s*[\(\[](?P<k>[A-Z]{1,6}[-_]?\d{1,4}[A-Za-z0-9\-]*)[\)\]]$")
_GERMAN_DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

# Status spellings seen in exports -> the values the prompts and schemas use. Order matters:
# "nicht umgesetzt" must win over "umgesetzt", "nicht relevant" over nothing.
_STATUS_PREFIXES = (
    ("nicht umgesetzt", "Nein"),
    ("nicht relevant", "entbehrlich"),
    ("entbehrlich", "entbehrlich"),
    ("n/a", "entbehrlich"),
    ("teilweise", "teilweise"),
    ("partiell", "teilweise"),
    ("umgesetzt", "Ja"),
    ("ja", "Ja"),
    ("nein", "Nein"),
)


def _norm(text: str) -> str:
    return (text.strip().rstrip(":").strip().lower()
            .replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))


def _label_kind(text: str) -> Optional[str]:
    """Classify a label cell; None means the cell is not a label."""
    label = _norm(text)
    if label.startswith("umsetzungsstatus"):
        return "status"
    if label.startswith("umsetzungserl"):
        return "erlaeuterung"
    if "pruef" in label:  # "Letzte Prüfung", "Datum der Prüfung", "geprüft am"
        return "pruefdatum"
    if label.startswith("umsetzung"):  # "Umsetzung bis", "Umsetzung durch": target data, ignored
        return "umsetzung"
    if label.startswith("beschreibung"):
        return "beschreibung"
    if label.endswith("verantwortlicher"):
        return "verantwortlicher"
    if label in ("datum", "version", "autor", "freigabe", "vorgehensweise", "mitarbeiter", "geltungsbereich"):
        return label
    if label in ("information", "informationsverbund", "name"):
        return "information"
    if label in ("abkuerzung", "kuerzel"):
        return "abkuerzung"
    if text.rstrip().endswith(":"):
        return "other_label"
    return None


def normalize_status(text: str) -> str:
    key = text.strip().lower()
    if not key:
        return ""
    for prefix, normalized in _STATUS_PREFIXES:
        if key.startswith(prefix):
            return normalized
    return text.strip()


def normalize_date(text: str) -> str:
    """'7.7.2026' or '2026-07-07' -> '2026-07-07'; anything else is passed through as seen."""
    text = text.strip()
    m = _ISO_DATE_RE.search(text)
    if m:
        return f"{m[1]}-{m[2]}-{m[3]}"
    m = _GERMAN_DATE_RE.search(text)
    if m:
        return f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}"
    return text


def split_kuerzel(text: str) -> Tuple[str, str]:
    """(kuerzel, name) of a Zielobjekt header; without a recognisable token both are the text."""
    text = text.strip()
    m = _KUERZEL_LEAD_RE.match(text) or _KUERZEL_TAIL_RE.match(text)
    if m:
        return m["k"], m["name"].strip()
    return text, text


def parse_rows(rows: Iterable[Sequence[Any]]) -> Dict[str, Any]:
    """Walk one sheet's rows with the state machine described in the module docstring."""
    header: Dict[str, str] = {}
    zielobjekte: Dict[str, Dict[str, str]] = {}
    mapping: Dict[str, List[str]] = {}
    anforderungen: List[Dict[str, Any]] = []
    current_zo: Optional[Tuple[str, str]] = None
    current: Optional[Dict[str, Any]] = None

    for row_no, row in enumerate(rows, start=1):
        cells = [_cell_text(c) for c in row]
        nonempty = [t for t in cells if t]
        if not nonempty:
            continue
        first = nonempty[0]
        value = nonempty[1] if len(nonempty) > 1 else ""

        m = _ANFORDERUNG_RE.match(first)
        if m:
            kuerzel, name = current_zo or (INFORMATIONSVERBUND_KUERZEL, header.get("information", ""))
            current = {
                "id": m["id"],
                "titel": (m["rest"].strip() or value).strip(),
                "umsetzungsstatus": "",
                "umsetzungserlaeuterung": "",
                "datumLetztePruefung": "",
                "pagenumber": row_no,  # a sheet has no pages: the row number locates the entry
                "zielobjekt_kuerzel": kuerzel,
                "zielobjekt_name": name,
            }
            anforderungen.append(current)
            continue

        m = _BAUSTEIN_RE.match(first)
        if m:
            current = None
            if current_zo:
                targets = mapping.setdefault(m["id"], [])
                if current_zo[0] not in targets:
                    targets.append(current_zo[0])
            continue

        kind = _label_kind(first)
        if kind is None:
            first_col = next(i for i, t in enumerate(cells) if t)
            if len(nonempty) == 1:
                kuerzel, name = split_kuerzel(first)
            elif first_col == 0:
                kuerzel, name = first, nonempty[-1]
            else:
                continue  # a value without a label (continuation line): nothing to assign it to
            if header.get("information") and _norm(name) == _norm(header["information"]):
                kuerzel = INFORMATIONSVERBUND_KUERZEL
            current_zo = (kuerzel, name)
            zielobjekte.setdefault(kuerzel, {"kuerzel": kuerzel, "name": name})
            current = None
            continue

        if current is not None:
            if kind == "status":
                current["umsetzungsstatus"] = normalize_status(value)
            elif kind == "erlaeuterung":
                current["umsetzungserlaeuterung"] = value
            elif kind == "pruefdatum":
                current["datumLetztePruefung"] = normalize_date(value)
            continue

        if value and kind not in ("other_label", "beschreibung", "verantwortlicher", "umsetzung"):
            header.setdefault(kind, value)

    document_date = normalize_date(header.get("datum", ""))
    for a in anforderungen:
        if not a["datumLetztePruefung"]:
            a["datumLetztePruefung"] = document_date

    used = {a["zielobjekt_kuerzel"] for a in anforderungen}
    return {
        "anforderungen": anforderungen,
        "zielobjekte": [z for k, z in zielobjekte.items() if k in used],
        "baustein_to_zielobjekt_mapping": {
            b: [k for k in ks if k in used] for b, ks in mapping.items() if any(k in used for k in ks)
        },
        "dokument": {
            "informationsverbund_name": header.get("information", ""),
            "datum": document_date,
            "version": header.get("version", ""),
            "autor": header.get("autor", ""),
        },
    }


def parse_grundschutz_check(data: bytes, filename: str) -> Dict[str, Any]:
    """Parse every sheet of the file and merge the results."""
    merged: Dict[str, Any] = {"anforderungen": [], "zielobjekte": [], "baustein_to_zielobjekt_mapping": {}, "dokument": {}}
    seen_zo = set()
    for sheet_name, rows in read_sheet_rows(data, filename):
        result = parse_rows(rows)
        logging.info(f"Sheet '{sheet_name}' of '{filename}': {len(result['anforderungen'])} requirements, {len(result['zielobjekte'])} Zielobjekte.")
        merged["anforderungen"].extend(result["anforderungen"])
        for z in result["zielobjekte"]:
            if z["kuerzel"] not in seen_zo:
                seen_zo.add(z["kuerzel"])
                merged["zielobjekte"].append(z)
        for baustein, kuerzel_list in result["baustein_to_zielobjekt_mapping"].items():
            targets = merged["baustein_to_zielobjekt_mapping"].setdefault(baustein, [])
            targets.extend(k for k in kuerzel_list if k not in targets)
        if not merged["dokument"].get("datum") and result["dokument"].get("datum"):
            merged["dokument"] = result["dokument"]
    return merged


def reconcile_zielobjekte(anforderungen: List[Dict[str, Any]], system_map: Dict[str, Any]) -> int:
    """Replace the sheet's Zielobjekt Kürzel with the system map's where name or Kürzel match.

    Stage 3's coverage check compares the Kürzel of the modelled Zielobjekte with those in the
    check, so both must speak the same identifiers. Returns the number of changed entries;
    unmatched entries keep the sheet's own values. Two entries are never touched: the
    Informationsverbund keeps the fixed Kürzel the audit plan addresses it by (the map often
    lists it without one, or under a name), and a map entry without a Kürzel cannot identify
    anything and so never replaces a Kürzel the sheet provided.
    """
    by_key: Dict[str, Dict[str, Any]] = {}
    for z in system_map.get("zielobjekte", []) or []:
        for key in (z.get("kuerzel"), z.get("name")):
            if key:
                by_key.setdefault(_norm(str(key)), z)
    changed = 0
    for a in anforderungen:
        if a.get("zielobjekt_kuerzel") == INFORMATIONSVERBUND_KUERZEL:
            continue
        match = by_key.get(_norm(a.get("zielobjekt_kuerzel", ""))) or by_key.get(_norm(a.get("zielobjekt_name", "")))
        if match and match.get("kuerzel") and a.get("zielobjekt_kuerzel") != match["kuerzel"]:
            a["zielobjekt_kuerzel"] = match["kuerzel"]
            a["zielobjekt_name"] = match.get("name") or a.get("zielobjekt_name", "")
            changed += 1
    return changed
