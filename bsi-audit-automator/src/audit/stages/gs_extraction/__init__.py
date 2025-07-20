# bsi-audit-automator/src/audit/stages/gs_extraction/__init__.py
"""
Grundschutz Check Extraction package.

This package implements the Ground-Truth-Driven Semantic Chunking strategy
for extracting structured security requirements from BSI Grundschutz documents.
"""

from .ground_truth_mapper import GroundTruthMapper
from .document_processor import DocumentProcessor
from .block_grouper import BlockGrouper
from .ai_refiner import AiRefiner

__all__ = [
    'GroundTruthMapper',
    'DocumentProcessor', 
    'BlockGrouper',
    'AiRefiner'
]