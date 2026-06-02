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
    # The application logs through the root logger at INFO in both modes. A previous
    # version set the root to DEBUG here and then immediately overrode it back to INFO,
    # so the promised app-level DEBUG logs never emitted. That contradiction is removed.
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        stream=sys.stdout,
    )

    # Suppress noisy third-party library logs for cleaner output.
    logging.getLogger("google.auth").setLevel(logging.WARNING)
    logging.getLogger("google.api_core").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
    # Suppress verbose JSON decoder stack traces.
    logging.getLogger("json").setLevel(logging.WARNING if config.is_test_mode else logging.ERROR)

    mode = "Test" if config.is_test_mode else "Production"
    logging.info(f"{mode} logging enabled at INFO; noisy third-party libraries suppressed.")