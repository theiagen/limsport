"""Turns QC results human-readable by using log lines on stderr,
and an optional TSV report (one row per failing sample/column pair)."""

import logging
from pathlib import Path

from . import table_io
from .config import QCFailure

logger = logging.getLogger("limsport")

_QC_REPORT_HEADER = ["sample", "column", "output_column", "operator", "expected", "actual", "reason"]


def log_summary(passed: int, total: int) -> None:
    logger.info("%d/%d samples passed QC", passed, total)


def log_no_qc_summary(included: int, total: int) -> None:
    # Used when no --config is given, so no QC ever ran
    logger.info("%d/%d samples included (no QC configured)", included, total)


def log_nothing_to_do() -> None:
    # used when there's nothing to do
    logger.info("no config, samples, or delimiter change given; nothing to do!")


def log_qc_failures(failures: list[QCFailure]) -> None:
    for failure in failures:
        if failure.output_column != failure.column:
            # only mention the rename when there is one
            logger.warning(
                "%s: column %r (output %r) failed QC (%s)",
                failure.sample,
                failure.column,
                failure.output_column,
                failure.reason,
            )
        else:
            logger.warning(
                "%s: column %r failed QC (%s)", failure.sample, failure.column, failure.reason
            )


def log_unknown_samples(unknown: set[str]) -> None:
    logger.warning("sample list references unknown sample(s): %s", sorted(unknown))


def write_qc_report(path: Path, failures: list[QCFailure]) -> None:
    """write one row per failure"""
    rows = [
        [
            f.sample,
            f.column,
            f.output_column,
            f.operator.value if f.operator is not None else "",
            str(f.expected) if f.expected is not None else "",
            f.actual or "",
            f.reason,
        ]
        for f in failures
    ]
    table_io.write_tsv(path, _QC_REPORT_HEADER, rows)
