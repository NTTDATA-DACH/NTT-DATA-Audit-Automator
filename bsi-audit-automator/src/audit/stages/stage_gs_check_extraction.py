# bsi-audit-automator/src/audit/stages/stage_gs_check_extraction.py
import logging
import json
from typing import Dict, Any

from src.config import AppConfig
from src.clients.gcs_client import GcsClient
from src.clients.document_ai_client import DocumentAiClient
from src.clients.ai_client import AiClient
from src.clients.rag_client import RagClient

from .gs_extraction.ground_truth_mapper import GroundTruthMapper
from .gs_extraction.document_processor import DocumentProcessor
from .gs_extraction.block_grouper import BlockGrouper
from .gs_extraction.ai_refiner import AiRefiner


class GrundschutzCheckExtractionRunner:
    """
    Main orchestrator for the Ground-Truth-Driven Semantic Chunking pipeline.
    Coordinates the four-stage process of extracting structured security requirements
    from the Grundschutz-Check document.
    """
    STAGE_NAME = "Grundschutz-Check-Extraction"

    def __init__(self, config: AppConfig, gcs_client: GcsClient, doc_ai_client: DocumentAiClient, ai_client: AiClient, rag_client: RagClient):
        self.config = config
        self.gcs_client = gcs_client
        
        # Initialize specialized processors
        self.ground_truth_mapper = GroundTruthMapper(ai_client, rag_client, gcs_client)
        self.document_processor = DocumentProcessor(gcs_client, doc_ai_client, rag_client, config)
        self.block_grouper = BlockGrouper(gcs_client)
        self.ai_refiner = AiRefiner(ai_client, gcs_client)
        
        logging.info(f"Initialized runner for stage: {self.STAGE_NAME}")

    async def run(self, force_overwrite: bool = False) -> Dict[str, Any]:
        """Main execution method for the full extraction and refinement pipeline."""
        logging.info(f"Executing stage: {self.STAGE_NAME}")
        
        try:
            # Step 1: Establish Ground Truth system structure
            system_map = await self.ground_truth_mapper.create_system_structure_map(force_overwrite)
            
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