"""Orchestrates the export: reads the input TSV, applies the config
(column allow-list, rename, QC) and sample filters, writes
the output TSV, and writes the summary/report logging in report.py.
"""

from pathlib import Path

from . import file_parsing, qc, report, table_io
from .config import (
    ColumnConfig,
    ExportConfig,
    QCByRule,
    QCCondition,
    QCFailure,
    SetQCRule,
    load_config,
)
from .exceptions import ConfigError, InputTableError


def _build_column_name_index(header: list[str]) -> dict[str, list[int]]:
    """Map each header name to a list of every index it appears at"""
    index: dict[str, list[int]] = {}
    for i, name in enumerate(header):  # returns (0, list[0]), (1, list[1])
        # if the column isn't in the dictionary, set it w/ a list value and add index to list
        index.setdefault(name, []).append(i)
    return index


def _validate_header_reference(
    name: str,
    column_name_indices: dict[str, list[int]],
    input_path: Path,
    description: str,
) -> None:
    """Check each column name from the config against the input file header to confirm
    it exists or isn't duplicated"""
    indices = column_name_indices.get(name)
    if not indices:
        raise InputTableError(
            f"{input_path}: {description} {name!r}, which is not in the input header"
        )
    if len(indices) > 1:
        raise InputTableError(
            f"{input_path}: {description} {name!r}, "
            f"which appears {len(indices)} times in the input header (ambiguous)"
        )


def _validate_columns_exist(
    columns: list[ColumnConfig],
    column_name_indices: dict[str, list[int]],
    input_path: Path,
) -> None:
    """Check before any output is written if the config references a
    column that doesn't exist or exists more than once."""
    for column in columns:
        _validate_header_reference(
            column.name, column_name_indices, input_path, "config references column"
        )
        if isinstance(column.qc, QCByRule):
            _validate_header_reference(
                column.qc.match,
                column_name_indices,
                input_path,
                f"column {column.name!r} has qc matching on column",
            )
        if column.file_parsing is not None:
            for output in column.file_parsing:
                if isinstance(output.qc, QCByRule):
                    _validate_header_reference(
                        output.qc.match,
                        column_name_indices,
                        input_path,
                        f"file_parsing output {output.name!r} on column {column.name!r} has qc matching on column",
                    )


def _validate_set_qc_columns(
    set_qc: list[SetQCRule], column_name_indices: dict[str, list[int]], input_path: Path
) -> None:
    """Check before any output is written if a set_qc rule references a
    column that doesn't exist or exists more than once."""
    for rule in set_qc:
        for check in rule.columns:
            _validate_header_reference(
                check.column,
                column_name_indices,
                input_path,
                f"set_qc rule {rule.name!r} reads column",
            )


def _validate_file_parsing_allowed(
    columns: list[ColumnConfig], allow_file_parsing: bool
) -> None:
    """Quit if the config uses file_parsing but the user didn't opt in."""
    if allow_file_parsing:
        return
    names = [c.name for c in columns if c.file_parsing is not None]
    if names:
        raise ConfigError(
            f"config uses file_parsing on column(s) {names}, but --allow-file-parsing was not given"
        )


def _load_sample_list(path: Path) -> set[str]:
    """Return list of samples in the input TSV"""
    names = {line.strip() for line in path.read_text().splitlines()}
    names.discard("")  # remove blank lines
    return names


def _resolve_qc(
    qc_value: list[QCCondition] | QCByRule, match_values: dict[str, str], column: str
) -> tuple[list[QCCondition], str | None]:
    """Pick which QC conditions apply to one output for one row

    Returns (conditions, unmatched_reason). Plain list QC is applied to all rows.
    If conditional QC is specified, the match must be met or the sample will fail QC
    """
    if isinstance(qc_value, list):
        return qc_value, None

    match_value = match_values[qc_value.match]
    conditions = qc_value.rules.get(match_value)
    if conditions is not None:
        return conditions, None
    if qc_value.default is not None:
        return qc_value.default, None

    # ------------------------------------------------------------------
    # TREAT UNMATCHED CONDITIONALS AS QC FAIL
    reason = (
        f"no qc rule matches {qc_value.match}={match_value!r} for "
        f"{column}, and no default is configured"
    )
    return [], reason
    # ------------------------------------------------------------------
    # ---                             OR                             ---
    # ------------------------------------------------------------------
    # TREAT UNMATCHED CONDITIONALS AS QC PASS
    # return [], None
    # ------------------------------------------------------------------


def _resolve_column(
    column: ColumnConfig, raw_cell: str, match_values: dict[str, str]
) -> list[qc.ResolvedField]:
    """Resolve one column's raw cell into its output field(s).

    A file_parsing column's raw cell is a path. QC and the output both see
    the parsed result(s) instead of the path.
    """
    if column.file_parsing is not None:
        values = file_parsing.run(column.file_parsing, raw_cell)
        fields = []
        for output, value in zip(column.file_parsing, values):
            conditions, unmatched_reason = _resolve_qc(
                output.qc,
                match_values,
                f"file_parsing output {output.name!r} (column {column.name!r})",
            )
            fields.append(
                qc.ResolvedField(
                    column.name, output.name, value, conditions, unmatched_reason
                )
            )
        return fields

    conditions, unmatched_reason = _resolve_qc(
        column.qc, match_values, f"column {column.name!r}"
    )
    return [
        qc.ResolvedField(
            column.name, column.output_name, raw_cell, conditions, unmatched_reason
        )
    ]


def _collect_match_columns(columns: list[ColumnConfig]) -> set[str]:
    """Every column name referenced as a conditional-qc match"""
    matches: set[str] = set()
    for c in columns:
        if isinstance(c.qc, QCByRule):
            matches.add(c.qc.match)
        if c.file_parsing is not None:
            for output in c.file_parsing:
                if isinstance(output.qc, QCByRule):
                    matches.add(output.qc.match)
    return matches


def run_export(
    input_path: Path,
    config_path: Path | None,
    samples_path: Path | None,
    output_path: Path,
    qc_report_path: Path | None,
    output_delimiter: str = "\t",
    allow_file_parsing: bool = False,
) -> None:
    # auto-detect delimiter
    input_delimiter = table_io.detect_delimiter(input_path)

    if (
        config_path is None
        and samples_path is None
        and output_delimiter == input_delimiter
    ):
        # why are you even running this program??
        report.log_nothing_to_do()
        return

    header = table_io.get_input_header(input_path, input_delimiter)
    column_name_indices = _build_column_name_index(header)

    config: ExportConfig | None = (
        load_config(config_path) if config_path is not None else None
    )
    if config is not None:
        _validate_columns_exist(config.columns or [], column_name_indices, input_path)
        _validate_file_parsing_allowed(config.columns or [], allow_file_parsing)
        _validate_set_qc_columns(config.set_qc, column_name_indices, input_path)

        if config.columns is not None:
            # ------------------------------------------------------------------
            # OUTPUT COLUMNS ARE ORDERED LIKE THE CONFIG
            ordered_columns = config.columns
            # ------------------------------------------------------------------
            # ---                             OR                             ---
            # ------------------------------------------------------------------
            # OUTPUT COLUMNS ARE KEPT IN THE SAME ORDER AS THE INPUT
            # ordered_columns = sorted(config.columns, key=lambda c: column_name_indices[c.name][0])
            # ------------------------------------------------------------------

            output_header = [
                name for c in ordered_columns for name in c.generated_output_names
            ]
            resolved_columns = [
                (c, column_name_indices[c.name][0]) for c in ordered_columns
            ]
        else:
            # columns omitted: this config exists only for set_qc, so every
            # input column passes through unchanged, same as no config at all
            output_header = header
            resolved_columns = []

        match_columns = _collect_match_columns(config.columns or []) | {
            check.column for rule in config.set_qc for check in rule.columns
        }
        match_index = {name: column_name_indices[name][0] for name in match_columns}
    else:
        # no config: pass every column through unchanged
        output_header = header
        resolved_columns = []
        match_index = {}

    requested_samples = (
        _load_sample_list(samples_path) if samples_path is not None else None
    )
    seen_samples: set[str] = set()

    # Buffered per-row state, not yet decided as pass/fail -- deciding that
    # happens only after the whole input is read (see below), because a
    # set_qc rule's failure can fail every row in the run, not just the
    # sample(s) it matched. When there's no config, a row is just its raw
    # cells; with one, it's the row's resolved (column, value, qc) fields.
    buffered: list[tuple[str, list]] = []
    set_qc_matched: dict[str, list[str]] = {
        rule.name: [] for rule in (config.set_qc if config else [])
    }
    set_qc_failures: dict[str, list[QCFailure]] = {
        rule.name: [] for rule in (config.set_qc if config else [])
    }

    total_rows = 0
    candidate_rows = 0  # rows after sample-list filtering, before QC

    for row in table_io.iter_rows(input_path, input_delimiter):
        # sample name should always be in the first column
        sample = row[0] if row else ""
        total_rows += 1

        if requested_samples is not None:
            seen_samples.add(sample)
            if sample not in requested_samples:
                # skip samples not specified
                continue
        candidate_rows += 1

        if config is not None:
            # extract the match value(s)
            match_values = {name: row[idx] for name, idx in match_index.items()}

            for rule in config.set_qc:
                if rule.match.applies_to(sample):
                    set_qc_matched[rule.name].append(sample)
                    rule_fields = [
                        qc.ResolvedField(
                            check.column,
                            check.column,
                            match_values[check.column],
                            check.qc,
                        )
                        for check in rule.columns
                    ]
                    rule_outcome = qc.evaluate_row(rule_fields, sample)
                    if not rule_outcome.passed:
                        set_qc_failures[rule.name].extend(rule_outcome.failures)

            if config.columns is not None:
                fields: list[qc.ResolvedField] = []
                for column, idx in resolved_columns:
                    fields.extend(_resolve_column(column, row[idx], match_values))
                buffered.append((sample, fields))
            else:
                # columns omitted: nothing to resolve/check per-row
                buffered.append((sample, row))
        else:
            buffered.append((sample, row))

    if requested_samples is not None:
        # names in the sample list that never showed up in the input are warnings
        unknown = requested_samples - seen_samples
        if unknown:
            report.log_unknown_samples(unknown)

    if config is not None:
        for rule in config.set_qc:
            if rule.match.samples is not None:
                # An exact-name matcher names specific samples the caller
                # expects to exist (e.g. known controls) -- every one of
                # them must actually show up, not just at least one, or a
                # typo'd/missing name would be silently never checked.
                missing = [
                    s for s in rule.match.samples if s not in set_qc_matched[rule.name]
                ]
                if missing:
                    raise InputTableError(
                        f"{input_path}: set_qc rule {rule.name!r} match.samples includes "
                        f"sample(s) not found in this run: {missing}"
                    )
            elif not set_qc_matched[rule.name]:
                # no sample to attach a QC failure to, fail
                raise InputTableError(
                    f"{input_path}: set_qc rule {rule.name!r} matched no samples in this run"
                )

    run_failed_rules = (
        [rule for rule in config.set_qc if set_qc_failures[rule.name]]
        if config is not None
        else []
    )

    output_rows: list[list[str]] = []
    all_failures: list[QCFailure] = []
    passed_rows = 0  # rows after QC

    if run_failed_rules:
        # A set_qc rule failed: the entire run fails QC, not just the
        # sample(s) that violated it
        offending_samples: set[str] = set()
        failing_rule_names = [rule.name for rule in run_failed_rules]
        for rule in run_failed_rules:
            all_failures.extend(set_qc_failures[rule.name])
            offending_samples.update(f.sample for f in set_qc_failures[rule.name])
        for sample, _ in buffered:
            if sample in offending_samples:
                continue
            all_failures.append(
                QCFailure(
                    sample=sample,
                    column="",
                    output_column="",
                    operator=None,
                    expected=None,
                    actual=None,
                    reason=f"run failed QC due to set_qc rule(s): {failing_rule_names}",
                )
            )
        # output_rows stays empty since the whole run failed
    else:
        for sample, fields_or_row in buffered:
            if config is not None and config.columns is not None:
                outcome = qc.evaluate_row(fields_or_row, sample)
                all_failures.extend(outcome.failures)
                if not outcome.passed:
                    # row failed qc, do not add to output
                    continue
                output_rows.append([field.value for field in fields_or_row])
            else:
                output_rows.append(fields_or_row)
            passed_rows += 1

    table_io.write_tsv(
        output_path, output_header, output_rows, delimiter=output_delimiter
    )

    if config is not None:
        report.log_summary(passed=passed_rows, total=candidate_rows)
        report.log_qc_failures(all_failures)
        if qc_report_path is not None:
            report.write_qc_report(qc_report_path, all_failures)
    else:
        # no config means no QC ever ran, so there's nothing to report
        report.log_no_qc_summary(candidate_rows, total_rows)
