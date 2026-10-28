"""
Turns QC results human-readable by using log lines on stderr, a QC failure report (one
row per failing sample/column pair), and a QC summary (one PASS/FAIL/CONTROL row per
sample).

External methods:
    - log_summary()
    - log_no_qc_summary()
    - log_nothing_to_do()
    - log_file_parsing_failures()
    - log_qc_failures()
    - log_missing_samples()
    - write_qc_report()
    - write_qc_summary()
"""

import logging
from collections.abc import Collection
from pathlib import Path

from . import table_io
from .qc import REPORT_HEADER, QCFailure

logger = logging.getLogger("limsport")

SUMMARY_HEADER = ["sample", "qc_status"]


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


def log_missing_samples(missing: set[str]) -> None:
    """
    Warns about sample names in the sample list that aren't in the input table.

    Args:
        missing: the sample names that matched no row.
    """
    logger.warning("sample list references a missing sample(s): %s", sorted(missing))


def write_qc_report(
    path: Path, failures: list[QCFailure], delimiter: str = "\t"
) -> None:
    """
    Writes one row per failure to a delimited table

    Args:
        path: where to write the QC report.
        failures: every failure to report, one row each.
        delimiter: the delimiter to join cells with; defaults to a tab.
    """
    # a generator, not a list: on a failed run there is one failure per sample
    table_io.write_tsv(
        path, REPORT_HEADER, (fail.to_list() for fail in failures), delimiter
    )


def _qc_status(sample: str, failed: set[str], skipped: Collection[str]) -> str:
    """
    Picks one sample's QC summary status.

    Args:
        sample: the sample name.
        failed: every sample with at least one failure.
        skipped: the samples that skipped per-row QC (skip_sample_qc).

    Returns:
        FAIL for a sample with any failure (a failed set_qc rule fails skipped samples
        too), CONTROL for a skipped sample, and PASS otherwise.
    """
    if sample in failed:
        return "FAIL"
    if sample in skipped:
        return "CONTROL"
    return "PASS"


def write_qc_summary(
    path: Path,
    samples: list[str],
    failures: list[QCFailure],
    skipped: Collection[str] = (),
    delimiter: str = "\t",
) -> None:
    """
    Writes one PASS/FAIL/CONTROL row per sample

    Args:
        path: where to write the QC summary.
        samples: every sample QC ran on, in input order; repeats are written once.
        failures: every failure; a sample with any failure is FAIL.
        skipped: the samples that skipped per-row QC (skip_sample_qc).
        delimiter: the delimiter to join cells with; defaults to a tab.
    """
    failed = {fail.sample for fail in failures}
    table_io.write_tsv(
        path,
        SUMMARY_HEADER,
        (
            [sample, _qc_status(sample, failed, skipped)]
            for sample in dict.fromkeys(samples)
        ),
        delimiter,
    )
