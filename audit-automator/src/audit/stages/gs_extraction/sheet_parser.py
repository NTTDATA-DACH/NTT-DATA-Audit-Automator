# bsi-audit-automator/src/audit/stages/gs_extraction/sheet_parser.py
"""Deterministic parser for a Grundschutz-Check (A.4) delivered as a spreadsheet.

GRC tools export the check as a vertical key/value layout rather than a flat table. Every
field the extraction stage needs has its own cell, so no AI is involved: the result is
computed, which is a stronger evidence class than generated.

Layout assumptions (taken from a customer export of October 2026; a tool that exports
differently shows up as "0 requirements" in the log and fails the stage, never as wrong data):

* An info block at the top: label/value rows such as "Datum:", "Version:", "Autor:".
* A Zielobjekt section is opened by a row with a single non-empty cell ("Hauptsitz Musterstadt",
  "S001 Fileserver") or by an outermost row (first column) that is neither a label nor an ID
  and carries the Kürzel in the first and the name in a later cell ("A-002" | "Active Directory
  Domain Services" in a Verinice export). In a single cell a leading or bracketed token like
  S001 or SVR-01 is the Kürzel; without one, the name doubles as Kürzel until the system map
  supplies the real one. The Informationsverbund itself (the section named like the document's
  "Informationsverbund:" header) gets the fixed Kürzel the audit plan uses for it.
* A Zielobjekt is the pair (Kürzel, name). Exports are not always clean: a customer may put a
  type word into the Kürzel column ("Firewall", "GP", "Gruppe") and the real identifier into the name
  column ("FW-01", "N-EXTERNERN"). A Kürzel shared by several Zielobjekte identifies none of them, so
  those objects get the canonical Kürzel "<Kürzel> <Name>" ("Firewall FW-01"); the document's
  spellings stay on the Zielobjekt as `kuerzel_im_dokument` and `aliases`, and the collisions are
  reported so the audit can name the data-quality issue. The Informationsverbund is never renamed.
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

from .zielobjekt_index import canonical_kuerzel, normalize_key, zielobjekte_from_anforderungen

# Bump when the parse of the same sheet changes meaning (Zielobjekt identity, fields). The
# extraction stage re-parses a stored result written by an older version instead of reusing it.
SHEET_PARSER_VERSION = 2

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

_norm = normalize_key


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


def _add_alias(entry: Dict[str, Any], *values: Any) -> None:
    aliases = entry.setdefault("aliases", [])
    known = {_norm(a) for a in aliases}
    for value in values:
        text = str(value or "").strip()
        if text and _norm(text) not in known:
            aliases.append(text)
            known.add(_norm(text))


def _zielobjekt_entry(kuerzel: str, name: str) -> Dict[str, Any]:
    entry = {"kuerzel": kuerzel, "name": name, "kuerzel_im_dokument": kuerzel, "aliases": []}
    _add_alias(entry, kuerzel, name)
    return entry


def assign_canonical_kuerzel(anforderungen: List[Dict[str, Any]], zielobjekte: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Gives Zielobjekte that share a document Kürzel the canonical Kürzel "<Kürzel> <Name>".

    Works on the registry entries and on the requirements in place and returns the
    collisions ({document Kürzel: [names]}). Only Zielobjekte that carry requirements count:
    the export's category rows share nothing with anybody. The Informationsverbund keeps the
    fixed Kürzel the audit plan addresses it by. Running it twice changes nothing.
    """
    used = {(a.get("zielobjekt_kuerzel"), a.get("zielobjekt_name")) for a in anforderungen}
    names_by_document_kuerzel: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for z in zielobjekte:
        if z["kuerzel"] == INFORMATIONSVERBUND_KUERZEL or (z["kuerzel"], z["name"]) not in used:
            continue
        document_kuerzel = z.get("kuerzel_im_dokument") or z["kuerzel"]
        names_by_document_kuerzel.setdefault(document_kuerzel, {}).setdefault(_norm(z["name"]), z)

    collisions: Dict[str, List[str]] = {}
    for document_kuerzel, by_name in names_by_document_kuerzel.items():
        if len(by_name) < 2:
            continue
        collisions[document_kuerzel] = sorted(z["name"] for z in by_name.values())
        for z in by_name.values():
            old = (z["kuerzel"], z["name"])
            z["kuerzel"] = canonical_kuerzel(document_kuerzel, z["name"])
            z["kuerzel_im_dokument"] = document_kuerzel
            _add_alias(z, document_kuerzel, z["name"])
            if z["kuerzel"] == old[0]:
                continue
            for a in anforderungen:
                if (a.get("zielobjekt_kuerzel"), a.get("zielobjekt_name")) == old:
                    a["zielobjekt_kuerzel"] = z["kuerzel"]
    return collisions


def mapping_from_anforderungen(anforderungen: Iterable[Dict[str, Any]]) -> Dict[str, List[str]]:
    """{Baustein ID: [Zielobjekt Kürzel, ...]} as the requirements imply it."""
    mapping: Dict[str, List[str]] = {}
    for a in anforderungen:
        baustein = str(a.get("id", "")).rsplit(".A", 1)[0]
        kuerzel = a.get("zielobjekt_kuerzel")
        if baustein and kuerzel:
            targets = mapping.setdefault(baustein, [])
            if kuerzel not in targets:
                targets.append(kuerzel)
    return mapping


def parse_rows(rows: Iterable[Sequence[Any]]) -> Dict[str, Any]:
    """Walk one sheet's rows with the state machine described in the module docstring."""
    header: Dict[str, str] = {}
    zielobjekte: Dict[Tuple[str, str], Dict[str, Any]] = {}
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

        if _BAUSTEIN_RE.match(first):
            current = None  # a Baustein row only ends the previous requirement
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
            zielobjekte.setdefault(current_zo, _zielobjekt_entry(kuerzel, name))
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

    used = {(a["zielobjekt_kuerzel"], a["zielobjekt_name"]) for a in anforderungen}
    registry = [z for key, z in zielobjekte.items() if key in used]
    # Requirements under a Zielobjekt the walk never opened (the Informationsverbund fallback)
    # still need a registry entry, so every Kürzel the file carries can be resolved.
    for kuerzel, name in sorted(used - set(zielobjekte)):
        registry.append(_zielobjekt_entry(kuerzel, name))
    collisions = assign_canonical_kuerzel(anforderungen, registry)
    return {
        "anforderungen": anforderungen,
        "zielobjekte": registry,
        "kuerzel_kollisionen": collisions,
        "baustein_to_zielobjekt_mapping": mapping_from_anforderungen(anforderungen),
        "dokument": {
            "informationsverbund_name": header.get("information", ""),
            "datum": document_date,
            "version": header.get("version", ""),
            "autor": header.get("autor", ""),
        },
    }


def parse_grundschutz_check(data: bytes, filename: str) -> Dict[str, Any]:
    """Parse every sheet of the file and merge the results."""
    merged: Dict[str, Any] = {"anforderungen": [], "zielobjekte": [], "kuerzel_kollisionen": {},
                              "baustein_to_zielobjekt_mapping": {}, "dokument": {}}
    seen_zo: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for sheet_name, rows in read_sheet_rows(data, filename):
        result = parse_rows(rows)
        logging.info(f"Sheet '{sheet_name}' of '{filename}': {len(result['anforderungen'])} requirements, {len(result['zielobjekte'])} Zielobjekte.")
        merged["anforderungen"].extend(result["anforderungen"])
        for z in result["zielobjekte"]:
            key = (z.get("kuerzel_im_dokument") or z["kuerzel"], _norm(z["name"]))
            if key in seen_zo:
                _add_alias(seen_zo[key], *z.get("aliases", []))
            else:
                seen_zo[key] = z
                merged["zielobjekte"].append(z)
        if not merged["dokument"].get("datum") and result["dokument"].get("datum"):
            merged["dokument"] = result["dokument"]
    # A Kürzel unique within each sheet may still be shared across sheets: resolve once over
    # the whole file, then derive the mapping from the final Kürzel.
    merged["kuerzel_kollisionen"] = assign_canonical_kuerzel(merged["anforderungen"], merged["zielobjekte"])
    merged["baustein_to_zielobjekt_mapping"] = mapping_from_anforderungen(merged["anforderungen"])
    if merged["kuerzel_kollisionen"]:
        logging.warning(
            f"'{filename}': Kürzel shared by several Zielobjekte "
            f"{ {k: len(v) for k, v in merged['kuerzel_kollisionen'].items()} }; they are addressed as '<Kürzel> <Name>'."
        )
    return merged


def reconcile_zielobjekte(
    anforderungen: List[Dict[str, Any]],
    system_map: Dict[str, Any],
    zielobjekte: Optional[List[Dict[str, Any]]] = None,
) -> int:
    """Replace the sheet's Zielobjekt Kürzel with the system map's where name or Kürzel match.

    Stage 3's coverage check compares the Kürzel of the modelled Zielobjekte with those in the
    check, so both must speak the same identifiers. Works on the registry (`zielobjekte`, derived
    from the requirements when not given) and the requirements in place; the map's spellings are
    kept as aliases either way. Returns the number of changed requirements.

    Never rewritten: the Informationsverbund (it keeps the fixed Kürzel the audit plan addresses
    it by), an entry whose map match has no Kürzel, and anything whose target would not identify
    one Zielobjekt: a map Kürzel listed with several names, a document Kürzel the sheet itself
    found shared ("Firewall FW-01" must not fall back to "Firewall" through the name hop "FW-01"), or a
    Kürzel another Zielobjekt of the sheet already carries under a different name.
    """
    registry = zielobjekte if zielobjekte is not None else zielobjekte_from_anforderungen(anforderungen)
    for z in registry:
        z.setdefault("kuerzel_im_dokument", z["kuerzel"])
        _add_alias(z, z["kuerzel"], z.get("name"))

    map_entries = [z for z in (system_map.get("zielobjekte") or []) if z.get("kuerzel") or z.get("name")]
    names_by_map_kuerzel: Dict[str, set] = {}
    by_key: Dict[str, Dict[str, Any]] = {}
    for z in map_entries:
        if z.get("kuerzel"):
            names_by_map_kuerzel.setdefault(_norm(z["kuerzel"]), set()).add(_norm(z.get("name")))
        for key in (z.get("kuerzel"), z.get("name")):
            if key:
                by_key.setdefault(_norm(str(key)), z)

    shared_in_sheet = {z["kuerzel_im_dokument"] for z in registry if z["kuerzel_im_dokument"] != z["kuerzel"]}
    changed = 0
    for z in registry:
        if z["kuerzel"] == INFORMATIONSVERBUND_KUERZEL:
            continue
        match = by_key.get(_norm(z["kuerzel"])) or by_key.get(_norm(z.get("name", "")))
        if not match or not match.get("kuerzel"):
            continue
        target = str(match["kuerzel"]).strip()
        _add_alias(z, target, match.get("name"))
        if target == z["kuerzel"]:
            continue
        if len(names_by_map_kuerzel.get(_norm(target), ())) > 1 or target in shared_in_sheet:
            continue
        if any(o is not z and o["kuerzel"] == target and _norm(o.get("name")) != _norm(z.get("name")) for o in registry):
            continue
        old = (z["kuerzel"], z.get("name", ""))
        z["kuerzel"] = target
        z["name"] = str(match.get("name") or z.get("name", "")).strip()
        for a in anforderungen:
            if (a.get("zielobjekt_kuerzel"), a.get("zielobjekt_name", "")) == old:
                a["zielobjekt_kuerzel"] = z["kuerzel"]
                a["zielobjekt_name"] = z["name"]
                changed += 1
    return changed
