# src/audit/stages/control_catalog.py
import logging
import json
from typing import List, Dict, Any

class ControlCatalog:
    """A utility to load and query the BSI Grundschutz OSCAL catalog."""
    
    def __init__(self, catalog_path: str = "assets/json/BSI_GS_OSCAL_current_2023_benutzerdefinierte.json"):
        self.catalog_path = catalog_path
        self._baustein_map = {}
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
                    self._baustein_map[baustein_id] = baustein_group.get("controls", [])
    
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