"""Tests for chunk preprocessing before the extraction prompt is built.

Blocks are shared objects: chunks overlap by 10-20 blocks and nested blocks appear
both inside their parent and as their own flattened entry. Preprocessing therefore
must not mutate its input, or a block gets re-processed once per chunk that holds
it. Quotes must survive verbatim — the caller runs json.dumps, which escapes them.

ChunkProcessor is pure (logging aside) and needs no clients.
"""
import json

import pytest

pytest.importorskip("fitz")

from src.audit.stages.gs_extraction.chunk_processor import ChunkProcessor


def _block(block_id, text):
    return {"blockId": str(block_id), "textBlock": {"text": text}}


def test_preprocessing_does_not_mutate_the_source_blocks():
    original = _block(1, 'Er sagte "Ja"\nin Zeile zwei')
    blocks = [original]
    ChunkProcessor.preprocess_blocks_for_ai(blocks)
    assert original["textBlock"]["text"] == 'Er sagte "Ja"\nin Zeile zwei'


def test_quotes_are_left_for_json_dumps_to_escape_exactly_once():
    processed = ChunkProcessor.preprocess_blocks_for_ai([_block(1, 'Status: "Ja"')])
    assert processed[0]["textBlock"]["text"] == 'Status: "Ja"'
    # Round-tripping through the prompt serialisation returns the original text.
    assert json.loads(json.dumps(processed))[0]["textBlock"]["text"] == 'Status: "Ja"'


def test_newlines_and_tabs_are_flattened_to_spaces():
    processed = ChunkProcessor.preprocess_blocks_for_ai([_block(1, "a\r\nb\nc\rd\te")])
    assert processed[0]["textBlock"]["text"] == "a b c d e"


def test_overprocessing_an_overlapping_block_twice_is_idempotent():
    """The same block object appears in two chunks; the second pass must see the
    original text, not an already-truncated one."""
    shared = _block(1, "x" * 3000)
    first = ChunkProcessor.preprocess_blocks_for_ai([shared])
    second = ChunkProcessor.preprocess_blocks_for_ai([shared])
    assert first[0]["textBlock"]["text"] == second[0]["textBlock"]["text"]
    assert first[0]["textBlock"]["text"].endswith(ChunkProcessor.TRUNCATION_MARKER)
    assert len(shared["textBlock"]["text"]) == 3000


def test_nested_blocks_are_cleaned_too():
    blocks = [{
        "blockId": "1",
        "textBlock": {"text": "parent\nline", "blocks": [_block(2, "child\nline")]},
    }]
    processed = ChunkProcessor.preprocess_blocks_for_ai(blocks)
    assert processed[0]["textBlock"]["text"] == "parent line"
    assert processed[0]["textBlock"]["blocks"][0]["textBlock"]["text"] == "child line"


def test_blocks_without_text_are_passed_through_untouched():
    blocks = [{"blockId": "1", "tableBlock": {"bodyRows": []}}, {"blockId": "2"}]
    assert ChunkProcessor.preprocess_blocks_for_ai(blocks) == blocks
