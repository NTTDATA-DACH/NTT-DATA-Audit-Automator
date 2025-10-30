# src/config.py
import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

@dataclass(frozen=True)
class AppConfig:
    """
    Dataclass to hold all application configuration. It's frozen to prevent
    accidental modification after initialization.
    """
    gcp_project_id: str
    source_prefix: str
    output_prefix: str
    audit_type: str
    region: str
    doc_ai_processor_name: str
    max_concurrent_ai_requests: int
    is_test_mode: bool
    bucket_name: Optional[str] = None 

def load_config_from_env() -> AppConfig:
    """
    Loads configuration from environment variables, validates them,
    and returns a frozen AppConfig dataclass.

    Raises:
        ValueError: If a required environment variable is missing.

    Returns:
        AppConfig: The validated application configuration.
    """
    # Load .env file for local development. In a cloud environment, these
    # will be set directly.
    load_dotenv()

    required_vars = [
        "GCP_PROJECT_ID", "SOURCE_PREFIX", "OUTPUT_PREFIX", "AUDIT_TYPE", "REGION", "DOC_AI_PROCESSOR_NAME", "BUCKET_NAME"
    ]
    
    config_values = {}
    for var in required_vars:
        value = os.getenv(var)
        if not value:
            raise ValueError(f"Configuration Error: Missing required environment variable: {var}")
        # Convert to lowercase to match dataclass fields
        config_values[var.lower()] = value

    # Handle special case and boolean variables
    config_values["is_test_mode"] = os.getenv("TEST", "false").lower() == "true"
    
    # Load the new concurrency limit, defaulting to 5 if not set or invalid
    max_reqs_str = os.getenv("MAX_CONCURRENT_AI_REQUESTS", "5")
    config_values["max_concurrent_ai_requests"] = int(max_reqs_str) if max_reqs_str.isdigit() else 5

    return AppConfig(**config_values)

# Create a singleton instance to be imported by other modules.
# The try/except block ensures the application exits gracefully if config is invalid.
try:
    config = load_config_from_env()
except ValueError as e:
    print(e)
    exit(1)