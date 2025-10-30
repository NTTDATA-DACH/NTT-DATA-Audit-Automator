# src/main.py
import argparse
import logging
import asyncio

from .config import config
from .logging_setup import setup_logging
from .clients.gcs_client import GcsClient
from .clients.rag_client import RagClient
from .clients.ai_client import AiClient
from .audit.controller import AuditController
from .audit.report_generator import ReportGenerator

async def main_async():
    """
    Asynchronous main function to handle all pipeline operations.
    """
    setup_logging(config)

    parser = argparse.ArgumentParser(
        description="BSI Grundschutz Audit Automation Pipeline."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    
    group.add_argument(
        '--scan-previous-report',
        action='store_true',
        help='Run the stage to scan a previous audit report.'
    )
    group.add_argument(
        '--run-gs-check-extraction',
        action='store_true',
        help='Run only the Grundschutz-Check data extraction and mapping stage.'
    )
    group.add_argument(
        '--run-stage',
        type=str,
        help='Run a single audit stage (e.g., --run-stage Chapter-1).'
    )
    group.add_argument(
        '--run-all-stages',
        action='store_true',
        help='Run all audit generation stages sequentially.'
    )
    group.add_argument(
        '--generate-report',
        action='store_true',
        help='Assemble the final report from completed stage stubs.'
    )

    parser.add_argument(
        '--force',
        action='store_true',
        help='Force re-running of completed stages and re-classification of source documents.'
    )

    args = parser.parse_args()

    gcs_client = GcsClient(config)
    ai_client = AiClient(config)

    if args.generate_report:
        logging.info("Starting final report assembly...")
        generator = ReportGenerator(config, gcs_client)
        await generator.assemble_report()
        return
        
    # For all other tasks, we need the RagClient (Document Finder)
    logging.info("Initializing Document Finder Client...")
    try:
        rag_client = await RagClient.create(config, gcs_client, ai_client, force_remap=args.force)
    except Exception as e:
        logging.critical(f"Failed to initialize the Document Finder client. This can happen if no source documents are present. Error: {e}", exc_info=True)
        exit(1)

    controller = AuditController(config, gcs_client, ai_client, rag_client)

    if args.scan_previous_report:
        await controller.run_single_stage("Scan-Report", force_overwrite=args.force)
    elif args.run_gs_check_extraction:
        await controller.run_single_stage("Grundschutz-Check-Extraction", force_overwrite=args.force)
    elif args.run_stage:
        await controller.run_single_stage(args.run_stage, force_overwrite=args.force)
    elif args.run_all_stages:
        await controller.run_all_stages(force_overwrite=args.force)


def main():
    """
    Main entry point for the BSI Audit Automator.
    Parses command-line arguments and runs the appropriate async task.
    """
    try:
        asyncio.run(main_async())
        logging.info("Pipeline step completed successfully.")
    except Exception as e:
        logging.critical(f"A critical error occurred in the pipeline: {e}", exc_info=True)
        exit(1)

if __name__ == "__main__":
    main()
