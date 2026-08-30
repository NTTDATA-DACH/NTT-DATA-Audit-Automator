# bsi-audit-automator/src/audit/stages/gs_extraction/block_grouper.py
import logging
import json
from typing import Dict, Any, List
from collections import defaultdict

from src.clients.gcs_client import GcsClient
from src.constants import FINAL_MERGED_LAYOUT_PATH, GROUPED_BLOCKS_PATH


class BlockGrouper:
    """
    Groups Document AI layout blocks by Zielobjekt context using a marker-based algorithm.
    Finds Zielobjekt identifiers as section markers and groups content between them.
    """

    def __init__(self, gcs_client: GcsClient):
        self.gcs_client = gcs_client

    async def group_layout_blocks_by_zielobjekt(self, system_map: Dict[str, Any], force_overwrite: bool):
        """
        Group layout blocks by Zielobjekt using a robust marker-based algorithm.
        
        Args:
            system_map: Ground truth map containing zielobjekte list
            force_overwrite: If True, reprocess even if output exists
        """
        if not force_overwrite and self.gcs_client.blob_exists(GROUPED_BLOCKS_PATH):
            logging.info(f"Grouped layout blocks file already exists. Skipping grouping.")
            return

        logging.info("Grouping layout blocks by Zielobjekt context using marker-based algorithm...")
        
        # Load layout data
        layout_data = await self.gcs_client.read_json_async(FINAL_MERGED_LAYOUT_PATH)
        all_blocks = layout_data.get("documentLayout", {}).get("blocks", [])

        # Initialize grouping structures
        grouped_blocks = defaultdict(list)
        
        # Flatten all blocks for consistent processing
        all_flattened_blocks = self._flatten_all_blocks(all_blocks)
        block_id_to_block_map = {}
        for b in all_flattened_blocks:
            raw_id = b.get('blockId')
            try:
                block_id_to_block_map[int(raw_id)] = b
            except (TypeError, ValueError):
                logging.warning(f"Skipping block with non-numeric blockId: {raw_id!r}")

        # Find Zielobjekt markers in the document
        markers = self._find_zielobjekt_markers(all_flattened_blocks, system_map)
        
        if not markers:
            # No markers is a recoverable, empty-result condition. Keep all blocks
            # ungrouped and continue rather than killing the whole process (previously
            # this called sys.exit(), which aborted the pipeline with success code 0).
            logging.warning("No Zielobjekt markers found in document. All blocks will be marked as ungrouped.")
            for bid in sorted(block_id_to_block_map.keys()):
                grouped_blocks["_UNGROUPED_"].append(block_id_to_block_map[bid])
        else:
            # Group blocks based on marker positions
            self._group_blocks_by_markers(markers, block_id_to_block_map, grouped_blocks)

        # Save grouped blocks
        await self.gcs_client.write_json_async({"zielobjekt_grouped_blocks": dict(grouped_blocks)}, GROUPED_BLOCKS_PATH)
        logging.info(f"Saved grouped layout blocks to {GROUPED_BLOCKS_PATH}")

    def _flatten_all_blocks(self, blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Flatten all blocks into a single list with hierarchical structure removed."""
        flattened = []
        
        def flatten_recursive(block_list):
            for block in block_list:
                # Add current block to flattened list
                flattened.append(block)
                
                # Process nested textBlock.blocks
                if 'textBlock' in block and 'blocks' in block['textBlock']:
                    flatten_recursive(block['textBlock']['blocks'])
                
                # Process table blocks
                if 'tableBlock' in block:
                    for row_type in ['headerRows', 'bodyRows']:
                        for row in block['tableBlock'].get(row_type, []):
                            for cell in row.get('cells', []):
                                if 'blocks' in cell:
                                    flatten_recursive(cell['blocks'])
        
        flatten_recursive(blocks)
        return flattened

    def _find_zielobjekt_markers(self, all_flattened_blocks: List[Dict[str, Any]], system_map: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Find Zielobjekt markers in the flattened blocks.

        A section heading may carry either the Kürzel ('SVR-01') or the descriptive
        name ('Main Web Server'), so both are accepted as markers — but a marker is
        always labelled with the Kürzel, because every consumer (ai_refiner,
        data_processor, stage 3's coverage check, stage 5's checklist) joins on it.

        A Zielobjekt may head several sections (A.4 exports are usually sorted by
        Baustein), so every exact match becomes a marker; consecutive duplicates are
        collapsed since they would only produce empty ranges.
        """
        text_to_kuerzel = {}
        for item in system_map.get("zielobjekte", []):
            kuerzel = (item.get("kuerzel") or "").strip()
            name = (item.get("name") or "").strip()
            identifier = kuerzel or name
            if not identifier:
                continue
            # Kürzel wins on collision: it is the value the rest of the pipeline uses.
            if kuerzel:
                text_to_kuerzel[kuerzel] = identifier
            if name and name not in text_to_kuerzel:
                text_to_kuerzel[name] = identifier

        verbund_name = (system_map.get("informationsverbund_name") or "").strip()
        if verbund_name and verbund_name not in text_to_kuerzel:
            text_to_kuerzel[verbund_name] = verbund_name

        markers = []
        found_identifiers = set()

        # Search for exact matches of Zielobjekt Kürzel/names in block text
        for block in all_flattened_blocks:
            direct_text = ""
            if 'textBlock' in block and 'text' in block['textBlock']:
                direct_text = block['textBlock']['text'].strip()

            kuerzel = text_to_kuerzel.get(direct_text) if direct_text else None
            if not kuerzel:
                continue
            if markers and markers[-1]['kuerzel'] == kuerzel:
                continue  # repeated heading with nothing in between
            markers.append({'kuerzel': kuerzel, 'block_id': int(block.get('blockId', 0))})
            found_identifiers.add(kuerzel)

        unfound = sorted(set(text_to_kuerzel.values()) - found_identifiers)
        logging.info(
            f"Found {len(markers)} Zielobjekt markers for {len(found_identifiers)} Zielobjekte. "
            f"Unfound ({len(unfound)}): {unfound}"
        )
        return markers

    def _group_blocks_by_markers(self, markers: List[Dict[str, Any]], block_id_to_block_map: Dict[int, Dict[str, Any]], grouped_blocks: defaultdict):
        """Group blocks based on marker positions."""
        # Sort markers by block ID position
        markers.sort(key=lambda m: m['block_id'])
        logging.info(f"Sorted {len(markers)} Zielobjekt markers.")

        # Get all block IDs in order
        sorted_block_ids = sorted(block_id_to_block_map.keys())
        
        # Handle blocks before first marker (ungrouped)
        first_marker_id = markers[0]['block_id']
        ungrouped_ids = [bid for bid in sorted_block_ids if bid < first_marker_id]
        for bid in ungrouped_ids:
            grouped_blocks["_UNGROUPED_"].append(block_id_to_block_map[bid])
        
        # Group blocks between consecutive markers
        for i, marker in enumerate(markers):
            start_id = marker['block_id']
            end_id = markers[i+1]['block_id'] if i + 1 < len(markers) else max(sorted_block_ids) + 1
            
            kuerzel = marker['kuerzel']
            group_ids = [bid for bid in sorted_block_ids if start_id <= bid < end_id]
            
            for bid in group_ids:
                grouped_blocks[kuerzel].append(block_id_to_block_map[bid])
            
            logging.info(f"Assigned {len(group_ids)} blocks to '{kuerzel}' (IDs {start_id}-{end_id-1}).")