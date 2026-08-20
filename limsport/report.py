"""
Turns QC results human-readable by using log lines on stderr and a TSV report (one row
per failing sample/column pair).

External methods:
    - log_summary()
    - log_no_qc_summary()
    - log_nothing_to_do()
    - log_file_parsing_failures()
    - log_qc_failures()
    - log_unknown_samples()
    - write_qc_report()
"""

import logging
from pathlib import Path

from . import table_io
from .qc import REPORT_HEADER, QCFailure

logger = logging.getLogger("limsport")


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


def log_file_parsing_failures(failed: int, total: int) -> None:
    """
    Warns that some rows were dropped because their file wouldn't parse.

    Logged separately from the QC summary because it usually means a broken path or
    an unreadable file, not a sample that genuinely failed its thresholds.

    Args:
        failed: the number of rows whose file_parsing failed.
        total: the number of rows QC was run against.
    """
    logger.warning(
        "%d/%d samples dropped: file_parsing could not produce a value (see the QC report for each reason)",
        failed,
        total,
    )


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
    # a generator, not a list: on a failed run there is one failure per sample
    table_io.write_tsv(path, REPORT_HEADER, (fail.to_list() for fail in failures))
