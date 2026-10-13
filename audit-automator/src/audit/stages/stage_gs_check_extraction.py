# bsi-audit-automator/src/audit/stages/stage_gs_check_extraction.py
import asyncio
import logging
import json
from typing import Dict, Any, Optional

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.document_ai_client import DocumentAiClient
from src.clients.ai_client import AiClient
from src.clients.rag_client import RagClient
from src.clients.spreadsheet import SHEET_SUFFIXES
from src.constants import EXTRACTED_CHECK_DATA_PATH

from .gs_extraction.ground_truth_mapper import GroundTruthMapper
from .gs_extraction.document_processor import DocumentProcessor
from .gs_extraction.block_grouper import BlockGrouper
from .gs_extraction.ai_refiner import AiRefiner
from .gs_extraction.data_processor import DataProcessor
from .gs_extraction.sheet_parser import parse_grundschutz_check, reconcile_zielobjekte


class GrundschutzCheckExtractionRunner:
    """
    Main orchestrator for the Ground-Truth-Driven Semantic Chunking pipeline.
    Coordinates the four-stage process of extracting structured security requirements
    from the Grundschutz-Check document.

    When the Grundschutz-Check arrives as a spreadsheet (XLSX/XLSM/CSV export of the GRC
    tool), steps 2-4 are replaced by a deterministic parse: no Document AI, no AI refiner.
    The PDF path stays the fallback.
    """
    STAGE_NAME = "Grundschutz-Check-Extraction"

    def __init__(self, config: AppConfig, gcs_client: GcsClient, doc_ai_client: DocumentAiClient, ai_client: AiClient, rag_client: RagClient):
        self.config = config
        self.gcs_client = gcs_client
        self.rag_client = rag_client

        # Initialize specialized processors
        self.ground_truth_mapper = GroundTruthMapper(ai_client, rag_client, gcs_client)
        self.document_processor = DocumentProcessor(gcs_client, doc_ai_client, rag_client, config)
        self.block_grouper = BlockGrouper(gcs_client)
        self.ai_refiner = AiRefiner(ai_client, gcs_client)

        logging.info(f"Initialized runner for stage: {self.STAGE_NAME}")

    def _find_check_sheet(self) -> Optional[str]:
        """Blob name of a Grundschutz-Check spreadsheet, if the customer delivered one."""
        sheets = self.rag_client.get_blob_names_for_categories(["Grundschutz-Check"], suffixes=SHEET_SUFFIXES)
        if not sheets:
            return None
        if len(sheets) > 1:
            logging.warning(f"{len(sheets)} Grundschutz-Check spreadsheets found; using '{sheets[0]}': {sheets}")
        return sheets[0]

    async def _extract_from_sheet(self, blob_name: str, system_map: Dict[str, Any], force_overwrite: bool) -> None:
        """Parse the spreadsheet into the same file the AI refiner would write."""
        if not force_overwrite and self.gcs_client.blob_exists(EXTRACTED_CHECK_DATA_PATH):
            logging.info("Final extracted check results file exists. Skipping spreadsheet parse.")
            return

        logging.info(f"Grundschutz-Check delivered as spreadsheet '{blob_name}': parsing deterministically (no Document AI, no AI calls).")
        data = await asyncio.to_thread(self.gcs_client.download_blob_as_bytes, self.gcs_client.bucket.blob(blob_name))
        parsed = parse_grundschutz_check(data, blob_name)
        anforderungen = parsed["anforderungen"]
        if not anforderungen:
            raise ValueError(
                f"Spreadsheet '{blob_name}' yielded no requirements: its layout does not match the parser's "
                "assumptions (see sheet_parser.py). Fix the export or remove the sheet to fall back to the PDF path."
            )

        matched = reconcile_zielobjekte(anforderungen, system_map)
        deduplicated = DataProcessor.deduplicate_requirements(anforderungen)
        final_output = {
            "anforderungen": deduplicated,
            # Provenance: the stage-3 QS trail should show the check was computed, not generated.
            "quelle": {"datei": blob_name, "verfahren": "spreadsheet-parser", "dokument": parsed["dokument"]},
        }
        await self.gcs_client.upload_from_string_async(
            json.dumps(final_output, indent=2, ensure_ascii=False), EXTRACTED_CHECK_DATA_PATH
        )
        logging.info(
            f"Parsed {len(deduplicated)} requirements over {len(parsed['zielobjekte'])} Zielobjekte and "
            f"{len(parsed['baustein_to_zielobjekt_mapping'])} Bausteine from '{blob_name}'; "
            f"{matched} entries matched to the system map. Deterministic: 0 AI calls."
        )

    async def run(self, force_overwrite: bool = False) -> Dict[str, Any]:
        """Main execution method for the full extraction and refinement pipeline."""
        logging.info(f"Executing stage: {self.STAGE_NAME}")

        try:
            # Step 1: Establish Ground Truth system structure
            system_map = await self.ground_truth_mapper.create_system_structure_map(force_overwrite)

            sheet_blob = self._find_check_sheet()
            if sheet_blob:
                await self._extract_from_sheet(sheet_blob, system_map, force_overwrite)
                return {"status": "success", "message": f"Stage {self.STAGE_NAME} completed from spreadsheet '{sheet_blob}'."}

            logging.info("No Grundschutz-Check spreadsheet found; using the PDF path (Document AI + AI refiner).")
            # Step 2: Process document with Document AI Layout Parser
            await self.document_processor.execute_layout_parser_workflow(force_overwrite)

            # Step 3: Group layout blocks by Zielobjekt context
            await self.block_grouper.group_layout_blocks_by_zielobjekt(system_map, force_overwrite)

            # Step 4: Refine grouped blocks with AI to extract structured requirements
            await self.ai_refiner.refine_grouped_blocks_with_ai(system_map, force_overwrite)

            return {"status": "success", "message": f"Stage {self.STAGE_NAME} completed successfully."}

        except Exception as e:
            logging.error(f"Stage {self.STAGE_NAME} failed: {e}", exc_info=True)
            return {"status": "error", "message": f"Stage {self.STAGE_NAME} failed: {str(e)}"}
