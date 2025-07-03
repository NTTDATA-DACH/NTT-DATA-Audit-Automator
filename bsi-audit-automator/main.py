# main.py
import argparse
import logging
import asyncio

from src.config import config
from src.logging_setup import setup_logging
from src.clients.gcs_client import GcsClient
from src.clients.rag_client import RagClient
from src.clients.ai_client import AiClient
from src.etl.processor import EtlProcessor
from src.audit.controller import AuditController
from src.audit.report_generator import ReportGenerator

# bump

def main():
    """
    Main entry point for the BSI Audit Automator.
    Parses command-line arguments to determine which pipeline stage to run.
    """
    setup_logging(config)

    parser = argparse.ArgumentParser(
        description="BSI Grundschutz Audit Automation Pipeline."
    )
    # Define mutually exclusive arguments: you can only run one stage at a time.
    group = parser.add_mutually_exclusive_group(required=True)
    
    group.add_argument(
        '--run-etl',
        action='store_true',
        help='Run the ETL phase: Chunk, embed, and upload documents to GCS for indexing.'
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

    args = parser.parse_args()

    # Instantiate clients once
    gcs_client = GcsClient(config)
    ai_client = AiClient(config)
    rag_client = RagClient(config, gcs_client)

    try:
        if args.run_etl:
            logging.info("Starting ETL phase...")
            etl_processor = EtlProcessor(config, gcs_client, ai_client)
            etl_processor.run()

        elif args.generate_report:
            logging.info("Starting final report assembly...")
            generator = ReportGenerator(config, gcs_client)
            generator.assemble_report()

        else:  # These are the async audit tasks
            controller = AuditController(config, gcs_client, ai_client, rag_client)

            async def run_audit_tasks():
                if args.run_stage:
                    await controller.run_single_stage(args.run_stage)
                elif args.run_all_stages:
                    await controller.run_all_stages()

            asyncio.run(run_audit_tasks())

    except Exception as e:
        logging.critical(f"A critical error occurred in the pipeline: {e}", exc_info=True)
        exit(1)

    logging.info("Pipeline step completed successfully.")


if __name__ == "__main__":
    main()