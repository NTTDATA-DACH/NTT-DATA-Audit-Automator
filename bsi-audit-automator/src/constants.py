"""
Centralized constants for file paths and output organization.
This ensures consistency across all stages and reduces magic strings.
"""

CHUNK_PROCESSING_MODEL =  "gemini-2.5-flash"
GROUND_TRUTH_MODEL =  "gemini-2.5-pro"

# Output organization structure:
# output/results/         -> Final stage outputs ready for report generation
# output/temp/           -> Temporary files (PDF chunks, intermediate processing)
# output/intermediary/   -> Idempotent saves with stage-specific subfolders

# =============================================================================
# RESULTS PATHS - Final stage outputs
# =============================================================================
RESULTS_BASE = "output/results"
STAGE_RESULTS_PATH = f"{RESULTS_BASE}/{{stage_name}}.json"  # Format with stage_name
ALL_FINDINGS_PATH = f"{RESULTS_BASE}/all_findings.json"
FINAL_REPORT_PATH = f"{RESULTS_BASE}/final_audit_report.json"

# =============================================================================
# TEMPORARY PATHS - Short-lived processing files
# =============================================================================
TEMP_BASE = "output/temp"
TEMP_PDF_CHUNKS_PREFIX = f"{TEMP_BASE}/pdf_chunks/"
DOC_AI_BATCH_RESULTS_PREFIX = f"{TEMP_BASE}/doc_ai_results/"

# =============================================================================
# INTERMEDIARY PATHS - Idempotent saves organized by stage
# =============================================================================
INTERMEDIARY_BASE = "output/intermediate"

# Grundschutz-Check-Extraction stage paths
GS_EXTRACTION_BASE = f"{INTERMEDIARY_BASE}/gs_extraction"
GROUND_TRUTH_MAP_PATH = f"{GS_EXTRACTION_BASE}/system_structure_map.json"
GROUPED_BLOCKS_PATH = f"{GS_EXTRACTION_BASE}/zielobjekt_grouped_blocks.json"
EXTRACTED_CHECK_DATA_PATH = f"{GS_EXTRACTION_BASE}/extracted_grundschutz_check_merged.json"
INDIVIDUAL_RESULTS_PREFIX = f"{GS_EXTRACTION_BASE}/individual_results/"
FINAL_MERGED_LAYOUT_PATH = f"{GS_EXTRACTION_BASE}/merged_layout_parser_result.json"

# Document AI processing paths
DOC_AI_BASE = f"{INTERMEDIARY_BASE}/doc_ai"
DOC_AI_CHUNK_RESULTS_PREFIX = f"{DOC_AI_BASE}/chunk_results/"

# RAG Client paths  
RAG_BASE = f"{INTERMEDIARY_BASE}/rag"
DOCUMENT_CATEGORY_MAP_PATH = f"{RAG_BASE}/document_category_map.json"
