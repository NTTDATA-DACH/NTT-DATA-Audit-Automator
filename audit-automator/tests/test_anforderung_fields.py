"""Tests for the shared normalisation of the two audit-critical requirement fields.

Both fields come from customer PDFs. The failure modes these guard against:
  * a status in the wrong case slipping past an exact-match filter, so the report
    attests that all unmet requirements are documented while none were checked;
  * an unparsable date silently becoming 1970-01-01 and producing a fabricated
    "last checked more than 12 months ago" deviation.
"""
from datetime import datetime

import pytest

from src.audit.stages.gs_extraction.anforderung_fields import (
    MISSING_DATE_SENTINEL,
    STATUS_ENTBEHRLICH,
    STATUS_JA,
    STATUS_NEIN,
    STATUS_TEILWEISE,
    normalize_anforderungen,
    normalize_status,
    parse_pruefdatum,
)


@pytest.mark.parametrize("raw,expected", [
    ("Ja", STATUS_JA),
    ("ja", STATUS_JA),
    ("  JA  ", STATUS_JA),
    ("umgesetzt", STATUS_JA),
    ("Nein", STATUS_NEIN),
    ("nein", STATUS_NEIN),
    ("nicht umgesetzt", STATUS_NEIN),
    ("teilweise", STATUS_TEILWEISE),
    ("Teilweise", STATUS_TEILWEISE),
    ("teilweise umgesetzt", STATUS_TEILWEISE),
    ("entbehrlich", STATUS_ENTBEHRLICH),
    ("Entbehrlich", STATUS_ENTBEHRLICH),
])
def test_known_statuses_map_to_the_canonical_spelling(raw, expected):
    assert normalize_status(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "vielleicht", 42, {"status": "Ja"}])
def test_unknown_statuses_are_none_never_a_passing_value(raw):
    assert normalize_status(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("2025-03-01", datetime(2025, 3, 1)),
    ("01.03.2025", datetime(2025, 3, 1)),
    ("01.03.25", datetime(2025, 3, 1)),
    (" 2025-03-01 ", datetime(2025, 3, 1)),
])
def test_known_date_formats_parse(raw, expected):
    assert parse_pruefdatum(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "03/2025-ish", "Q1 2025", "unbekannt", 20250301, MISSING_DATE_SENTINEL])
def test_unparsable_dates_are_none_never_an_old_date(raw):
    """None must NOT be substituted with 1970 — that fabricates a deviation."""
    assert parse_pruefdatum(raw) is None


def test_normalize_anforderungen_rewrites_in_place_and_keeps_unmappable_items():
    items = [
        {"id": "A1", "umsetzungsstatus": "nein"},
        {"id": "A2", "umsetzungsstatus": "Teilweise"},
        {"id": "A3", "umsetzungsstatus": "unklar"},
        {"id": "A4"},
    ]
    result = normalize_anforderungen(items)

    assert [a.get("umsetzungsstatus") for a in result] == [STATUS_NEIN, STATUS_TEILWEISE, "unklar", None]
    assert len(result) == 4  # nothing is dropped; the report must still show them
