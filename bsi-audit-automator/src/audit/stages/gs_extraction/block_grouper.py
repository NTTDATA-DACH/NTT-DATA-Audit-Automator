# bsi-audit-automator/src/audit/stages/gs_extraction/block_grouper.py
import logging
import json
import sys
from typing import Dict, Any, List
from collections import defaultdict

from src.clients.gcs_client import GcsClient


class BlockGrouper:
    """
    Groups Document AI layout blocks by Zielobjekt context using a marker-based algorithm.
    Finds Zielobjekt identifiers as section markers and groups content between them.
    """
    
    FINAL_MERGED_LAYOUT_PATH = "output/results/intermediate/doc_ai_layout_parser_merged.json"
    GROUPED_BLOCKS_PATH = "output/results/intermediate/zielobjekt_grouped_blocks.json"

    def __init__(self, gcs_client: GcsClient):
        self.gcs_client = gcs_client

    async def group_layout_blocks_by_zielobjekt(self, system_map: Dict[str, Any], force_overwrite: bool):
        """
        Group layout blocks by Zielobjekt using a robust marker-based algorithm.
        
        Args:
            system_map: Ground truth map containing zielobjekte list
            force_overwrite: If True, reprocess even if output exists
        """
        if not force_overwrite and self.gcs_client.blob_exists(self.GROUPED_BLOCKS_PATH):
            logging.info(f"Grouped layout blocks file already exists. Skipping grouping.")
            return

        logging.info("Grouping layout blocks by Zielobjekt context using marker-based algorithm...")
        
        # Load layout data
        layout_data = await self.gcs_client.read_json_async(self.FINAL_MERGED_LAYOUT_PATH)
        all_blocks = layout_data.get("documentLayout", {}).get("blocks", [])

        # Initialize grouping structures
        grouped_blocks = defaultdict(list)
        
        # Flatten all blocks for consistent processing
        all_flattened_blocks = self._flatten_all_blocks(all_blocks)
        block_id_to_block_map = {int(b['blockId']): b for b in all_flattened_blocks}

        # Find Zielobjekt markers in the document
        markers = self._find_zielobjekt_markers(all_flattened_blocks, system_map)
        
        if not markers:
            # If no markers found, all blocks are ungrouped
            logging.warning("No Zielobjekt markers found in document. All blocks will be marked as ungrouped.")
            sys.exit()
        else:
            # Group blocks based on marker positions
            self._group_blocks_by_markers(markers, block_id_to_block_map, grouped_blocks)

        # Save grouped blocks
        await self.gcs_client.upload_from_string_async(
            json.dumps({"zielobjekt_grouped_blocks": dict(grouped_blocks)}, indent=2, ensure_ascii=False),
            self.GROUPED_BLOCKS_PATH
        )
        logging.info(f"Saved grouped layout blocks to {self.GROUPED_BLOCKS_PATH}")

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
        """Find Zielobjekt markers in the flattened blocks."""
        zielobjekte = system_map.get("zielobjekte", [])
        kuerzel_list = [item['kuerzel'] for item in zielobjekte]
        remaining_kuerzel = kuerzel_list.copy()
        markers = []
        
        # Search for exact matches of Zielobjekt kürzel in block text
        for block in all_flattened_blocks:
            direct_text = ""
            if 'textBlock' in block and 'text' in block['textBlock']:
                direct_text = block['textBlock']['text'].strip()
            
            if direct_text:
                for kuerzel in remaining_kuerzel.copy():
                    if direct_text == kuerzel:
                        block_id = int(block.get('blockId', 0))
                        markers.append({'kuerzel': kuerzel, 'block_id': block_id})
                        remaining_kuerzel.remove(kuerzel)
                        break
        
        logging.info(f"Found {len(markers)} Zielobjekt markers. Unfound kürzel ({len(remaining_kuerzel)}): {remaining_kuerzel}")
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