# main.py
import argparse
import logging

from src.config import config
from src.logging_setup import setup_logging

def main():
    """
    Main entry point for the BSI Audit Automator.
    Parses command-line arguments to determine which pipeline stage to run.
    """
    # First, set up logging so we can see output from the start.
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
        help='Run a single audit stage for a specific report subchapter (e.g., --run-stage 3.1).'
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

    try:
        # Here we will instantiate and call our controller classes based on the args.
        if args.run_etl:
            logging.info("Starting ETL phase...")
            # from src.etl.processor import EtlProcessor
            # etl_processor = EtlProcessor(config)
            # etl_processor.run()
            logging.info("Placeholder: ETL phase would run here.")

        elif args.run_stage:
            logging.info(f"Starting single audit stage: {args.run_stage}...")
            # from src.audit.controller import AuditController
            # controller = AuditController(config)
            # controller.run_single_stage(args.run_stage)
            logging.info(f"Placeholder: Stage {args.run_stage} would run here.")
        
        elif args.run_all_stages:
            logging.info("Starting all audit stages sequentially...")
            # from src.audit.controller import AuditController
            # controller = AuditController(config)
            # controller.run_all_stages()
            logging.info("Placeholder: All stages would run here.")

        elif args.generate_report:
            logging.info("Starting final report assembly...")
            # from src.audit.report_generator import ReportGenerator
            # generator = ReportGenerator(config)
            # generator.assemble_report()
            logging.info("Placeholder: Report assembly would run here.")

    except Exception as e:
        logging.critical(f"A critical error occurred in the pipeline: {e}", exc_info=True)
        exit(1)

    logging.info("Pipeline step completed successfully.")


if __name__ == "__main__":
    main()