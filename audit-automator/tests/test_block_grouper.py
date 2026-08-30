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


SYSTEM_MAP = {
    "zielobjekte": [
        {"kuerzel": "SVR-01", "name": "Main Web Server"},
        {"kuerzel": "APP-02", "name": "CRM Anwendung"},
        {"kuerzel": "NET-09", "name": "Backbone"},
    ]
}


def test_find_markers_matches_exact_kuerzel_only(grouper):
    blocks = [
        _text_block(1, "intro text"),
        _text_block(2, "SVR-01"),            # exact match -> marker
        _text_block(3, "about SVR-01 here"),  # substring, NOT exact -> no marker
        _text_block(4, "APP-02"),            # exact match -> marker
    ]
    markers = grouper._find_zielobjekt_markers(blocks, SYSTEM_MAP)
    found = {(m["kuerzel"], m["block_id"]) for m in markers}
    assert found == {("SVR-01", 2), ("APP-02", 4)}


def test_find_markers_matches_descriptive_names_but_keys_by_kuerzel(grouper):
    """Documents may head sections with the name; consumers still join on the Kürzel."""
    blocks = [
        _text_block(1, "Main Web Server"),
        _text_block(2, "some content"),
        _text_block(3, "CRM Anwendung"),
    ]
    markers = grouper._find_zielobjekt_markers(blocks, SYSTEM_MAP)
    assert markers == [
        {"kuerzel": "SVR-01", "block_id": 1},
        {"kuerzel": "APP-02", "block_id": 3},
    ]


def test_find_markers_emits_a_marker_for_every_repeated_heading(grouper):
    """A Zielobjekt heads one section per Baustein; later repeats must not be
    swallowed into the preceding Zielobjekt's group."""
    blocks = [
        _text_block(1, "SVR-01"),
        _text_block(2, "ORP.1 content"),
        _text_block(3, "APP-02"),
        _text_block(4, "ORP.1 content"),
        _text_block(5, "SVR-01"),          # same Zielobjekt, next Baustein chapter
        _text_block(6, "SYS.1.1 content"),
    ]
    markers = grouper._find_zielobjekt_markers(blocks, SYSTEM_MAP)
    assert markers == [
        {"kuerzel": "SVR-01", "block_id": 1},
        {"kuerzel": "APP-02", "block_id": 3},
        {"kuerzel": "SVR-01", "block_id": 5},
    ]


def test_find_markers_collapses_immediately_repeated_headings(grouper):
    blocks = [_text_block(1, "SVR-01"), _text_block(2, "Main Web Server"), _text_block(3, "content")]
    markers = grouper._find_zielobjekt_markers(blocks, SYSTEM_MAP)
    assert markers == [{"kuerzel": "SVR-01", "block_id": 1}]


def test_repeated_markers_merge_into_one_group(grouper):
    from collections import defaultdict

    block_map = {i: _text_block(i, f"b{i}") for i in range(1, 7)}
    markers = grouper._find_zielobjekt_markers(
        [_text_block(1, "SVR-01"), _text_block(3, "APP-02"), _text_block(5, "SVR-01")], SYSTEM_MAP
    )
    grouped = defaultdict(list)
    grouper._group_blocks_by_markers(markers, block_map, grouped)

    assert [b["blockId"] for b in grouped["SVR-01"]] == ["1", "2", "5", "6"]
    assert [b["blockId"] for b in grouped["APP-02"]] == ["3", "4"]


def test_find_markers_includes_informationsverbund_name(grouper):
    blocks = [_text_block(5, "MyVerbund")]
    system_map = {"zielobjekte": [], "informationsverbund_name": "MyVerbund"}
    markers = grouper._find_zielobjekt_markers(blocks, system_map)
    assert markers == [{"kuerzel": "MyVerbund", "block_id": 5}]


def test_find_markers_falls_back_to_the_name_when_no_kuerzel_is_known(grouper):
    blocks = [_text_block(2, "Nur ein Name")]
    system_map = {"zielobjekte": [{"name": "Nur ein Name"}]}
    markers = grouper._find_zielobjekt_markers(blocks, system_map)
    assert markers == [{"kuerzel": "Nur ein Name", "block_id": 2}]


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
