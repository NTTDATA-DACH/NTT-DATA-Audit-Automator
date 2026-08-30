# src/audit/stages/control_catalog.py
import logging
import json
from typing import List, Dict, Any, Optional

from src.constants import CONTROL_CATALOG_PATH


class ControlCatalog:
    """Query interface for the official BSI IT-Grundschutz-Kompendium (Edition 2023).

    Reads the lean catalog generated from the sha256-pinned official BSI XML by
    `python -m src.tools.build_bsi_catalog`. Levels are the BSI's own B/S/H
    (Basis / Standard / erhöhter Schutzbedarf); requirements marked ENTFALLEN are kept
    in the file for traceability but never handed out — they are not auditable.
    """

    def __init__(self, catalog_path: str = CONTROL_CATALOG_PATH):
        self.catalog_path = catalog_path
        self._baustein_map: Dict[str, List[Dict[str, Any]]] = {}
        self._baustein_titles: Dict[str, str] = {}
        self._control_map: Dict[str, Dict[str, Any]] = {}
        try:
            self._load_and_parse_catalog()
        except Exception as e:
            logging.error(f"Failed to initialize ControlCatalog: {e}", exc_info=True)
            raise

    def _load_and_parse_catalog(self):
        """Loads the catalog JSON and builds the Baustein and control lookup maps."""
        with open(self.catalog_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        self._baustein_titles = {b["id"]: b.get("titel", "") for b in data.get("bausteine", [])}
        self._baustein_map = {baustein_id: [] for baustein_id in self._baustein_titles}

        entfallen_count = 0
        for control in data.get("anforderungen", []):
            self._control_map[control["id"]] = control
            if control.get("entfallen"):
                entfallen_count += 1
                continue
            self._baustein_map.setdefault(control["baustein"], []).append(control)

        meta = data.get("meta", {})
        logging.info(
            f"Loaded BSI Kompendium Edition {meta.get('edition', '?')} from {self.catalog_path}: "
            f"{len(self._baustein_titles)} Bausteine, "
            f"{len(self._control_map) - entfallen_count} active requirements "
            f"({entfallen_count} ENTFALLEN)."
        )

    def get_controls_for_baustein_id(self, baustein_id: str) -> List[Dict[str, Any]]:
        """
        Retrieves the active (non-ENTFALLEN) requirements of a Baustein.

        Args:
            baustein_id: The ID of the Baustein (e.g., 'ISMS.1').

        Returns:
            A list of requirement objects, or an empty list if the Baustein is unknown.
            Bausteine the institution defined itself are not part of the official
            Kompendium and therefore return an empty list.
        """
        controls = self._baustein_map.get(baustein_id, [])
        if not controls:
            logging.warning(f"No controls found for Baustein ID: {baustein_id}")
        return controls

    def get_control_level(self, control_id: str) -> Optional[str]:
        """
        Retrieves the BSI level of a requirement.

        Args:
            control_id: The ID of the requirement (e.g., 'ISMS.1.A1').

        Returns:
            'B' (Basis / MUSS), 'S' (Standard) or 'H' (erhöhter Schutzbedarf), or None
            if the ID is not part of the official Kompendium.
        """
        control = self._control_map.get(control_id)
        return control.get("level") if control else None

    def get_muss_control_ids(self) -> List[str]:
        """
        Returns the IDs of all active Basis-Anforderungen (level 'B', i.e. MUSS).

        ENTFALLEN requirements are excluded — they carry no obligation.
        """
        muss_ids = [
            control["id"]
            for control in self._control_map.values()
            if control.get("level") == "B" and not control.get("entfallen")
        ]
        logging.info(f"Found {len(muss_ids)} Basis-Anforderungen (MUSS) in the catalog.")
        return muss_ids

    def get_baustein_title(self, baustein_id: str) -> Optional[str]:
        """Returns the official title of a Baustein, or None if it is not in the catalog."""
        return self._baustein_titles.get(baustein_id)
