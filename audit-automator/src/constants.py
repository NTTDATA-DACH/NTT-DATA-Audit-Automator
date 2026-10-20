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


# One model for every task: gemini-4-argon on the Gemini Enterprise Agent Platform (formerly
# Vertex AI), GA on the global endpoint since 2026-10-13. Cost and quality are set per call by
# the thinking level and the output budget, never by a second model: there is no "flash" bulk
# model and no "pro" checker model any more. The env var is the rollback lever if the id is
# unavailable in a project, not a model menu.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-4-argon")

# Reasoning depth per call: low | medium | high (the API default is medium). THINKING_LEVEL
# applies to every call that reasons over customer documents, CHUNK_THINKING_LEVEL to the bulk
# extraction in the AI refiner, CHECKER_THINKING_LEVEL to the second opinion of the maker/checker
# pass. Legacy values "minimal"/"off" become "low" and "max" becomes "high" in AiClient; Argon
# returns 400 for them, and for thinking_budget.
THINKING_LEVEL = os.getenv("THINKING_LEVEL", "medium")
CHUNK_THINKING_LEVEL = os.getenv("CHUNK_THINKING_LEVEL", "low")
CHECKER_THINKING_LEVEL = os.getenv("CHECKER_THINKING_LEVEL", "high")
# Calls that only classify or extract (filename classification, ground-truth extraction, the
# scan of the previous report). Google places classification and extraction at "low" for
# gemini-4-argon, but the default stays at the maker's level: the ground-truth map is the
# foundation of every later stage, and the implicit cache only serves a later call at the same
# thinking level (two real runs: every same-level maker on the same documents hit the cache,
# every checker at "high" missed), so an extraction at "low" would leave the Chapter-3 calls on
# the same documents without a warm prefix.
EXTRACTION_THINKING_LEVEL = os.getenv("EXTRACTION_THINKING_LEVEL", "medium")

# Output budget per call: max_output_tokens counts thinking AND answer tokens. 65,536 is the API
# default; below 32,768 the model thinks the budget away and returns truncated JSON, so AiClient
# refuses such a value. Above 65,536 the SDK continues the answer in 64K hops (long decode); the
# 1,048,576-token window is shared by input, thinking and output.
MAX_OUTPUT_TOKENS = _env_int("MAX_OUTPUT_TOKENS", 65536)

# Maker/checker (Vier-Augen-Prinzip): every answer that ends up in the report or produces
# a finding is re-judged by a second, independent call against the same source documents.
# Roughly doubles the calls on those stages — set to "false" to fall back to single-pass.
ENABLE_MAKER_CHECKER = _env_bool("ENABLE_MAKER_CHECKER", True)

# Caching is implicit on gemini-4-argon: a request whose prefix (system instruction, then the
# attached documents) repeats a recent request's prefix of at least 6,144 tokens is billed at
# about 5 % for that part, for 15-20 minutes, with no API call. AiClient orders every request
# that way; there is no cache to create, configure or expire.

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
# the date stamp is deliberate, so there is no single constant path for it. The final
# stage (ReportFinalizer) reads the newest of them and writes report_<YYMMDD>_final.json.
REPORT_FILE_PREFIX = "report_"
FINAL_REPORT_SUFFIX = "_final"

# =============================================================================
# TEMPORARY PATHS - Short-lived processing files
# =============================================================================
TEMP_BASE = "output/temp"
TEMP_PDF_CHUNKS_PREFIX = f"{TEMP_BASE}/pdf_chunks/"
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

# Final stage over the assembled report: the model's duplicate groups are stored under a
# key over the findings and the prompt (a rerun on an unchanged report costs no call), and
# one log per report records every merge decision, including those not applied.
FINALIZER_BASE = f"{INTERMEDIARY_BASE}/report_finalizer"
# A duplicate group is applied only when the model is at least this sure; groups below the
# threshold are logged for the auditor and leave the report untouched.
FINALIZER_CONFIDENCE_THRESHOLD = 0.9
# A model group may fold at most this many findings of the current audit into one. Larger
# groups are topic clusters ("everything about A.1") rather than one deficiency and are left
# for the auditor, as are groups that mix categories within one audit.
FINALIZER_MAX_NEW_PER_GROUP = 3

# =============================================================================
# ASSET PATHS
# =============================================================================
PROMPT_CONFIG_PATH = "assets/json/prompt_config.json"

# BSI IT-Grundschutz-Kompendium, Edition 2023. Generated from the sha256-pinned official
# BSI XML by `python -m src.tools.build_bsi_catalog`; see readme.md ("Katalog aktualisieren").
CONTROL_CATALOG_PATH = "assets/json/bsi_kompendium_ed2023.json"

# The Kürzel under which the audit plan (Chapter 4), the parsed Grundschutz-Check and the
# checklist (Chapter 5) address the Informationsverbund as a whole. Process-layer Bausteine
# (ISMS, ORP, CON, OPS, DER) apply to it, and GRC exports give it no Kürzel of its own, so the
# three must agree on this fixed one or the customer's explanations never reach 5.5.2.
INFORMATIONSVERBUND_KUERZEL = "Informationsverbund"

# JSON Schema for the fully-assembled audit report (used by ReportGenerator to
# validate the report before saving). Replaces the former no-op structural check.
REPORT_SCHEMA_PATH = "assets/schemas/master_report_schema.json"
