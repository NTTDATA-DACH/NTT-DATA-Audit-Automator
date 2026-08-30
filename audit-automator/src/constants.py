"""
Centralized constants for file paths and output organization.
This ensures consistency across all stages and reduces magic strings.
"""
import os

# Model IDs are config-driven (env override) with current-generation defaults (2026-08):
# gemini-3.7-flash is the GA workhorse, gemini-3.1-pro the GA flagship on Vertex AI.
# Both env vars are the rollback lever if a model is unavailable in a given project.
CHUNK_PROCESSING_MODEL = os.getenv("CHUNK_PROCESSING_MODEL", "gemini-3.7-flash")
GROUND_TRUTH_MODEL = os.getenv("GROUND_TRUTH_MODEL", "gemini-3.1-pro")

# Reasoning depth per call (Gemini 3.x): minimal | low | medium | high. The pro tier has
# no "minimal" level, so AiClient clamps it to "low" for those models.
THINKING_LEVEL = os.getenv("THINKING_LEVEL", "minimal")

# Maker/checker (Vier-Augen-Prinzip): every answer that ends up in the report or produces
# a finding is re-judged by a second, independent call against the same source documents.
# Roughly doubles the calls on those stages — set to "false" to fall back to single-pass.
ENABLE_MAKER_CHECKER = os.getenv("ENABLE_MAKER_CHECKER", "true").lower() in ("true", "1", "yes")
# The checker deliberately runs on the stronger model, whatever the maker used.
CHECKER_MODEL = os.getenv("CHECKER_MODEL", GROUND_TRUTH_MODEL)

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
# The assembled report is written as results/report_<YYMMDD>.json (ReportGenerator);
# the date stamp is deliberate, so there is no single constant path for it.

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

# Maker/checker protocol: one entry per checked AI answer, so the QS trail of the audit
# shows what the second pass objected to and whether its correction was taken.
CHECKER_LOG_PATH = f"{INTERMEDIARY_BASE}/checker_log.json"

# =============================================================================
# ASSET PATHS
# =============================================================================
PROMPT_CONFIG_PATH = "assets/json/prompt_config.json"

# BSI IT-Grundschutz-Kompendium, Edition 2023. Generated from the sha256-pinned official
# BSI XML by `python -m src.tools.build_bsi_catalog`; see readme.md ("Katalog aktualisieren").
CONTROL_CATALOG_PATH = "assets/json/bsi_kompendium_ed2023.json"

# JSON Schema for the fully-assembled audit report (used by ReportGenerator to
# validate the report before saving). Replaces the former no-op structural check.
REPORT_SCHEMA_PATH = "assets/schemas/master_report_schema.json"
