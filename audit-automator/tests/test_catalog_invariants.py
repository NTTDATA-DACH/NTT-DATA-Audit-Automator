"""Invariant tests for the committed BSI Kompendium catalog.

These run against the generated artifact (assets/json/bsi_kompendium_ed2023.json), not
against the XML, so they guard what the pipeline actually reads. They are the structural
replacement for the old hand-delivered OSCAL catalog, which shipped 44 malformed control
IDs (inf-5-a1 instead of INF.5.A1) that silently disabled the MUSS coverage check for
INF.5, INF.9 and SYS.1.8.
"""

import json

import pytest

from src.constants import CONTROL_CATALOG_PATH
from src.tools.build_bsi_catalog import (
    CANONICAL_REQ_ID_RE,
    EXPECTED_ANFORDERUNGEN_AKTIV,
    EXPECTED_BAUSTEINE,
    EXPECTED_ENTFALLEN,
    VALID_LEVELS,
    assert_invariants,
)
from src.tools.ed23_xml import BSI_XML_SHA256


@pytest.fixture(scope="module")
def catalog():
    with open(CONTROL_CATALOG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def test_meta_is_pinned_to_the_official_edition(catalog):
    meta = catalog["meta"]
    assert meta["edition"] == "2023"
    assert meta["sha256"] == BSI_XML_SHA256
    assert meta["source_url"].endswith("XML_Kompendium_2023.xml?__blob=publicationFile&v=4")


def test_counts_match_the_pinned_edition(catalog):
    counts = catalog["meta"]["counts"]
    assert counts["bausteine"] == EXPECTED_BAUSTEINE
    assert counts["anforderungen_aktiv"] == EXPECTED_ANFORDERUNGEN_AKTIV
    assert counts["entfallen"] == EXPECTED_ENTFALLEN
    aktiv = [a for a in catalog["anforderungen"] if not a["entfallen"]]
    assert len(aktiv) == counts["anforderungen_aktiv"]


def test_committed_catalog_satisfies_build_invariants(catalog):
    """The same gate the converter applies — so a hand-edited catalog fails CI too."""
    assert_invariants(catalog)


def test_every_requirement_id_is_canonical(catalog):
    bad = [a["id"] for a in catalog["anforderungen"] if not CANONICAL_REQ_ID_RE.match(a["id"])]
    assert bad == []


def test_no_duplicate_ids(catalog):
    ids = [a["id"] for a in catalog["anforderungen"]]
    assert len(ids) == len(set(ids))
    baustein_ids = [b["id"] for b in catalog["bausteine"]]
    assert len(baustein_ids) == len(set(baustein_ids))


def test_active_requirements_carry_a_valid_level(catalog):
    bad = [a["id"] for a in catalog["anforderungen"] if not a["entfallen"] and a["level"] not in VALID_LEVELS]
    assert bad == []


def test_previously_broken_bausteine_are_present_and_canonical(catalog):
    """Regression guard for the malformed-ID class of defects in the old catalog."""
    by_baustein = {}
    for a in catalog["anforderungen"]:
        by_baustein.setdefault(a["baustein"], []).append(a)
    for baustein_id in ("INF.5", "INF.9", "SYS.1.8"):
        entries = by_baustein.get(baustein_id, [])
        assert entries, f"{baustein_id} fehlt im Katalog"
        assert all(a["id"].startswith(f"{baustein_id}.A") for a in entries)
        assert any(a["level"] == "B" and not a["entfallen"] for a in entries), (
            f"{baustein_id} hat keine MUSS-Anforderung — der alte Silent-Drop wäre zurück"
        )


def test_every_requirement_belongs_to_a_listed_baustein(catalog):
    baustein_ids = {b["id"] for b in catalog["bausteine"]}
    orphans = {a["baustein"] for a in catalog["anforderungen"]} - baustein_ids
    assert orphans == set()


def test_custom_bausteine_are_gone(catalog):
    """The old catalog carried 10 'Benutzerdefiniert:' Bausteine; the official XML has none."""
    assert not any("Benutzerdefiniert" in b["titel"] for b in catalog["bausteine"])
    for custom_id in ("ORP.6", "DER.7", "APP.4.10", "SYS.1.10", "SYS.1.11", "OPS.1.1.8"):
        assert custom_id not in {b["id"] for b in catalog["bausteine"]}
