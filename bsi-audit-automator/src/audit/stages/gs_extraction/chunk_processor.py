# bsi-audit-automator/src/audit/stages/gs_extraction/chunk_processor.py
import logging
from typing import List, Dict


class ChunkProcessor:
    """Handles chunking logic for processing large block collections."""

    MAX_BLOCKS_PER_CHUNK = 200
    MIN_BLOCKS_PER_CHUNK = 50

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

    @staticmethod
    def preprocess_blocks_for_ai(blocks: List[Dict]) -> List[Dict]:
        """Preprocess blocks to avoid JSON generation issues."""
        processed_blocks = []
        
        for block in blocks:
            # Create a clean copy of the block
            clean_block = block.copy()
            
            # Clean text content to prevent JSON issues
            if 'textBlock' in clean_block and 'text' in clean_block['textBlock']:
                text = clean_block['textBlock']['text']
                # Remove or escape problematic characters
                text = text.replace('\r\n', ' ').replace('\n', ' ').replace('\r', ' ')
                text = text.replace('"', '\\"').replace('\t', ' ')
                # Limit extremely long text blocks that might cause issues
                if len(text) > 2000:
                    text = text[:1800] + "... [truncated]"
                clean_block['textBlock']['text'] = text
            
            processed_blocks.append(clean_block)
        
        return processed_blocks