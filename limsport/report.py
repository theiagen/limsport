"""
Turns QC results human-readable by using log lines on stderr, a QC failure report (one
row per failing sample/column pair), and a QC summary (one row per sample with its
sample-level and set-level QC status, and the reasons for any failure).

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

SUMMARY_HEADER = ["sample", "sample_qc_status", "set_qc_status", "rationale"]


def log_summary(passed: int, total: int, controls: int = 0) -> None:
    """
    Logs how many samples passed QC.

    Args:
        passed: the number of samples that passed every QC rule.
        total: the number of samples QC was run against, not counting controls.
        controls: the number of skip_sample_qc samples, which have no row verdict.
    """
    if controls:
        logger.info(
            "%d/%d samples passed QC (%d sample(s) skipped sample-level QC)",
            passed,
            total,
            controls,
        )
    else:
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
    Warns that some rows failed QC because their file wouldn't parse.

    Logged separately from the QC summary because it usually means a broken path or
    an unreadable file, not a sample that genuinely failed its thresholds.

    Args:
        failed: the number of rows whose file_parsing failed.
        total: the number of rows QC was run against.
    """
    logger.warning(
        "%d/%d samples failed QC: file_parsing could not produce a value (see the QC report for each reason)",
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
        if not failure.input_column:
            # a run-level set_qc failure names no column
            logger.warning("%s: failed QC (%s)", failure.sample, failure.reason)
        elif failure.output_column != failure.input_column:
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


def _sample_qc_status(
    sample: str, sample_qc_failed: Collection[str], skipped: Collection[str]
) -> str:
    """
    Picks one sample's sample-level QC status; set_qc is reported separately.

    Args:
        sample: the sample name.
        sample_qc_failed: every sample with at least one per-row `qc` failure.
        skipped: the samples that skipped per-row QC (skip_sample_qc).

    Returns:
        SKIPPED for a skipped sample, FAIL for a sample with a per-row failure, and
        PASS otherwise.
    """
    if sample in skipped:
        return "SKIPPED"
    if sample in sample_qc_failed:
        return "FAIL"
    return "PASS"


def _rationale(failures: list[QCFailure]) -> str:
    """
    Joins one sample's failures into a single QC summary cell.

    Args:
        failures: the sample's failures, in the order they were found.

    Returns:
        Each failure as "<column>: <reason>" (just the reason when no column failed,
        as in a run-level set_qc failure), separated by "; ". Empty with no failures.
    """
    return "; ".join(
        f"{fail.output_column or fail.input_column}: {fail.reason}"
        if fail.output_column or fail.input_column
        else fail.reason
        for fail in failures
    )


def write_qc_summary(
    path: Path,
    samples: list[str],
    failures: list[QCFailure],
    skipped: Collection[str] = (),
    delimiter: str = "\t",
    sample_qc_failed: Collection[str] = (),
    set_qc_status: str = "SKIPPED",
) -> None:
    """
    Writes one row per sample with its sample-level and set-level QC status, and why
    it failed.

    Args:
        path: where to write the QC summary.
        samples: every sample QC ran on, in input order; repeats are written once.
        failures: every failure, sample-level and set_qc; each sample's are joined
          into its rationale.
        skipped: the samples that skipped per-row QC (skip_sample_qc).
        delimiter: the delimiter to join cells with; defaults to a tab.
        sample_qc_failed: the samples with at least one per-row `qc` failure.
        set_qc_status: the run's set_qc verdict, the same for every sample: PASS,
          FAIL, or SKIPPED when the config has no set_qc rules.
    """
    failures_by_sample: dict[str, list[QCFailure]] = {}
    for fail in failures:
        failures_by_sample.setdefault(fail.sample, []).append(fail)
    table_io.write_tsv(
        path,
        SUMMARY_HEADER,
        (
            [
                sample,
                _sample_qc_status(sample, sample_qc_failed, skipped),
                set_qc_status,
                _rationale(failures_by_sample.get(sample, [])),
            ]
            for sample in dict.fromkeys(samples)
        ),
        delimiter,
    )
