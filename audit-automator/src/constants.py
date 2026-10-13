"""
Centralized constants for file paths and output organization.
This ensures consistency across all stages and reduces magic strings.
"""
import os


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("true", "1", "yes")


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value.isdigit() else default


# Model IDs are config-driven (env override). One model serves every task (verified against the
# Vertex AI model pages on 2026-10-13): gemini-3.8-flash is GA on the global endpoint with batch
# and context caching. The only pro model on Vertex is gemini-3.1-pro-preview, a preview that can
# be shut down without notice, so quality is dialled via the thinking level instead of a second
# model. Both env vars remain the rollback lever if a model is unavailable in a given project.
DEFAULT_MODEL = "gemini-3.8-flash"
CHUNK_PROCESSING_MODEL = os.getenv("CHUNK_PROCESSING_MODEL", DEFAULT_MODEL)
GROUND_TRUTH_MODEL = os.getenv("GROUND_TRUTH_MODEL", DEFAULT_MODEL)

# Reasoning depth per call (Gemini 3.x thinking_level): low | medium | high. THINKING_LEVEL
# applies to every call that reasons over customer documents, CHUNK_THINKING_LEVEL to the bulk
# extraction in the AI refiner. "minimal" exists only on 3.5/3.6 Flash and Flash-Lite; AiClient
# clamps it to "low" everywhere else (3.7+/3.8 Flash and the pro tier reject it with a 400).
THINKING_LEVEL = os.getenv("THINKING_LEVEL", "medium")
CHUNK_THINKING_LEVEL = os.getenv("CHUNK_THINKING_LEVEL", "low")

# Maker/checker (Vier-Augen-Prinzip): every answer that ends up in the report or produces
# a finding is re-judged by a second, independent call against the same source documents.
# Roughly doubles the calls on those stages — set to "false" to fall back to single-pass.
ENABLE_MAKER_CHECKER = _env_bool("ENABLE_MAKER_CHECKER", True)
# The checker runs on the ground-truth model by default, whatever the maker used.
CHECKER_MODEL = os.getenv("CHECKER_MODEL", GROUND_TRUTH_MODEL)

# Context caching: the source PDFs attached to a call are cached once per (model, document set)
# on Vertex AI and reused by every later call with the same set — the maker and its checker, and
# the Chapter-3 subchapters that read the same documents. Cached input costs a tenth of fresh
# input; a set below the 4,096-token minimum falls back to inline attachment automatically.
ENABLE_CONTEXT_CACHE = _env_bool("ENABLE_CONTEXT_CACHE", True)
CONTEXT_CACHE_TTL_S = _env_int("CONTEXT_CACHE_TTL_S", 3600)

# Batch tier: the AI refiner sends its chunk requests as one Vertex batch job (half price).
# A job that is not done after BATCH_DEADLINE_S is cancelled; what it delivered is kept and the
# remaining chunks run as standard calls. Below BATCH_MIN_ITEMS chunks the detour is not worth it.
ENABLE_BATCH = _env_bool("ENABLE_BATCH", True)
BATCH_DEADLINE_S = _env_int("BATCH_DEADLINE_S", 300)
BATCH_MIN_ITEMS = _env_int("BATCH_MIN_ITEMS", 5)

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
# Vertex batch jobs: one folder per job key holding the request JSONL and the output files.
# A rerun reads the output folder first, so an interrupted job's results are not paid twice.
BATCH_PREFIX = f"{TEMP_BASE}/batch/"

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
# Per category: every delivered file, its format, and whether/why it reaches the model.
DOCUMENT_INVENTORY_PATH = f"{RAG_BASE}/document_inventory.json"

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
