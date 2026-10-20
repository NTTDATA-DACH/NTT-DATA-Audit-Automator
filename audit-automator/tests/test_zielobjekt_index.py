"""Tests for the Zielobjekt alias index.

The customer's Verinice export puts type words into the Kürzel column ("Firewall", "GP", "Gruppe")
and the identifier into the name column; the model's system map and the audit plan use yet
other spellings. Every consumer resolves through this index, so the behaviour pinned here is:
any spelling resolves to the canonical Kürzel, an ambiguous spelling to all candidates, an
unknown one to nothing.
"""
import pytest

pytest.importorskip("fitz")  # the gs_extraction package imports PyMuPDF at package level

from src.audit.stages.gs_extraction.zielobjekt_index import (
    ZielobjektIndex,
    canonical_kuerzel,
    normalize_key,
    zielobjekte_from_anforderungen,
)

FIREWALLS = [
    {"kuerzel": f"Firewall {name}", "name": name, "kuerzel_im_dokument": "Firewall", "aliases": ["Firewall", name]}
    for name in ("FW-01", "FW-02", "FW-03", "FW-04")
]
REGISTRY = FIREWALLS + [
    {"kuerzel": "A-002", "name": "Active Directory Domain Services", "aliases": ["A-002", "Active Directory Domain Services"]},
    {"kuerzel": "R-001", "name": "Archivräume (Lager)"},
    {"kuerzel": "Informationsverbund", "name": "Hauptsitz Musterstadt", "aliases": ["Hauptsitz Musterstadt", "HQ-01"]},
]


def test_helpers():
    assert normalize_key(" Lagerräume: ") == "lagerraeume"
    assert normalize_key(None) == ""
    assert canonical_kuerzel("Firewall ", " FW-01") == "Firewall FW-01"


def test_resolves_kuerzel_name_and_alias_regardless_of_case_and_umlauts():
    index = ZielobjektIndex(REGISTRY)
    assert index.resolve("A-002") == {"A-002"}
    assert index.resolve("active directory domain services") == {"A-002"}
    assert index.resolve("ARCHIVRAEUME (LAGER)") == {"R-001"}
    assert index.resolve("HQ-01") == {"Informationsverbund"}  # an alias the system map supplied
    assert index.resolve("FW-01") == {"Firewall FW-01"}  # the document's name column is the identifier
    assert index.resolve("Firewall FW-02") == {"Firewall FW-02"}


def test_shared_document_kuerzel_is_ambiguous_and_reported_as_collision():
    index = ZielobjektIndex(REGISTRY)
    assert index.resolve("Firewall") == {f"Firewall FW-0{n}" for n in (1, 2, 3, 4)}
    assert index.is_ambiguous("Firewall") and not index.is_ambiguous("FW-01")
    assert index.collisions() == {"Firewall": ["FW-01", "FW-02", "FW-03", "FW-04"]}


def test_unknown_identifier_resolves_to_nothing():
    index = ZielobjektIndex(REGISTRY)
    assert index.resolve("FW-ALL") == set()
    assert index.resolve("") == set()
    assert not index.is_ambiguous("FW-ALL")


def test_registry_derived_from_requirements_for_the_pdf_path():
    anforderungen = [
        {"id": "NET.1.1.A1", "zielobjekt_kuerzel": "GP", "zielobjekt_name": "Cluster-A"},
        {"id": "NET.1.1.A2", "zielobjekt_kuerzel": "GP", "zielobjekt_name": "Cluster-A"},
        {"id": "SYS.1.1.A1", "zielobjekt_kuerzel": "GP", "zielobjekt_name": "Portal"},
        {"id": "ISMS.1.A1", "zielobjekt_kuerzel": "Informationsverbund", "zielobjekt_name": "Hauptsitz Musterstadt"},
        {"id": "ISMS.1.A2", "zielobjekt_kuerzel": "", "zielobjekt_name": "ohne Kürzel"},
    ]
    registry = zielobjekte_from_anforderungen(anforderungen)
    assert [(z["kuerzel"], z["name"]) for z in registry] == [
        ("GP", "Cluster-A"), ("GP", "Portal"), ("Informationsverbund", "Hauptsitz Musterstadt"),
    ]
    index = ZielobjektIndex.from_anforderungen(anforderungen)
    assert index.resolve("Portal") == {"GP"}
    assert index.collisions() == {"GP": ["Cluster-A", "Portal"]}
    assert index.canonical == ["GP", "Informationsverbund"]
