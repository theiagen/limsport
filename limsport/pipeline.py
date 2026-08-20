"""
Orchestrates one export end to end: reads the config, works out the layout against
the input's header, reads the rows, settles the run's verdict once run-level QC has
had its say, and reports what happened.

The steps themselves live elsewhere -- layout.py plans, ingest.py reads, qc.py judges
one row, report.py tells the user. What's here is the order they run in, and the
decisions that can only be made once the whole input has been seen.

External methods:
    - run_export()
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import qc, report, table_io
from .config import ExportConfig, SetQCRule, load_config
from .exceptions import FileParsingError, InputTableError
from .ingest import InputSummary, read_input
from .layout import ExportLayout, build_layout, validate_file_parsing_allowed


@dataclass
class _QCOutcome:
    """
    The run's final verdict, once set_qc's whole-run result is known.

    Attributes:
        failures: every QC failure to log and report.
        passed_rows: how many rows made it to the output.
        run_failed: True when a set_qc rule failed the whole run, so whatever was
          staged must be thrown away and the output is header-only.
    """

    failures: list[qc.QCFailure] = field(default_factory=list)
    passed_rows: int = 0
    run_failed: bool = False


def _load_sample_list(path: Path) -> set[str]:
    """
    Reads the sample names to filter for; the names come from the `--samples` file, not from the input table.

    Args:
        path: the file listing one sample name per line.

    Returns:
        The sample names, stripped of surrounding whitespace, with blank lines dropped.
    """
    names = {line.strip() for line in path.read_text().splitlines()}
    names.discard("")  # remove blank lines
    return names


def _warn_unknown_samples(
    requested_samples: set[str] | None, summary: InputSummary
) -> None:
    """
    Warns about names in the --samples file that never showed up in the input.

    Logged before the set_qc checks that can abort the run, so the warning is still
    seen when the run then fails.

    Args:
        requested_samples: the sample names asked for, or None if none were.
        summary: a finished summary, read for the samples it actually saw.
    """
    if requested_samples is None:
        return
    unknown = requested_samples - summary.seen_samples
    if unknown:
        report.log_unknown_samples(unknown)


def _check_set_qc_matched(
    set_qc_rules: list[SetQCRule], summary: InputSummary, input_path: Path
) -> None:
    """
    Confirms every set_qc rule actually matched the sample(s) it claimed.

    A rule that matches nothing is a LIMSport error, not a QC failure: there's no
    sample to attach a failure to, so the run aborts before any output is written.

    Args:
        set_qc_rules: the config's run-level rules, or an empty list.
        summary: the finished summary, read for its set_qc_matched bookkeeping.
        input_path: the input table's path, used in the error messages.

    Raises:
        InputTableError: if a rule matched no sample at all, or if an exact-name
          matcher named a sample this run never produced.
    """
    for rule in set_qc_rules:
        matched = summary.set_qc_matched[rule.rule_name]
        if rule.match_samples.samples is not None:
            # An exact-name matcher names specific samples the caller expects to
            # exist (e.g. known controls) -- every one of them must actually show
            # up, not just at least one, or a typo'd/missing name would be
            # silently never checked.
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
    summary: InputSummary, input_path: Path
) -> None:
    """
    Aborts when file_parsing failed for every row that tried it.

    One bad file among many is data, and fails only its own row. Every file failing
    is a broken command, path template, or missing dependency -- there is no output
    left to write, and a header-only table plus an exit code of 0 would report that
    as success. Independent of --max-file-parsing-failures, which tunes how much
    genuinely bad data to tolerate; this asks whether anything worked at all.

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
    run_failed_rules: list[SetQCRule], summary: InputSummary
) -> list[qc.QCFailure]:
    """
    Builds the failure list for a run that a set_qc rule failed.

    Every sample fails, not only the offending one: the sample(s) that violated a
    rule keep their real, full-detail failures, and every other sample in the run
    gets one collateral row naming the rule(s) that failed the run.

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


def _apply_qc(set_qc_rules: list[SetQCRule], summary: InputSummary) -> _QCOutcome:
    """
    Settles the run's verdict, now that set_qc's whole-run result is known.

    The rows themselves were already written during the summary, so there is nothing
    left to build here -- only to decide whether what was written counts, and
    which failures to report.

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
        # A set_qc rule failed, so the entire run fails QC -- not just the
        # sample(s) that violated it. Whatever rows reached the staging file are
        # discarded, and the per-row failures go with them: the run-level verdict
        # replaces them.
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
    set_qc_rules: list[SetQCRule], summary: InputSummary, input_path: Path
) -> _QCOutcome:
    """
    Runs the checks that need the whole input read, then returns the run's verdict.

    The single place any end-of-input step belongs: both the set_qc pre-pass and the
    real pass call it, so a new check is added once rather than kept in step across
    two paths that must stay identical.

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
    _check_set_qc_matched(set_qc_rules, summary, input_path)
    # a no-op after the pre-pass, which expands no rows and so parses no files
    _check_file_parsing_produced_something(summary, input_path)
    return _apply_qc(set_qc_rules, summary)


def _report_results(
    config: ExportConfig | None,
    summary: InputSummary,
    outcome: _QCOutcome,
    qc_report_path: Path | None,
) -> None:
    """
    Logs the run summary, and writes the QC report when one was asked for.

    Args:
        config: the loaded config, or None if there wasn't one.
        summary: the finished summary, read for its row counts.
        outcome: the QC result, read for its failures and pass count.
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
        # Read the input once for set_qc alone, before any file_parsing runs. A
        # set_qc failure fails the whole run, so without this the expensive work
        # is done and then thrown away -- and worst case that's all of it, since
        # controls often sit at the end of the table and a rule matching zero
        # samples isn't detected until the read finishes either way.
        pre_summary = read_input(
            input_path,
            input_delimiter,
            layout,
            set_qc_rules,
            requested_samples,
            writer=None,  # set_qc only: evaluate the gate, expand and write nothing
        )
        _warn_unknown_samples(requested_samples, pre_summary)
        pre_outcome = _settle_run(set_qc_rules, pre_summary, input_path)
        if pre_outcome.run_failed:
            # nothing expensive has run yet, and now none of it has to
            _write_header_only_output(output_path, layout, output_delimiter)
            _report_results(config, pre_summary, pre_outcome, qc_report_path)
            return

    # Rows are written as they're read, but to a staging file rather than to
    # output_path, because one decision can't be made until the last row is in:
    # a failing set_qc rule fails every row in the run, not just the sample(s) it
    # matched. So output_path is only created at the very end -- by renaming the
    # staging file when the run passed, or by writing a header-only table when it
    # didn't. Anything that raises in between leaves output_path untouched, which
    # also means an earlier export at that path survives a failed re-run.
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
            # the pre-pass already logged these
            _warn_unknown_samples(requested_samples, summary)

        # set_qc's verdict is known, so what was staged can finally be judged
        outcome = _settle_run(set_qc_rules, summary, input_path)

        if outcome.run_failed:
            # a set_qc rule failed, so nothing that was staged counts
            _write_header_only_output(output_path, layout, output_delimiter)
        else:
            os.replace(staging_path, output_path)
    finally:
        # a no-op after a successful replace; cleans up on every other path
        staging_path.unlink(missing_ok=True)

    _report_results(config, summary, outcome, qc_report_path)
