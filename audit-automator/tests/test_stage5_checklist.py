"""Tests for the Chapter 5.5.2 control checklist — the one place where catalog content
becomes report content.

Offline: `_generate_control_checklist` is pure (dicts in, dicts out), so the runner is
built with __new__ and given a real ControlCatalog.
"""

import pytest

pytest.importorskip("google.cloud.storage")

from src.audit.stages.control_catalog import ControlCatalog
from src.audit.stages.stage_5_vor_ort_audit import Chapter5Runner


@pytest.fixture(scope="module")
def runner():
    instance = Chapter5Runner.__new__(Chapter5Runner)
    instance.control_catalog = ControlCatalog()
    return instance


def _chapter_4(baustein: str, kuerzel: str = "SVR-01"):
    return {
        "auswahlBausteine2Ueberwachungsaudit": {
            "rows": [{"Baustein": baustein, "Zielobjekt-Name": "Server 1", "Zielobjekt-Kürzel": kuerzel}]
        }
    }


def _pruefungen(result):
    return result["verifikationDesITGrundschutzChecks"]["einzelergebnisse"]["bausteinPruefungen"]


def test_checklist_rows_carry_official_title_and_level(runner):
    extracted = {("ISMS.1.A1", "SVR-01"): {
        "umsetzungsstatus": "Ja", "umsetzungserlaeuterung": "Leitung hat die Verantwortung übernommen.",
    }}
    result = runner._generate_control_checklist(_chapter_4("ISMS.1 Sicherheitsmanagement"), extracted)

    anforderungen = _pruefungen(result)[0]["anforderungen"]
    assert anforderungen
    first = next(a for a in anforderungen if a["nummer"] == "ISMS.1.A1")
    assert first["anforderung"].endswith("(B)")
    assert first["bewertung"] == "Umgesetzt"
    assert first["dokuAntragsteller"] == "Leitung hat die Verantwortung übernommen."
    # Every row must show its BSI level so the auditor sees the obligation.
    assert all(a["anforderung"].endswith(("(B)", "(S)", "(H)")) for a in anforderungen)


def test_entfallen_requirements_are_not_listed(runner):
    """APP.1.1.A1 is ENTFALLEN in Edition 2023 and must not reach the checklist."""
    result = runner._generate_control_checklist(_chapter_4("APP.1.1 Office-Produkte"), {})
    numbers = [a["nummer"] for a in _pruefungen(result)[0]["anforderungen"]]
    assert numbers
    assert "APP.1.1.A1" not in numbers


def test_previously_broken_baustein_now_produces_rows(runner):
    """INF.5 used to yield nothing because the old catalog stored 'inf-5-a1' style IDs."""
    result = runner._generate_control_checklist(_chapter_4("INF.5 Raum sowie Schrank"), {})
    anforderungen = _pruefungen(result)[0]["anforderungen"]
    assert anforderungen
    assert all(a["nummer"].startswith("INF.5.A") for a in anforderungen)


def test_custom_baustein_is_filled_from_the_extracted_check_data(runner):
    """A custom Baustein is not in the Kompendium, but its requirements are in the
    customer's Grundschutz-Check — dropping them would shrink the audited scope."""
    extracted = {
        ("ORP.6.A1", "SVR-01"): {
            "titel": "Zugriffsrechte in der Cloud",
            "umsetzungsstatus": "teilweise",
            "umsetzungserlaeuterung": "IAM-Rollen sind teilweise dokumentiert.",
        },
        ("ORP.6.A2", "SVR-01"): {"titel": "Rezertifizierung", "umsetzungsstatus": "Nein"},
        ("ORP.6.A1", "APP-02"): {"titel": "Anderes Zielobjekt"},  # must not leak in
    }
    result = runner._generate_control_checklist(
        _chapter_4("ORP.6 Benutzerdefiniert: Cloud-IAM"), extracted
    )
    pruefung = _pruefungen(result)[0]

    assert [a["nummer"] for a in pruefung["anforderungen"]] == ["ORP.6.A1", "ORP.6.A2"]
    assert pruefung["anforderungen"][0]["bewertung"] == "Teilweise umgesetzt"
    assert pruefung["anforderungen"][0]["dokuAntragsteller"] == "IAM-Rollen sind teilweise dokumentiert."
    assert pruefung["baustein"] == "ORP.6 Benutzerdefiniert: Cloud-IAM"


def test_unresolvable_baustein_gets_a_visible_note_not_an_empty_list(runner):
    """An empty subchapter reads as 'nothing to audit'; the gap must be stated."""
    result = runner._generate_control_checklist(_chapter_4("ORP.6 Benutzerdefiniert: Cloud-IAM"), {})
    anforderungen = _pruefungen(result)[0]["anforderungen"]
    assert len(anforderungen) == 1
    assert "manuell zu ergänzen" in anforderungen[0]["anforderung"]


@pytest.mark.parametrize("cell", [
    "ISMS.1 Sicherheitsmanagement",
    "ISMS.1: Sicherheitsmanagement",
    "ISMS.1 - Sicherheitsmanagement",
    "  ISMS.1  Sicherheitsmanagement",
    "ISMS.1",
])
def test_baustein_id_is_parsed_from_any_plausible_cell_format(runner, cell):
    """Nothing in the Chapter-4 schema enforces the 'ID Titel' separator, and an
    unparsed ID yields a subchapter with no requirements."""
    result = runner._generate_control_checklist(_chapter_4(cell), {})
    numbers = [a["nummer"] for a in _pruefungen(result)[0]["anforderungen"]]
    assert any(n.startswith("ISMS.1.A") for n in numbers), f"no controls resolved for {cell!r}"


def test_missing_customer_data_falls_back_to_a_placeholder(runner):
    result = runner._generate_control_checklist(_chapter_4("ISMS.1 Sicherheitsmanagement"), {})
    first = _pruefungen(result)[0]["anforderungen"][0]
    assert first["bewertung"] == "N/A"
    assert "Keine spezifische Angabe" in first["dokuAntragsteller"]
