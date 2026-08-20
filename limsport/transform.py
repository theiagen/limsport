"""
Orchestrates the export: reads the input TSV, applies the config
(column allow-list, output_column, QC) and sample filters, writes
the output TSV, and writes the summary/report logging in report.py.

External methods:
    - run_export()
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import file_parsing, qc, report, table_io
from .config import (
    ColumnConfig,
    ConditionalQC,
    ExportConfig,
    QCCondition,
    SetQCRule,
    load_config,
)
from .exceptions import ConfigError, InputTableError


@dataclass(frozen=True)
class _ExportPlan:
    """
    What the config asks for, worked out once against this input's header.

    Attributes:
        output_header: the output table's header row
        column_positions: each configured column paired with the header index it
          reads from.
        match_index: the columns that are used in QC mapped to to its header index.
          ConditionalQC -> contains the columns upon which the match/qc conditions apply;
          set_qc -> contains the columns that are being checked
        config_has_columns: True when the config lists `columns`, so each row is
          expanded into QCInputs. False when there is no config or only `set_qc`.
    """

    output_header: list[str]
    column_positions: list[tuple[ColumnConfig, int]]
    match_index: dict[str, int]
    config_has_columns: bool


@dataclass
class _InputInformation:
    """
    Information from reading the input file, and what got written while reading it.

    Rows are written straight out to a staging file as they're read, so nothing
    accumulates here except the sample names -- and those are only needed to
    report the collateral failures when a set_qc rule fails the whole run.

    Attributes:
        sample_names: every sample surviving the sample-list filter, in input order.
        set_qc_matched: each set_qc rule name mapped to the samples it matched.
        set_qc_failures: each set_qc rule name mapped to the failures it collected.
        row_failures: per-row QC failures. Discarded when a set_qc rule fails the
          run, since the run-level verdict replaces them.
        total_rows: the number of rows in the input.
        candidate_rows: the number of rows surviving the sample-list filter, before QC.
        seen_samples: a set of every sample name encountered
        written_rows: how many rows were written to the staging file.
        set_qc_failed: True once any set_qc check has failed. Every later row then
          skips its own expansion and QC, because the run is already doomed.
    """

    sample_names: list[str] = field(default_factory=list)
    set_qc_matched: dict[str, list[str]] = field(default_factory=dict)
    set_qc_failures: dict[str, list[qc.QCFailure]] = field(default_factory=dict)
    row_failures: list[qc.QCFailure] = field(default_factory=list)
    total_rows: int = 0
    candidate_rows: int = 0
    seen_samples: set[str] = field(default_factory=set)
    written_rows: int = 0
    set_qc_failed: bool = False


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


def _build_column_name_index(header: list[str]) -> dict[str, list[int]]:
    """
    Maps each header name to a list of every index it appears at

    Args:
        header: the input file's header row, in file order.

    Returns:
        A dict of header name to every index that name occupies
    """
    index: dict[str, list[int]] = {}
    for i, name in enumerate(header):  # returns (0, list[0]), (1, list[1])
        # if the column isn't in the dictionary, set it w/ a list value and add index to list
        index.setdefault(name, []).append(i)
    return index


def _validate_header_reference(
    column_name: str,
    column_name_indices: dict[str, list[int]],
    input_path: Path,
    description: str,
) -> None:
    """
    Checks each column name from the config against the input file header to confirm
    it exists or isn't duplicated

    Args:
        column_name: the column name the config referenced.
        column_name_indices: the input header's name-to-indices index.
        input_path: the input table's path, used in the error message.
        description: how the config referenced this column, used in the error message.

    Raises:
        InputTableError: if the column is missing from the input header, or appears
          more than once.
    """
    indices = column_name_indices.get(column_name)
    if not indices:
        raise InputTableError(
            f"{input_path}: {description} {column_name!r}, which is not in the input header"
        )
    if len(indices) > 1:
        raise InputTableError(
            f"{input_path}: {description} {column_name!r}, which appears {len(indices)} times in the input header (ambiguous)"
        )


def _validate_columns_exist(
    columns: list[ColumnConfig],
    column_name_indices: dict[str, list[int]],
    input_path: Path,
) -> None:
    """
    Checks before any output is written if the config references a column that doesn't
    exist or exists more than once.

    Args:
        columns: every column the config configures, including their qc matches
          and file_parsing outputs.
        column_name_indices: the input header's name-to-indices index.
        input_path: the input table's path, used in the error message.

    Raises:
        InputTableError: if any referenced column is missing from the input header,
          or appears more than once.
    """
    for column in columns:
        _validate_header_reference(
            column.input_column,
            column_name_indices,
            input_path,
            "config references column",
        )
        if isinstance(column.qc, ConditionalQC):
            _validate_header_reference(
                column.qc.match_column,
                column_name_indices,
                input_path,
                f"column {column.input_column!r} has qc matching on column",
            )
        if column.file_parsing is not None:
            for output in column.file_parsing:
                if isinstance(output.qc, ConditionalQC):
                    _validate_header_reference(
                        output.qc.match_column,
                        column_name_indices,
                        input_path,
                        f"file_parsing output {output.output_column!r} on column {column.input_column!r} has qc matching on column",
                    )


def _validate_set_qc_check_columns(
    set_qc: list[SetQCRule], column_name_indices: dict[str, list[int]], input_path: Path
) -> None:
    """
    Checks before any output is written if a set_qc rule references a column that
    doesn't exist or exists more than once.

    Args:
        set_qc: every set_qc rule in the config.
        column_name_indices: the input header's name-to-indices index.
        input_path: the input table's path, used in the error message.

    Raises:
        InputTableError: if a rule reads a column that is missing from the input
          header, or appears more than once.
    """
    for rule in set_qc:
        for check in rule.checks:
            _validate_header_reference(
                check.input_column,
                column_name_indices,
                input_path,
                f"set_qc rule {rule.rule_name!r} reads column",
            )


def _validate_file_parsing_allowed(
    columns: list[ColumnConfig], allow_file_parsing: bool
) -> None:
    """
    Quits if the config uses file_parsing but the user didn't opt in.

    Args:
        columns: every column the config configures.
        allow_file_parsing: whether --allow-file-parsing was given.

    Raises:
        ConfigError: if any column configures file_parsing without the opt-in.
    """
    if allow_file_parsing:
        return
    names = [c.input_column for c in columns if c.file_parsing is not None]
    if names:
        raise ConfigError(
            f"config uses file_parsing on column(s) {names}, but --allow-file-parsing was not given"
        )


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


def _select_qc_conditions(
    qc_value: list[QCCondition] | ConditionalQC,
    match_values: dict[str, str],
    column: str,
) -> list[QCCondition] | qc.NoMatchingRule:
    """
    Picks which QC conditions apply to one output for one row

    Plain list QC is applied to all rows. If conditional QC is specified, the match
    must be met or the sample will fail QC

    Args:
        qc_value: this output's configured QC -- a plain condition list, or a
          conditional.
        match_values: each conditional qc match option available (if ConditionalQC)
        column: how to name this output in a no-matching-rule reason.

    Returns:
        The conditions to check this row against, or qc.NoMatchingRule when a
        conditional `qc` matched no rule and has no default.
    """
    if isinstance(qc_value, list):
        # it's a list[QCCondition] already, return it
        return qc_value

    # we have a ConditonalQC object
    match_value = match_values[qc_value.match_column]
    conditions = qc_value.cases.get(match_value)
    if conditions is not None:
        return conditions
    if qc_value.default is not None:
        return qc_value.default

    # ------------------------------------------------------------------
    # TREAT UNMATCHED CONDITIONALS AS QC FAIL
    return qc.NoMatchingRule(
        f"no qc rule matches {qc_value.match_column}={match_value!r} for {column}, and no default is configured"
    )
    # ------------------------------------------------------------------
    # ---                             OR                             ---
    # ------------------------------------------------------------------
    # TREAT UNMATCHED CONDITIONALS AS QC PASS
    # return []
    # ------------------------------------------------------------------


def _build_qc_inputs(
    column: ColumnConfig, raw_cell: str, match_values: dict[str, str]
) -> list[qc.QCInput]:
    """
    Builds one QCInput per output that this column contributes to this row.

    A file_parsing column's raw cell is a path. QC and the output both see the parsed
    result(s) instead of the path.

    Args:
        column: the column's config, including any output_column, qc, and file_parsing.
        raw_cell: this row's value in that column.
        match_values: each conditional-qc match column mapped to this row's value.

    Returns:
        One QCInput per output name this column generates, in output order.
    """
    # if file parsing is present, the QC is nested inside
    if column.file_parsing is not None:
        command_outputs = file_parsing.run(column.file_parsing, raw_cell)
        qc_inputs = []
        # for each instruction and result, perform any applicable qc
        for parsing_instructions, command_result in zip(
            column.file_parsing, command_outputs
        ):
            conditions = _select_qc_conditions(
                parsing_instructions.qc,
                match_values,
                f"file_parsing output {parsing_instructions.output_column!r} (column {column.input_column!r})",
            )
            qc_inputs.append(
                qc.QCInput(
                    column.input_column,
                    parsing_instructions.output_column,
                    command_result,
                    conditions,
                )
            )
        return qc_inputs

    conditions = _select_qc_conditions(
        column.qc, match_values, f"column {column.input_column!r}"
    )
    return [
        qc.QCInput(column.input_column, column.output_column_name, raw_cell, conditions)
    ]


def _collect_conditional_qc_match_columns(columns: list[ColumnConfig]) -> set[str]:
    """
    Every column name referenced as a conditional-qc match

    Args:
        columns: every column the config configures, including their file_parsing
          outputs. An empty list if not conditional QC.

    Returns:
        The set of match column names, which must be read from each row even when they
        aren't output themselves.
    """
    matches: set[str] = set()
    for c in columns:
        if isinstance(c.qc, ConditionalQC):
            matches.add(c.qc.match_column)
        if c.file_parsing is not None:
            for output in c.file_parsing:
                if isinstance(output.qc, ConditionalQC):
                    matches.add(output.qc.match_column)
    return matches


def _plan_export(
    config: ExportConfig | None,
    header: list[str],
    column_name_indices: dict[str, list[int]],
    input_path: Path,
    allow_file_parsing: bool,
) -> _ExportPlan:
    """
    Validates the config against this input's header, then works out what has to
    be read from each row and what the output header looks like.

    Args:
        config: the loaded config, or None to pass every column through unchanged.
        header: the input file's header row, in file order.
        column_name_indices: the input header's name-to-indices index.
        input_path: the input table's path, used in the error messages.
        allow_file_parsing: whether --allow-file-parsing was given.

    Returns:
        The plan every later step reads from.

    Raises:
        InputTableError: if the config or a set_qc rule references a column that is
          missing or duplicated in the header.
        ConfigError: if the config uses file_parsing without `allow_file_parsing`.
    """
    # no config at all: every input column passes through untouched
    if config is None:
        return _ExportPlan(
            output_header=header,
            column_positions=[],
            match_index={},
            config_has_columns=False,
        )

    _validate_columns_exist(config.columns or [], column_name_indices, input_path)
    _validate_file_parsing_allowed(config.columns or [], allow_file_parsing)
    _validate_set_qc_check_columns(config.set_qc, column_name_indices, input_path)

    if config.columns is not None:
        # ------------------------------------------------------------------
        # OUTPUT COLUMNS ARE ORDERED LIKE THE CONFIG
        ordered_columns = config.columns
        # ------------------------------------------------------------------
        # ---                             OR                             ---
        # ------------------------------------------------------------------
        # OUTPUT COLUMNS ARE KEPT IN THE SAME ORDER AS THE INPUT
        # ordered_columns = sorted(config.columns, key=lambda c: column_name_indices[c.input_column][0])
        # ------------------------------------------------------------------

        # one column can contribute several output names (file_parsing), so the
        # header is flattened out of every column's generated names
        output_header = [
            name for c in ordered_columns for name in c.generated_output_column_names
        ]
        column_positions = [
            (c, column_name_indices[c.input_column][0]) for c in ordered_columns
        ]
    else:
        # no columns (only happens if there's only set_qc)
        # all input column passes through unchanged, same as no config at all
        output_header = header
        column_positions = []

    # Cells that have to be read whether or not they end up in the output:
    # conditional qc's match columns, plus every column a set_qc check reads.
    # Neither has to appear in the `columns` allow-list.
    match_columns = _collect_conditional_qc_match_columns(config.columns or []) | {
        check.input_column for rule in config.set_qc for check in rule.checks
    }

    return _ExportPlan(
        output_header=output_header,
        column_positions=column_positions,
        match_index={name: column_name_indices[name][0] for name in match_columns},
        config_has_columns=config.columns is not None,
    )


def _scan_rows(
    input_path: Path,
    input_delimiter: str,
    plan: _ExportPlan,
    set_qc_rules: list[SetQCRule],
    requested_samples: set[str] | None,
    writer,
) -> _InputInformation:
    """
    Reads every input row, QCs it, and writes the passing ones straight to `writer`.

    A row's own QC verdict never depends on a later row, so it's decided here and
    the row is written immediately -- nothing is held in memory. Only one decision
    has to wait for the end of the file: whether a set_qc rule failed, which fails
    the *whole* run. That's why `writer` must point at a staging file that only
    gets committed once _apply_qc() has had its say.

    Once any set_qc check fails the run is already doomed, so every later row
    skips its own expansion and QC entirely. That matters most for `file_parsing`,
    where expanding a row means a subprocess (and possibly a download) per cell.

    Args:
        input_path: the input table to read.
        input_delimiter: the input table's delimiter.
        plan: what to read from each row, and whether rows need expanding.
        set_qc_rules: the config's run-level rules, or an empty list when there
          are none (or no config).
        requested_samples: the sample names to keep, or None to keep all.
        writer: an open row writer from table_io.open_row_writer().

    Returns:
        The sample names, the set_qc bookkeeping, and what was written.
    """
    scan = _InputInformation(
        set_qc_matched={rule.rule_name: [] for rule in set_qc_rules},
        set_qc_failures={rule.rule_name: [] for rule in set_qc_rules},
    )

    for row in table_io.iter_rows(input_path, input_delimiter):
        # sample name should always be in the first column
        sample = row[0] if row else ""
        scan.total_rows += 1

        if requested_samples is not None:
            scan.seen_samples.add(sample)
            if sample not in requested_samples:
                # skip samples not specified
                continue
        scan.candidate_rows += 1
        # kept even when the run is doomed: a failed set_qc rule reports one
        # collateral failure per sample, so every name is needed
        scan.sample_names.append(sample)

        # This row's value for every column in match_index. Read two different
        # ways below: conditional qc looks a value up to pick which conditions
        # apply, while a set_qc check treats the value as the thing being checked.
        match_values = {name: row[idx] for name, idx in plan.match_index.items()}

        # Always evaluated, even once doomed: every rule still needs its full
        # matched-sample list for _check_set_qc_matched(), and every failing
        # sample still earns its own full-detail report row.
        for rule in set_qc_rules:
            if not rule.match_samples.applies_to(sample):
                continue
            scan.set_qc_matched[rule.rule_name].append(sample)
            rule_qc_inputs = [
                qc.QCInput(
                    check.input_column,
                    # a set_qc check is only ever read, never output, so the
                    # input and output names are the same
                    check.input_column,
                    match_values[check.input_column],
                    check.qc,
                )
                for check in rule.checks
            ]
            rule_outcome = qc.evaluate_row(rule_qc_inputs, sample)
            if not rule_outcome.passed:
                scan.set_qc_failures[rule.rule_name].extend(rule_outcome.failures)
                scan.set_qc_failed = True

        if scan.set_qc_failed:
            # the run can no longer produce output, so skip the expensive part
            continue

        if not plan.config_has_columns:
            # pass-through: no QC configured, so the raw cells are the output row
            writer.writerow(row)
            scan.written_rows += 1
            continue

        qc_inputs: list[qc.QCInput] = []
        for column, idx in plan.column_positions:
            qc_inputs.extend(_build_qc_inputs(column, row[idx], match_values))
        row_outcome = qc.evaluate_row(qc_inputs, sample)
        scan.row_failures.extend(row_outcome.failures)
        if not row_outcome.passed:
            # row failed qc, keep it out of the output
            continue
        writer.writerow([qc_input.value for qc_input in qc_inputs])
        scan.written_rows += 1

    return scan


def _check_set_qc_matched(
    set_qc_rules: list[SetQCRule], scan: _InputInformation, input_path: Path
) -> None:
    """
    Confirms every set_qc rule actually matched the sample(s) it claimed.

    A rule that matches nothing is a LIMSport error, not a QC failure: there's no
    sample to attach a failure to, so the run aborts before any output is written.

    Args:
        set_qc_rules: the config's run-level rules, or an empty list.
        scan: the finished scan, read for its set_qc_matched bookkeeping.
        input_path: the input table's path, used in the error messages.

    Raises:
        InputTableError: if a rule matched no sample at all, or if an exact-name
          matcher named a sample this run never produced.
    """
    for rule in set_qc_rules:
        matched = scan.set_qc_matched[rule.rule_name]
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


def _whole_run_failures(
    run_failed_rules: list[SetQCRule], scan: _InputInformation
) -> list[qc.QCFailure]:
    """
    Builds the failure list for a run that a set_qc rule failed.

    Every sample fails, not only the offending one: the sample(s) that violated a
    rule keep their real, full-detail failures, and every other sample in the run
    gets one collateral row naming the rule(s) that failed the run.

    Args:
        run_failed_rules: the set_qc rules that collected at least one failure.
        scan: the finished scan, read for its failures and sample names.

    Returns:
        Every failure to log and report, the offending samples' first.
    """
    failures: list[qc.QCFailure] = []
    offending_samples: set[str] = set()
    failing_rule_names = [rule.rule_name for rule in run_failed_rules]

    for rule in run_failed_rules:
        rule_failures = scan.set_qc_failures[rule.rule_name]
        failures.extend(rule_failures)
        offending_samples.update(f.sample for f in rule_failures)

    for sample in scan.sample_names:
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
                reason=f"run failed QC due to set_qc rule(s): {failing_rule_names}",
            )
        )
    return failures


def _apply_qc(set_qc_rules: list[SetQCRule], scan: _InputInformation) -> _QCOutcome:
    """
    Settles the run's verdict, now that set_qc's whole-run result is known.

    The rows themselves were already written during the scan, so there is nothing
    left to build here -- only to decide whether what was written counts, and
    which failures to report.

    Args:
        set_qc_rules: the config's run-level rules, or an empty list.
        scan: the finished scan.

    Returns:
        The failures to report, and how many rows count as passing.
    """
    run_failed_rules = [
        rule for rule in set_qc_rules if scan.set_qc_failures[rule.rule_name]
    ]
    if run_failed_rules:
        # A set_qc rule failed, so the entire run fails QC -- not just the
        # sample(s) that violated it. Whatever rows reached the staging file are
        # discarded, and the per-row failures go with them: the run-level verdict
        # replaces them.
        return _QCOutcome(
            failures=_whole_run_failures(run_failed_rules, scan), run_failed=True
        )

    return _QCOutcome(failures=scan.row_failures, passed_rows=scan.written_rows)


def _report_results(
    config: ExportConfig | None,
    scan: _InputInformation,
    outcome: _QCOutcome,
    qc_report_path: Path | None,
) -> None:
    """
    Logs the run summary, and writes the QC report when one was asked for.

    Args:
        config: the loaded config, or None if there wasn't one.
        scan: the finished scan, read for its row counts.
        outcome: the QC result, read for its failures and pass count.
        qc_report_path: where to write the QC failure report, or None to skip it.
    """
    if config is None:
        # no config means no QC ever ran, so there's nothing to report
        report.log_no_qc_summary(scan.candidate_rows, scan.total_rows)
        return

    report.log_summary(passed=outcome.passed_rows, total=scan.candidate_rows)
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

    Raises:
        InputTableError: if the config or a set_qc rule references a column that is
          missing or duplicated in the header, or if a set_qc rule matches no sample in
          this run.
        ConfigError: if the config is invalid, or uses file_parsing without
          `allow_file_parsing`.
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
    column_name_indices = _build_column_name_index(header)
    config: ExportConfig | None = (
        load_config(config_path) if config_path is not None else None
    )
    plan = _plan_export(
        config, header, column_name_indices, input_path, allow_file_parsing
    )
    set_qc_rules = config.set_qc if config is not None else []
    requested_samples = (
        _load_sample_list(samples_path) if samples_path is not None else None
    )

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
            staging_path, plan.output_header, output_delimiter
        ) as writer:
            scan = _scan_rows(
                input_path,
                input_delimiter,
                plan,
                set_qc_rules,
                requested_samples,
                writer,
            )

        if requested_samples is not None:
            # names in the sample list that never showed up in the input are
            # warnings. Logged before the check below, which can abort the run.
            unknown = requested_samples - scan.seen_samples
            if unknown:
                report.log_unknown_samples(unknown)

        _check_set_qc_matched(set_qc_rules, scan, input_path)

        # set_qc's verdict is known, so what was staged can finally be judged
        outcome = _apply_qc(set_qc_rules, scan)

        if outcome.run_failed:
            # a set_qc rule failed, so nothing that was staged counts
            table_io.write_tsv(
                output_path, plan.output_header, [], delimiter=output_delimiter
            )
        else:
            os.replace(staging_path, output_path)
    finally:
        # a no-op after a successful replace; cleans up on every other path
        staging_path.unlink(missing_ok=True)

    _report_results(config, scan, outcome, qc_report_path)
