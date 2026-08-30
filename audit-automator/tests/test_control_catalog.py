"""Tests for the ControlCatalog query layer against the committed Ed. 2023 catalog.

Offline: reads only the JSON asset. Run from the audit-automator/ directory.
"""

import pytest

from src.audit.stages.control_catalog import ControlCatalog


@pytest.fixture(scope="module")
def catalog():
    return ControlCatalog()


def test_levels_are_bsh(catalog):
    assert catalog.get_control_level("ISMS.1.A1") == "B"
    # A requirement outside the official Kompendium (e.g. from a custom Baustein)
    assert catalog.get_control_level("XYZ.9.A1") is None
    assert catalog.get_control_level(None) is None


def test_muss_ids_are_basis_and_exclude_entfallen(catalog):
    muss_ids = catalog.get_muss_control_ids()
    assert len(muss_ids) > 500
    assert len(muss_ids) == len(set(muss_ids))
    for control_id in muss_ids:
        assert catalog.get_control_level(control_id) == "B"
    # APP.1.1.A1 is ENTFALLEN in Edition 2023 but still carries a (B) heading — it must
    # not turn up as an obligation.
    assert "APP.1.1.A1" not in set(muss_ids)


def test_get_controls_returns_active_requirements_with_title_and_level(catalog):
    controls = catalog.get_controls_for_baustein_id("ISMS.1")
    assert controls
    for control in controls:
        assert control["titel"]
        assert control["level"] in {"B", "S", "H"}
        assert control["entfallen"] is False
        assert control["id"].startswith("ISMS.1.A")


def test_previously_dropped_bausteine_now_resolve(catalog):
    """The old OSCAL catalog stored these with malformed IDs, so they matched nothing."""
    for baustein_id in ("INF.5", "INF.9", "SYS.1.8"):
        controls = catalog.get_controls_for_baustein_id(baustein_id)
        assert controls, f"{baustein_id} liefert keine Anforderungen"
        assert any(c["level"] == "B" for c in controls)


def test_unknown_baustein_yields_empty_list(catalog, caplog):
    """Custom Bausteine are no longer in the catalog: empty list plus a warning, no crash."""
    assert catalog.get_controls_for_baustein_id("ORP.6") == []
    assert catalog.get_controls_for_baustein_id("Frei erfundener Baustein") == []


def test_baustein_titles_are_available(catalog):
    assert catalog.get_baustein_title("ISMS.1") == "Sicherheitsmanagement"
    assert catalog.get_baustein_title("ORP.6") is None
