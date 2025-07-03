# src/main.py
import argparse
import logging
import asyncio

from .config import config
from .logging_setup import setup_logging
from .clients.gcs_client import GcsClient
from .clients.rag_client import RagClient
from .clients.ai_client import AiClient
from .etl.processor import EtlProcessor
from .audit.controller import AuditController
from .audit.report_generator import ReportGenerator

EMBEDDINGS_FILE_PATH = "vector_index_data/embeddings.json"

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

    try:
        if args.run_etl:
            logging.info("Starting ETL phase...")
            ai_client = AiClient(config)
            etl_processor = EtlProcessor(config, gcs_client, ai_client)
            etl_processor.run()

        elif args.generate_report:
            logging.info("Starting final report assembly...")
            generator = ReportGenerator(config, gcs_client)
            generator.assemble_report()

        else:  # These are the async audit tasks
            # --- PRE-FLIGHT CHECK for RAG-dependent stages ---
            rag_dependent_tasks = args.run_stage or args.run_all_stages
            if rag_dependent_tasks:
                logging.info(f"Checking for required ETL output file: {EMBEDDINGS_FILE_PATH}")
                if not gcs_client.blob_exists(EMBEDDINGS_FILE_PATH):
                    logging.critical(
                        f"\n\n--- PREREQUISITE MISSING ---\n"
                        f"The required file '{EMBEDDINGS_FILE_PATH}' was not found in bucket '{config.bucket_name}'.\n"
                        f"You must run the ETL process first to generate embeddings.\n"
                        f"Please run: python -m src.main --run-etl\n"
                    )
                    exit(1)
            ai_client = AiClient(config)
            rag_client = RagClient(config, gcs_client)
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