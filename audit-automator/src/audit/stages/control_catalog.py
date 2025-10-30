# src/audit/stages/control_catalog.py
import logging
import json
from typing import List, Dict, Any, Optional

class ControlCatalog:
    """A utility to load and query the BSI Grundschutz OSCAL catalog."""
    
    def __init__(self, catalog_path: str = "assets/json/BSI_GS_OSCAL_current_2023_benutzerdefinierte.json"):
        self.catalog_path = catalog_path
        self._baustein_map = {}
        self._control_map = {}  # New: Map for direct control lookup
        try:
            self._load_and_parse_catalog()
            logging.info(f"Successfully loaded and parsed BSI Control Catalog from {catalog_path}.")
        except Exception as e:
            logging.error(f"Failed to initialize ControlCatalog: {e}", exc_info=True)
            raise

    def _load_and_parse_catalog(self):
        """Loads the JSON catalog and builds an efficient lookup map."""
        with open(self.catalog_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        catalog = data.get("catalog", {})
        # Layers like 'ISMS', 'ORP', 'INF', etc.
        for layer_group in catalog.get("groups", []):
            # Bausteine within each layer
            for baustein_group in layer_group.get("groups", []):
                baustein_id = baustein_group.get("id")
                if baustein_id:
                    controls = baustein_group.get("controls", [])
                    self._baustein_map[baustein_id] = controls
                    for control in controls:
                        self._control_map[control.get("id")] = control
    
    def get_controls_for_baustein_id(self, baustein_id: str) -> List[Dict[str, Any]]:
        """
        Retrieves all controls for a given Baustein ID.

        Args:
            baustein_id: The ID of the Baustein (e.g., 'ISMS.1').

        Returns:
            A list of control objects, or an empty list if not found.
        """
        controls = self._baustein_map.get(baustein_id, [])
        if not controls:
            logging.warning(f"No controls found for Baustein ID: {baustein_id}")
        return controls

    def get_control_level(self, control_id: str) -> Optional[str]:
        """
        Efficiently retrieves the 'level' property for a given control ID.

        Args:
            control_id: The ID of the control (e.g., 'ISMS.1.A1').

        Returns:
            The level as a string (e.g., '1', '5') or None if not found.
        """
        control = self._control_map.get(control_id)
        if control:
            for prop in control.get("props", []):
                if prop.get("name") == "level":
                    return prop.get("value")
        return None

    def get_level_1_control_ids(self) -> List[str]:
        """
        Scans the entire catalog and returns a list of all control IDs that
        are marked as Level 1 (MUSS-Anforderungen).

        Returns:
            A list of Level 1 control ID strings.
        """
        level_1_ids = []
        for baustein_id, controls in self._baustein_map.items():
            for control in controls:
                for prop in control.get("props", []):
                    if prop.get("name") == "level" and prop.get("value") == "1":
                        level_1_ids.append(control.get("id"))
                        break # Move to the next control once level is found
        logging.info(f"Found {len(level_1_ids)} Level 1 (MUSS) controls in the catalog.")
        return level_1_ids