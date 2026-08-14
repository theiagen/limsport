"""Orchestrates the export: reads the input TSV, applies the config
(column allow-list, rename, QC) and sample filters, writes
the output TSV, and writes the summary/report logging in report.py.
"""

from pathlib import Path

from . import file_parsing, table_io, qc, report
from .config import ColumnConfig, ExportConfig, QCByRule, QCCondition, QCFailure, load_config
from .exceptions import ConfigError, InputTableError


def _build_name_index(header: list[str]) -> dict[str, list[int]]:
    """Map each header name to every index it appears at. A name that's
    duplicated in the input TSV shows up as a list with len() > 1"""
    index: dict[str, list[int]] = {}
    for i, name in enumerate(header):
        index.setdefault(name, []).append(i)
    return index


def _validate_header_reference(
    name: str, name_to_indices: dict[str, list[int]], input_path: Path, description: str
) -> None:
    """Check each column name against the input file header"""
    indices = name_to_indices.get(name)
    if not indices:
        raise InputTableError(f"{input_path}: {description} {name!r}, which is not in the input header")
    if len(indices) > 1:
        raise InputTableError(
            f"{input_path}: {description} {name!r}, "
            f"which appears {len(indices)} times in the input header (ambiguous)"
        )


def _validate_columns_exist(
    columns: list[ColumnConfig], name_to_indices: dict[str, list[int]], input_path: Path
) -> None:
    """Check before any output is written if the config references a
    column that doesn't exist or exists more than once."""
    for column in columns:
        _validate_header_reference(column.name, name_to_indices, input_path, "config references column")
        if isinstance(column.qc, QCByRule):
            _validate_header_reference(column.qc.match, name_to_indices, input_path,
                f"column {column.name!r} has qc matching on column"
            )
        if column.file_parsing is not None:
            for output in column.file_parsing:
                if isinstance(output.qc, QCByRule):
                    _validate_header_reference(output.qc.match, name_to_indices, input_path,
                        f"file_parsing output {output.name!r} on column {column.name!r} has qc matching on column",
                    )


def _validate_file_parsing_allowed(columns: list[ColumnConfig], allow_file_parsing: bool) -> None:
    """Quit if the config uses file_parsing but the CLI didn't opt in."""
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

    A file_parsing column's raw cell is a path, not the real value(s) so
    the file parsing command is run first, so QC and the output both see
    the parsed result(s) instead of the path
    """
    if column.file_parsing is not None:
        values = file_parsing.run(column.file_parsing, raw_cell)
        fields = []
        for output, value in zip(column.file_parsing, values):
            conditions, unmatched_reason = _resolve_qc(
                output.qc, match_values, f"file_parsing output {output.name!r} (column {column.name!r})"
            )
            fields.append(qc.ResolvedField(column.name, output.name, value, conditions, unmatched_reason))
        return fields

    conditions, unmatched_reason = _resolve_qc(column.qc, match_values, f"column {column.name!r}")
    return [qc.ResolvedField(column.name, column.output_name, raw_cell, conditions, unmatched_reason)]


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
    # Detect once and thread it through every read below, so nothing
    # re-sniffs and risks disagreeing with itself.
    input_delimiter = table_io.detect_delimiter(input_path)

    if config_path is None and samples_path is None and output_delimiter == input_delimiter:
        # Nothing to filter, transform, or re-delimit
        report.log_nothing_to_do()
        return

    header = table_io.read_header(input_path, input_delimiter)
    name_to_indices = _build_name_index(header)

    config: ExportConfig | None = load_config(config_path) if config_path is not None else None
    if config is not None:
        _validate_columns_exist(config.columns, name_to_indices, input_path)
        _validate_file_parsing_allowed(config.columns, allow_file_parsing)

        # ------------------------------------------------------------------
        # OUTPUT COLUMNS ARE ORDERED LIKE THE CONFIG
        ordered_columns = config.columns
        # ------------------------------------------------------------------
        # ---                             OR                             ---
        # ------------------------------------------------------------------
        # OUTPUT COLUMNS ARE KEPT IN THE SAME ORDER AS THE INPUT
        # ordered_columns = sorted(config.columns, key=lambda c: name_to_indices[c.name][0])
        # ------------------------------------------------------------------

        output_header = [name for c in ordered_columns for name in c.output_names]
        resolved_columns = [(c, name_to_indices[c.name][0]) for c in ordered_columns]
        match_index = {name: name_to_indices[name][0] for name in _collect_match_columns(config.columns)}
    else:
        # no config: pass every column through unchanged
        output_header = header
        resolved_columns = []
        match_index = {}

    requested_samples = _load_sample_list(samples_path) if samples_path is not None else None
    seen_samples: set[str] = set()

    output_rows: list[list[str]] = []
    all_failures: list[QCFailure] = []
    total_rows = 0
    candidate_rows = 0 # rows after sample-list filtering, before QC
    passed_rows = 0 # rows after QC

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
            fields: list[qc.ResolvedField] = []
            for column, idx in resolved_columns:
                fields.extend(_resolve_column(column, row[idx], match_values))

            # perform the qc check
            outcome = qc.evaluate_row(fields, sample)
            all_failures.extend(outcome.failures)
            if not outcome.passed:
                # row failed qc, do not add to output, skip to next item in loop
                continue
            output_rows.append([field.value for field in fields])
        else:
            output_rows.append(row)

        passed_rows += 1

    if requested_samples is not None:
        # names in the sample list that never showed up in the input are warnings
        unknown = requested_samples - seen_samples
        if unknown:
            report.log_unknown_samples(unknown)

    table_io.write_tsv(output_path, output_header, output_rows, delimiter=output_delimiter)

    if config is not None:
        report.log_summary(passed=passed_rows, total=candidate_rows)
        report.log_qc_failures(all_failures)
        if qc_report_path is not None:
            report.write_qc_report(qc_report_path, all_failures)
    else:
        # no config means no QC ever ran, so there's nothing to report
        report.log_no_qc_summary(candidate_rows, total_rows)
