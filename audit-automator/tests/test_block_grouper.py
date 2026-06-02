"""Tests for block grouping / Zielobjekt marker detection (MAX-9).

The BlockGrouper helpers (_flatten_all_blocks, _find_zielobjekt_markers,
_group_blocks_by_markers) are pure aside from logging and don't touch the
GcsClient passed to __init__, so they're tested on an instance built with a
dummy client. Importing the gs_extraction package pulls in document_processor,
which needs PyMuPDF (fitz); skip cleanly if that isn't installed.
"""
import pytest

pytest.importorskip("fitz")

from src.audit.stages.gs_extraction.block_grouper import BlockGrouper


@pytest.fixture
def grouper():
    return BlockGrouper(gcs_client=None)


def _text_block(block_id, text):
    return {"blockId": str(block_id), "textBlock": {"text": text}}


def test_flatten_descends_into_nested_text_and_table_blocks(grouper):
    blocks = [
        {
            "blockId": "1",
            "textBlock": {
                "text": "parent",
                "blocks": [_text_block(2, "child")],
            },
        },
        {
            "blockId": "3",
            "tableBlock": {
                "bodyRows": [
                    {"cells": [{"blocks": [_text_block(4, "cell")]}]}
                ]
            },
        },
    ]
    flattened = grouper._flatten_all_blocks(blocks)
    ids = [b.get("blockId") for b in flattened]
    assert ids == ["1", "2", "3", "4"]


def test_find_markers_matches_exact_kuerzel_only(grouper):
    blocks = [
        _text_block(1, "intro text"),
        _text_block(2, "SVR-01"),            # exact match -> marker
        _text_block(3, "about SVR-01 here"),  # substring, NOT exact -> no marker
        _text_block(4, "APP-02"),            # exact match -> marker
    ]
    system_map = {"zielobjekte": [{"name": "SVR-01"}, {"name": "APP-02"}, {"name": "NET-09"}]}
    markers = grouper._find_zielobjekt_markers(blocks, system_map)
    found = {(m["kuerzel"], m["block_id"]) for m in markers}
    assert found == {("SVR-01", 2), ("APP-02", 4)}


def test_find_markers_includes_informationsverbund_name(grouper):
    blocks = [_text_block(5, "MyVerbund")]
    system_map = {"zielobjekte": [], "informationsverbund_name": "MyVerbund"}
    markers = grouper._find_zielobjekt_markers(blocks, system_map)
    assert markers == [{"kuerzel": "MyVerbund", "block_id": 5}]


def test_group_blocks_assigns_ranges_and_ungrouped_prefix(grouper):
    from collections import defaultdict

    block_map = {i: _text_block(i, f"b{i}") for i in [1, 2, 3, 4, 5]}
    markers = [{"kuerzel": "SVR-01", "block_id": 3}]  # blocks 1,2 precede the first marker
    grouped = defaultdict(list)
    grouper._group_blocks_by_markers(markers, block_map, grouped)

    assert [b["blockId"] for b in grouped["_UNGROUPED_"]] == ["1", "2"]
    assert [b["blockId"] for b in grouped["SVR-01"]] == ["3", "4", "5"]


def test_group_blocks_splits_between_consecutive_markers(grouper):
    from collections import defaultdict

    block_map = {i: _text_block(i, f"b{i}") for i in [1, 2, 3, 4]}
    markers = [{"kuerzel": "A", "block_id": 1}, {"kuerzel": "B", "block_id": 3}]
    grouped = defaultdict(list)
    grouper._group_blocks_by_markers(markers, block_map, grouped)

    assert [b["blockId"] for b in grouped["A"]] == ["1", "2"]
    assert [b["blockId"] for b in grouped["B"]] == ["3", "4"]
    assert "_UNGROUPED_" not in grouped  # nothing precedes the first marker
