# src/logging_setup.py
import logging
import sys
from src.config import AppConfig

def setup_logging(config: AppConfig):
    """
    Sets up the root logger based on the execution mode from the config.

    Args:
        config: The application configuration object.
    """
    # In test mode, we want detailed logs at INFO level.
    # In production, we want high-level INFO, with details at DEBUG.
    log_level = logging.INFO if config.is_test_mode else logging.DEBUG
    
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        stream=sys.stdout,
    )

    # In production, set the root logger to INFO to see high-level status,
    # while our application-specific logs can be at the DEBUG level.
    if not config.is_test_mode:
        logging.getLogger().setLevel(logging.INFO)

        # Suppress noisy third-party library logs for cleaner production output
        logging.getLogger("google.auth").setLevel(logging.WARNING)
        logging.getLogger("google.api_core").setLevel(logging.WARNING)
        logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
        logging.info("Production logging enabled. Set root to INFO, app logs to DEBUG, and suppressed noisy libs.")
    else:
        logging.info("Test mode logging enabled. All INFO logs will be visible.")