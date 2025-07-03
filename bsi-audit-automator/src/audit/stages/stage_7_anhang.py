# src/audit/stages/stage_7_anhang.py
import logging
import json
from typing import Dict, Any

from src.config import AppConfig
from src.clients.gcs_client import GcsClient

class Chapter7Runner:
    """
    Handles generating the appendix for Chapter 7.
    - 7.1 is generated deterministically by listing GCS source files.
    - 7.2 (Deviations) is populated by the ReportGenerator from the central findings file.
    """
    STAGE_NAME = "Chapter-7"

    def __init__(self, config: AppConfig, gcs_client: GcsClient):
        self.config = config
        self.gcs_client = gcs_client
        logging.info(f"Initialized runner for stage: {self.STAGE_NAME}")

    async def _generate_referenzdokumente_table(self) -> Dict[str, Any]:
        """Generates the table of reference documents by listing source files in GCS."""
        logging.info("Generating subchapter 7.1 (Referenzdokumente) from GCS file list.")
        try:
            source_files = self.gcs_client.list_files()
            rows = []
            for i, blob in enumerate(source_files):
                rows.append({
                    "Nr.": f"A.{i}",
                    "Kurzbezeichnung": blob.name.split('/')[-1],
                    "Dateiname / Verweis": blob.name,
                    "Version, Datum": blob.updated.strftime("%Y-%m-%d") if blob.updated else "N/A",
                    "Relevante Änderungen": "Initial eingereicht für Audit."
                })
            # The key must match the structure in master_report_template.json
            return {"referenzdokumente": {"table": {"rows": rows}}}
        except Exception as e:
            logging.error(f"Failed to generate Referenzdokumente table: {e}", exc_info=True)
            return {"referenzdokumente": {"table": {"rows": []}}}

    async def run(self) -> dict:
        """Executes the generation logic for Chapter 7."""
        logging.info(f"Executing stage: {self.STAGE_NAME}")
        # Only one task remains for this chapter.
        result = await self._generate_referenzdokumente_table()
        logging.info(f"Successfully generated data for stage {self.STAGE_NAME}")
        return result