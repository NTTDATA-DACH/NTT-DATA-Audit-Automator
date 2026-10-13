# bsi-audit-automator/src/audit/stages/gs_extraction/__init__.py
"""
Grundschutz Check Extraction package.

This package implements the Ground-Truth-Driven Semantic Chunking strategy
for extracting structured security requirements from BSI Grundschutz documents.

The submodules are imported lazily: `anforderung_fields` and `sheet_parser` are pure
Python and used by stages 3 and 5, whereas `document_processor` needs PyMuPDF. An eager
import here would make every stage depend on the PDF toolchain.
"""
import importlib

_EXPORTS = {
    "GroundTruthMapper": ".ground_truth_mapper",
    "DocumentProcessor": ".document_processor",
    "BlockGrouper": ".block_grouper",
    "AiRefiner": ".ai_refiner",
    "CacheManager": ".cache_manager",
    "ChunkProcessor": ".chunk_processor",
    "DataProcessor": ".data_processor",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module_name, __name__), name)
