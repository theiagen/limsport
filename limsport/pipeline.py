"""
Orchestrates LIMSport by reading the config, working out the layout against
the input's header, reading the rows, and reporting what happened

External methods:
    - run_export()
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import qc, report, table_io
from .config import ExportConfig, SetQCRule, load_config
from .exceptions import FileParsingError, InputTableError
from .ingest import IngestSummary, read_input
from .layout import ExportLayout, build_layout, validate_file_parsing_allowed


@dataclass
class _QCOutcome:
    """
    The final QC verdict

    Attributes:
        failures: every QC failure to log and report.
        passed_rows: how many rows made it to the output.
        run_failed: True when a set_qc rule failed the whole run
    """

    failures: list[qc.QCFailure] = field(default_factory=list)
    passed_rows: int = 0
    run_failed: bool = False


def _load_sample_list(path: Path) -> set[str]:
    """
    Reads the sample names to filter for (from `--samples`)

    Args:
        path: the file listing one sample name per line.

    Returns:
        The sample names, stripped of surrounding whitespace, with blank lines dropped.
    """
    names = {line.strip() for line in path.read_text().splitlines()}
    names.discard("")  # remove blank lines
    return names


def _warn_missing_samples(
    requested_samples: set[str] | None, summary: IngestSummary
) -> None:
    """
    Warns about names in the --samples file that never showed up in the input.

    Args:
        requested_samples: the sample names asked for, or None if none were.
        summary: a finished summary, read for the samples it actually saw.
    """
    if requested_samples is None:
        return
    missing = requested_samples - summary.seen_samples
    if missing:
        report.log_missing_samples(missing)


def _check_set_qc_matched_samples(
    set_qc_rules: list[SetQCRule], summary: IngestSummary, input_path: Path
) -> None:
    """
    Confirms every set_qc rule actually matched the sample(s) it claimed.

    A rule that matches nothing is a LIMSport error, not a QC failure: there's no
    sample to attach a failure to, so the run aborts before any output is written.

    Args:
        set_qc_rules: the config's run-level rules, or an empty list.
        summary: the finished ingest summary
        input_path: the input table's path, used in the error messages.

    Raises:
        InputTableError: if a rule matched no sample at all, or if an exact-name
          matcher named a sample this run never produced.
    """
    for rule in set_qc_rules:
        matched = summary.set_qc_matched[rule.rule_name]
        if rule.match_samples.samples is not None:
            # require all named samples to be present
            missing = [s for s in rule.match_samples.samples if s not in matched]
            if missing:
                raise InputTableError(
                    f"{input_path}: set_qc rule {rule.rule_name!r} match_samples.samples includes "
                    f"sample(s) not found in this run: {missing}"
                )
        elif not matched:
            raise InputTableError(
                f"{input_path}: set_qc rule {rule.rule_name!r} matched no samples in this run"
            )


def _check_file_parsing_produced_something(
    summary: IngestSummary, input_path: Path
) -> None:
    """
    Aborts when file_parsing failed for every row that tried it.

    Independent of --max-file-parsing-failures

    Args:
        summary: the finished summary, read for its parsing counters.
        input_path: the input table's path, used in the error message.

    Raises:
        FileParsingError: if at least one row attempted file_parsing and none
          succeeded.
    """
    if summary.parse_failed_rows and summary.parse_failed_rows == summary.expanded_rows:
        raise FileParsingError(
            f"{input_path}: file_parsing failed on every row it ran against "
            f"({summary.parse_failed_rows}/{summary.expanded_rows}), so the output would be "
            f"empty; this is usually a broken command, path template, or missing "
            f"dependency rather than bad data. Last failure: {summary.last_parse_failure}"
        )


def _whole_run_failures(
    run_failed_rules: list[SetQCRule], summary: IngestSummary
) -> list[qc.QCFailure]:
    """
    Builds the failure list for a run that a set_qc rule failed.

    Every sample fails, not only the offending one: the sample(s) that violated a
    set_qc rule keep their real, full-detail failures, and every other sample in the
    run gets one collateral row naming the rule(s) that failed the run.

    Args:
        run_failed_rules: the set_qc rules that collected at least one failure.
        summary: the finished summary, read for its failures and sample names.

    Returns:
        Every failure to log and report, the offending samples' first.
    """
    failures: list[qc.QCFailure] = []
    offending_samples: set[str] = set()
    failing_rule_names = [rule.rule_name for rule in run_failed_rules]
    # identical for every collateral row, so build it once rather than per sample
    collateral_reason = f"run failed QC due to set_qc rule(s): {failing_rule_names}"

    for rule in run_failed_rules:
        rule_failures = summary.set_qc_failures[rule.rule_name]
        failures.extend(rule_failures)
        offending_samples.update(f.sample for f in rule_failures)

    for sample in summary.sample_names:
        if sample in offending_samples:
            # already has its own full-detail failure above
            continue
        failures.append(
            qc.QCFailure(
                sample=sample,
                input_column="",
                output_column="",
                operator=None,
                expected=None,
                actual=None,
                reason=collateral_reason,
            )
        )
    return failures


def _apply_qc_verdict(
    set_qc_rules: list[SetQCRule], summary: IngestSummary
) -> _QCOutcome:
    """
    Settles the run's verdict, now that set_qc's whole-run result is known.

    The rows themselves were already written during the summary so it's only to decide
    if we keep what was written.

    Args:
        set_qc_rules: the config's run-level rules, or an empty list.
        summary: the finished summary.

    Returns:
        The failures to report, and how many rows count as passing.
    """
    run_failed_rules = [
        rule for rule in set_qc_rules if summary.set_qc_failures[rule.rule_name]
    ]
    if run_failed_rules:
        # A set_qc rule failed, so the entire run fails QC. The run-level verdict
        # replaces anything written
        return _QCOutcome(
            failures=_whole_run_failures(run_failed_rules, summary), run_failed=True
        )

    return _QCOutcome(failures=summary.row_failures, passed_rows=summary.written_rows)


def _write_header_only_output(
    output_path: Path, layout: ExportLayout, output_delimiter: str
) -> None:
    """
    Writes the output table a failed run gets: the header, and no rows.

    Args:
        output_path: where to write the output table.
        layout: read for its output header.
        output_delimiter: the delimiter to write with.
    """
    table_io.write_tsv(
        output_path, layout.output_header, [], delimiter=output_delimiter
    )


def _settle_run(
    set_qc_rules: list[SetQCRule], summary: IngestSummary, input_path: Path
) -> _QCOutcome:
    """
    Runs the checks that need the whole input read, then returns the run's verdict.

    Args:
        set_qc_rules: the config's run-level rules, or an empty list.
        summary: the finished read of the input.
        input_path: the input table's path, used in the error messages.

    Returns:
        The failures to report, and how many rows count as passing.

    Raises:
        InputTableError: if a set_qc rule matched no sample it named.
        FileParsingError: if file_parsing failed on every row it ran against.
    """
    _check_set_qc_matched_samples(set_qc_rules, summary, input_path)
    # a no-op after the pre-pass, which expands no rows and so parses no files
    _check_file_parsing_produced_something(summary, input_path)
    return _apply_qc_verdict(set_qc_rules, summary)


def _report_results(
    config: ExportConfig | None,
    summary: IngestSummary,
    outcome: _QCOutcome,
    qc_report_path: Path | None,
) -> None:
    """
    Logs the run summary and writes the QC report

    Args:
        config: the loaded config, or None if there wasn't one.
        summary: the finished ingest summary
        outcome: the QC result
        qc_report_path: where to write the QC failure report, or None to skip it.
    """
    if config is None:
        # no config means no QC ever ran, so there's nothing to report
        report.log_no_qc_summary(summary.candidate_rows, summary.total_rows)
        return

    if summary.parse_failed_rows:
        report.log_file_parsing_failures(
            summary.parse_failed_rows, summary.candidate_rows
        )
    report.log_summary(passed=outcome.passed_rows, total=summary.candidate_rows)
    report.log_qc_failures(outcome.failures)
    if qc_report_path is not None:
        report.write_qc_report(qc_report_path, outcome.failures)


def run_export(
    input_path: Path,
    config_path: Path | None,
    samples_path: Path | None,
    output_path: Path,
    qc_report_path: Path | None,
    output_delimiter: str = "\t",
    allow_file_parsing: bool = False,
    max_file_parsing_failures: int | None = None,
) -> None:
    """
    Runs the whole export by reading the input table, applying the config and sample
    filters, writing the output table, and reporting what happened.

    Args:
        input_path: the input table to read; its delimiter is auto-detected.
        config_path: the YAML config for column mapping and QC, or None to pass every
          column through unchanged.
        samples_path: a file listing the sample names to include, or None to keep all.
        output_path: where to write the output table.
        qc_report_path: where to write the QC failure report, or None to skip it.
        output_delimiter: the delimiter to write the output table with.
        allow_file_parsing: whether the config may run file_parsing commands.
        max_file_parsing_failures: how many rows may lose their file_parsing before
          the run aborts, or None for no limit. A file that won't parse fails its
          own row's QC; this is the guard against that quietly emptying the output.

    Raises:
        InputTableError: if the config or a set_qc rule references a column that is
          missing or duplicated in the header, or if a set_qc rule matches no sample in
          this run.
        ConfigError: if the config is invalid, or uses file_parsing without
          `allow_file_parsing`.
        FileParsingError: if more than `max_file_parsing_failures` rows fail to
          parse, or if file_parsing failed on every row it ran against.
        ToolNotFoundError: if a file_parsing path needs a cloud CLI that isn't
          installed.
    """
    # auto-detect delimiter
    input_delimiter = table_io.detect_delimiter(input_path)

    if (
        config_path is None
        and samples_path is None
        and output_delimiter == input_delimiter
    ):
        # you might want to reconsider your input parameters
        report.log_nothing_to_do()
        return

    header = table_io.get_input_header(input_path, input_delimiter)
    config: ExportConfig | None = (
        load_config(config_path) if config_path is not None else None
    )
    if config is not None:
        # a capability question, not a header one -- refuse before the input is
        # touched, so a column error can't mask it
        validate_file_parsing_allowed(config.columns or [], allow_file_parsing)
    layout = build_layout(config, header, input_path)
    set_qc_rules = config.set_qc if config is not None else []
    requested_samples = (
        _load_sample_list(samples_path) if samples_path is not None else None
    )

    if layout.set_qc_prepass:
        # check for set_qc failures if file parsing was included to avoid GCP downloads
        pre_summary = read_input(
            input_path,
            input_delimiter,
            layout,
            set_qc_rules,
            requested_samples,
            writer=None,  # set_qc only
        )
        _warn_missing_samples(requested_samples, pre_summary)
        pre_outcome = _settle_run(set_qc_rules, pre_summary, input_path)
        if pre_outcome.run_failed:
            _write_header_only_output(output_path, layout, output_delimiter)
            _report_results(config, pre_summary, pre_outcome, qc_report_path)
            return

    # keep the output staged on disk instead of memory
    staging_path = output_path.with_name(output_path.name + ".tmp")
    try:
        with table_io.open_row_writer(
            staging_path, layout.output_header, output_delimiter
        ) as writer:
            summary = read_input(
                input_path,
                input_delimiter,
                layout,
                set_qc_rules,
                requested_samples,
                writer,
                max_file_parsing_failures,
            )

        if not layout.set_qc_prepass:
            _warn_missing_samples(requested_samples, summary)

        outcome = _settle_run(set_qc_rules, summary, input_path)
        if outcome.run_failed:
            # a set_qc rule failed, so nothing that was staged counts
            _write_header_only_output(output_path, layout, output_delimiter)
        else:
            os.replace(staging_path, output_path)
    finally:
        # remove staging file
        staging_path.unlink(missing_ok=True)

    _report_results(config, summary, outcome, qc_report_path)
