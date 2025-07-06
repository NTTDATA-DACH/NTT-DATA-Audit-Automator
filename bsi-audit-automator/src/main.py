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

EMBEDDINGS_PATH_PREFIX = "vector_index_data/"

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

    parser.add_argument(
        '--force',
        action='store_true',
        help='Force re-running of stages that have already completed. Only applies to --run-all-stages.'
    )

    args = parser.parse_args()

    gcs_client = GcsClient(config)
    ai_client = AiClient(config)

    if args.run_etl:
        logging.info("Starting ETL phase...")
        etl_processor = EtlProcessor(config, gcs_client, ai_client)
        # **FIX**: The async run method must be awaited.
        await etl_processor.run()

    elif args.generate_report:
        logging.info("Starting final report assembly...")
        generator = ReportGenerator(config, gcs_client)
        await generator.assemble_report()

    else:  # These are the async audit tasks
        rag_dependent_tasks = args.run_stage or args.run_all_stages
        if rag_dependent_tasks:
            logging.info(f"Checking for required ETL output files in: {EMBEDDINGS_PATH_PREFIX}")
            embedding_files = [b for b in gcs_client.list_files(prefix=EMBEDDINGS_PATH_PREFIX) if "placeholder.json" not in b.name]
            
            if not embedding_files:
                logging.critical(
                    f"\n\n--- PREREQUISITE MISSING ---\n"
                    f"No embedding files found in '{EMBEDDINGS_PATH_PREFIX}' in bucket '{config.bucket_name}'.\n"
                    f"You must run the ETL process first to generate embeddings for the source documents.\n"
                    f"Please run: python -m src.main --run-etl\n"
                )
                exit(1)

        rag_client = RagClient(config, gcs_client, ai_client)
        controller = AuditController(config, gcs_client, ai_client, rag_client)

        if args.run_stage:
            await controller.run_single_stage(args.run_stage, force_overwrite=True)
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