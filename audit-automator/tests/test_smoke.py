"""Smoke tests for the audit-automator package.

Deliberately dependency-light: these only import `src.config` and `src.constants`,
which do not require a GCP environment or the cloud SDKs. Run from the
`audit-automator/` directory:

    pip install -r requirements-dev.txt
    pytest
"""
import importlib

import pytest

REQUIRED_ENV = [
    "GCP_PROJECT_ID", "SOURCE_PREFIX", "OUTPUT_PREFIX",
    "AUDIT_TYPE", "REGION", "DOC_AI_PROCESSOR_NAME", "BUCKET_NAME",
]


def test_config_module_is_importable_without_env(monkeypatch):
    """MAX-10: importing src.config must NOT exit/raise even with no GCP env set."""
    for var in REQUIRED_ENV:
        monkeypatch.delenv(var, raising=False)
    module = importlib.import_module("src.config")
    importlib.reload(module)  # re-run module top-level with the env cleared
    assert hasattr(module, "load_config_from_env")


def test_load_config_raises_on_missing_env(monkeypatch):
    """Missing required env should raise ValueError (not silently succeed)."""
    from src.config import load_config_from_env
    for var in REQUIRED_ENV:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValueError):
        load_config_from_env()


def test_load_config_succeeds_with_full_env(monkeypatch):
    from src.config import load_config_from_env
    for var in REQUIRED_ENV:
        monkeypatch.setenv(var, "x")
    monkeypatch.setenv("MAX_CONCURRENT_AI_REQUESTS", "3")
    cfg = load_config_from_env()
    assert cfg.output_prefix == "x"
    assert cfg.max_concurrent_ai_requests == 3


def test_findings_path_constant_is_consistent():
    """MAX-3: the constant readers/writers share must derive from RESULTS_BASE."""
    from src.constants import ALL_FINDINGS_PATH, RESULTS_BASE
    assert ALL_FINDINGS_PATH == f"{RESULTS_BASE}/all_findings.json"


def test_models_are_current_generation():
    """MAX-5: defaults must stay on current, generally available models.

    Preview models are excluded deliberately: they get shut down without much notice
    (gemini-3.1-flash-lite-preview and gemini-3-pro-preview both did), which would
    break an audit run mid-flight.
    """
    from src.constants import GROUND_TRUTH_MODEL, CHUNK_PROCESSING_MODEL
    for model in (GROUND_TRUTH_MODEL, CHUNK_PROCESSING_MODEL):
        assert "gemini-2.5" not in model
        assert "gemini-2.0" not in model
        assert not model.endswith("-preview")


def test_block_grouper_does_not_call_sys_exit():
    """MAX-2: regression guard — the no-marker path must not kill the process."""
    import pathlib
    src = pathlib.Path("src/audit/stages/gs_extraction/block_grouper.py").read_text()
    # Ignore comments so the explanatory note about the old behavior doesn't trip this.
    code_only = [line.split("#", 1)[0] for line in src.splitlines()]
    assert not any("sys.exit(" in line for line in code_only)
