"""
Turns QC results human-readable by using log lines on stderr and a TSV report (one row
per failing sample/column pair).

External methods:
    - log_summary()
    - log_no_qc_summary()
    - log_nothing_to_do()
    - log_qc_failures()
    - log_unknown_samples()
    - write_qc_report()
"""

import logging
from pathlib import Path

from . import table_io
from .qc import QCFailure

logger = logging.getLogger("limsport")

_QC_REPORT_HEADER = [
    "sample",
    "input_column",
    "output_column",
    "operator",
    "expected",
    "actual",
    "reason",
]


def log_summary(passed: int, total: int) -> None:
    """
    Logs how many samples passed QC.

    Args:
        passed: the number of samples that passed every QC rule.
        total: the number of samples QC was run against.
    """
    logger.info("%d/%d samples passed QC", passed, total)


def log_no_qc_summary(included: int, total: int) -> None:
    """
    Logs how many samples made it into the output when no QC was configured.

    Args:
        included: the number of samples written to the output.
        total: the number of samples in the input table.
    """
    logger.info("%d/%d samples included (no QC configured)", included, total)


def log_nothing_to_do() -> None:
    """
    Logs that there was nothing to do.
    """
    logger.info("no config, samples, or delimiter change given; nothing to do!")


def log_qc_failures(failures: list[QCFailure]) -> None:
    """
    Logs one warning line per QC failure.

    Args:
        failures: every failure found across every sample.
    """
    for failure in failures:
        if failure.output_column != failure.input_column:
            # only mention the rename when there is one
            logger.warning(
                "%s: column %r (output %r) failed QC (%s)",
                failure.sample,
                failure.input_column,
                failure.output_column,
                failure.reason,
            )
        else:
            logger.warning(
                "%s: column %r failed QC (%s)",
                failure.sample,
                failure.input_column,
                failure.reason,
            )


def log_unknown_samples(unknown: set[str]) -> None:
    """
    Warns about sample names in the sample list that aren't in the input table.

    Args:
        unknown: the sample names that matched no row.
    """
    logger.warning("sample list references unknown sample(s): %s", sorted(unknown))


def write_qc_report(path: Path, failures: list[QCFailure]) -> None:
    """
    Writes one row per failure to a TSV

    Args:
        path: where to write the QC report TSV.
        failures: every failure to report, one row each.
    """
    rows = [fail.to_list() for fail in failures]
    table_io.write_tsv(path, _QC_REPORT_HEADER, rows)
