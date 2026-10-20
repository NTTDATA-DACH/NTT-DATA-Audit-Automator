# bsi-audit-automator/src/audit/staleness.py
"""Detects stage results that were computed from inputs which have changed since.

A stage result in output/results is reused by every later run until --force. When an input it
was computed from (the parsed Grundschutz-Check, the system map, an earlier chapter) is
rewritten afterwards, the stale result silently carries the old data into the report: this is
how a chapter 3 computed from a pre-fix parse of A.4 reached a final report. Comparing the
blobs' modification times costs a few metadata reads and names the problem in the log.
"""
import logging
from typing import Any, Dict, List, Tuple

from src.constants import EXTRACTED_CHECK_DATA_PATH, GROUND_TRUTH_MAP_PATH, STAGE_RESULTS_PATH

# The intermediate files each stage result is computed from.
STAGE_INPUTS: Dict[str, Tuple[str, ...]] = {
    "Chapter-3": (EXTRACTED_CHECK_DATA_PATH, GROUND_TRUTH_MAP_PATH),
    "Chapter-4": (GROUND_TRUTH_MAP_PATH,),
    "Chapter-5": (EXTRACTED_CHECK_DATA_PATH, STAGE_RESULTS_PATH.format(stage_name="Chapter-4")),
}


def stale_inputs(gcs_client: Any, stage_name: str, result_path: str) -> List[str]:
    """Input blobs of `stage_name` that are newer than its stored result; logs a warning if any.

    Tolerates clients without `blob_updated` (test fakes) and any metadata error: a failed
    check must never fail the run, it only loses the warning.
    """
    blob_updated = getattr(gcs_client, "blob_updated", None)
    inputs = STAGE_INPUTS.get(stage_name, ())
    if blob_updated is None or not inputs:
        return []
    try:
        result_time = blob_updated(result_path)
        if result_time is None:
            return []
        newer = []
        for input_path in inputs:
            input_time = blob_updated(input_path)
            if input_time is not None and input_time > result_time:
                newer.append(input_path)
    except Exception as e:  # metadata only; never let the check break the pipeline
        logging.debug(f"Could not compare modification times for stage '{stage_name}': {e}")
        return []
    if newer:
        logging.warning(
            f"Stage '{stage_name}' result '{result_path}' is older than its input(s) {newer}; it was computed "
            f"from outdated data. Re-run the stage with --force before generating the report."
        )
    return newer
