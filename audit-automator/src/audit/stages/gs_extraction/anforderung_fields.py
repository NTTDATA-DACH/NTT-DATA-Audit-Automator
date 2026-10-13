# bsi-audit-automator/src/audit/stages/gs_extraction/anforderung_fields.py
"""Normalisation of the two Grundschutz-Check fields the audit verdicts hinge on.

`umsetzungsstatus` and `datumLetztePruefung` are extracted from customer PDFs, so
their wording and formatting vary. Every consumer used to re-implement its own
comparison, which is how a 'nein' slipped past a `in ["Nein", "teilweise"]` filter
and an unparsable date silently became 1970-01-01 — a fabricated "last checked more
than 12 months ago" deviation. Normalise once, here, and compare on the result.
"""
import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

# The canonical spellings, as declared in stage_3_6_1_extract_check_data_schema.json.
STATUS_JA = "Ja"
STATUS_NEIN = "Nein"
STATUS_TEILWEISE = "teilweise"
STATUS_ENTBEHRLICH = "entbehrlich"
VALID_STATUS = (STATUS_JA, STATUS_NEIN, STATUS_TEILWEISE, STATUS_ENTBEHRLICH)

# Wordings seen in real A.4 exports, mapped onto the canonical values.
_STATUS_SYNONYMS = {
    "ja": STATUS_JA,
    "umgesetzt": STATUS_JA,
    "vollständig umgesetzt": STATUS_JA,
    "vollstaendig umgesetzt": STATUS_JA,
    "nein": STATUS_NEIN,
    "nicht umgesetzt": STATUS_NEIN,
    "offen": STATUS_NEIN,
    "teilweise": STATUS_TEILWEISE,
    "teilweise umgesetzt": STATUS_TEILWEISE,
    "teilw.": STATUS_TEILWEISE,
    "in umsetzung": STATUS_TEILWEISE,
    "entbehrlich": STATUS_ENTBEHRLICH,
    "nicht anwendbar": STATUS_ENTBEHRLICH,
    "n/a": STATUS_ENTBEHRLICH,
}

# Formats seen in the wild; the first two are what the extraction prompt asks for.
_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%d/%m/%Y", "%Y/%m/%d")

# The extraction schema names this as the fallback for "no date in the document",
# so it means "unknown", not "checked in 1970".
MISSING_DATE_SENTINEL = "1970-01-01"


def normalize_status(value: Any) -> Optional[str]:
    """Map an extracted implementation status onto one of `VALID_STATUS`.

    Returns None for a missing or unrecognised status — the caller decides whether
    that is a data-quality note or a hard failure. Never guesses a passing value.
    """
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return _STATUS_SYNONYMS.get(cleaned.lower())


def parse_pruefdatum(value: Any) -> Optional[datetime]:
    """Parse a `datumLetztePruefung` into a datetime.

    Returns None when the value is missing, the "no date" sentinel, or in a format
    we do not recognise. Callers must treat None as "not assessable" and must NOT
    substitute an old date: that would report a deviation the document never showed.
    """
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or cleaned == MISSING_DATE_SENTINEL:
        return None
    for date_format in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, date_format)
        except ValueError:
            continue
    return None


def normalize_anforderungen(anforderungen: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalise the status of every requirement in place and report what did not map.

    Returns the same list, so it can be used inline. Unmappable statuses are left
    untouched (and logged) rather than dropped: the requirement still has to appear
    in the report, flagged as a data-quality issue.
    """
    items = list(anforderungen)
    unmapped = []
    for anforderung in items:
        raw = anforderung.get("umsetzungsstatus")
        normalized = normalize_status(raw)
        if normalized:
            anforderung["umsetzungsstatus"] = normalized
        elif raw:
            unmapped.append(f"{anforderung.get('id', '?')}={raw!r}")

    if unmapped:
        logging.warning(
            f"{len(unmapped)} requirement(s) carry an unrecognised Umsetzungsstatus and are "
            f"treated as 'not assessable': {unmapped[:20]}"
        )
    return items
