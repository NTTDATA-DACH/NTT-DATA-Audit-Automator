# bsi-audit-automator/src/audit/stages/gs_extraction/chunk_processor.py
import copy
import logging
from typing import List, Dict


class ChunkProcessor:
    """Handles chunking logic for processing large block collections."""

    MAX_BLOCKS_PER_CHUNK = 200

    @staticmethod
    def chunk_blocks(blocks: List[Dict], max_blocks: int = MAX_BLOCKS_PER_CHUNK) -> List[List[Dict]]:
        """Split blocks into chunks of manageable size with 10% overlap."""
        if len(blocks) <= max_blocks:
            return [blocks]

        # Calculate overlap size (10% of max_blocks, minimum 10 blocks, maximum 20 blocks)
        overlap_size = max(10, min(20, int(max_blocks * 0.10)))
        
        chunks = []
        i = 0
        while i < len(blocks):
            # Calculate chunk boundaries
            start_idx = max(0, i - (overlap_size if i > 0 else 0))
            end_idx = min(len(blocks), i + max_blocks)
            
            # Extract chunk with overlap
            chunk = blocks[start_idx:end_idx]
            chunks.append(chunk)
            
            # Move to next chunk position (accounting for overlap)
            i += max_blocks - overlap_size
            
            # Break if we've covered all blocks
            if end_idx >= len(blocks):
                break
        
        logging.info(f"Split {len(blocks)} blocks into {len(chunks)} chunks with {overlap_size}-block overlap ({overlap_size/max_blocks*100:.1f}%)")
        return chunks

    MAX_TEXT_LENGTH = 2000
    TRUNCATED_TEXT_LENGTH = 1800
    TRUNCATION_MARKER = "... [gekürzt durch die Vorverarbeitung]"

    @staticmethod
    def preprocess_blocks_for_ai(blocks: List[Dict]) -> List[Dict]:
        """Normalize block text for the prompt without touching the source blocks.

        Chunks overlap and nested blocks appear both inside their parent and as their
        own entry, so the blocks are shared objects: a copy must be deep, or a block
        would be re-processed (and re-truncated) once per chunk that contains it.

        Quotes are deliberately NOT escaped here — the caller runs json.dumps over the
        result, which escapes them once and correctly.
        """
        processed_blocks = [copy.deepcopy(block) for block in blocks]
        truncated_ids = []

        def clean_recursive(block_list: List[Dict]) -> None:
            for block in block_list:
                text_block = block.get('textBlock')
                if not isinstance(text_block, dict):
                    continue
                text = text_block.get('text')
                if isinstance(text, str):
                    text = text.replace('\r\n', ' ').replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
                    if len(text) > ChunkProcessor.MAX_TEXT_LENGTH:
                        text = text[:ChunkProcessor.TRUNCATED_TEXT_LENGTH] + ChunkProcessor.TRUNCATION_MARKER
                        truncated_ids.append(block.get('blockId'))
                    text_block['text'] = text
                if isinstance(text_block.get('blocks'), list):
                    clean_recursive(text_block['blocks'])

        clean_recursive(processed_blocks)

        if truncated_ids:
            logging.warning(
                f"Truncated the text of {len(truncated_ids)} block(s) to "
                f"{ChunkProcessor.TRUNCATED_TEXT_LENGTH} characters before extraction: {truncated_ids}"
            )

        return processed_blocks