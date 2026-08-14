"""Orchestrates the export: reads the input TSV, applies the optional
config (column allow-list, rename, QC) and optional sample filter, writes
the output TSV, and wires up the summary/report logging in report.py.
"""

from pathlib import Path

from . import file_parsing, table_io, qc, report
from .config import ColumnConfig, ExportConfig, QCFailure, load_config
from .exceptions import ConfigError, InputTableError


def _build_name_index(header: list[str]) -> dict[str, list[int]]:
    """Map each header name to every index it appears at. A name that's
    duplicated in the input TSV shows up as a list with len() > 1"""
    index: dict[str, list[int]] = {}
    for i, name in enumerate(header):
        index.setdefault(name, []).append(i)
    return index


def _validate_columns_exist(
    columns: list[ColumnConfig], name_to_indices: dict[str, list[int]], input_path: Path
) -> None:
    """Check before any output is written if the config references a column
    that either doesn't exist in the input header, or exists more than once"""
    for column in columns:
        indices = name_to_indices.get(column.name)
        if not indices:
            raise InputTableError(
                f"{input_path}: config references column {column.name!r}, which is not in the input header"
            )
        if len(indices) > 1:
            raise InputTableError(
                f"{input_path}: config references column {column.name!r}, "
                f"which appears {len(indices)} times in the input header (ambiguous)"
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
    names = {line.strip() for line in path.read_text().splitlines()}
    names.discard("") # remove empty items from set
    return names


def _resolve_column(column: ColumnConfig, raw_cell: str) -> list[qc.ResolvedField]:
    """Resolve one column's raw cell into its output field(s).

    A plain column resolves to itself unchanged. A file_parsing column's
    raw cell is a path, not the real value(s) -- its command(s) run
    first, so QC and the output both see the parsed result(s) instead of
    the path, resolving to one field per configured output.
    """
    if column.file_parsing is None:
        return [qc.ResolvedField(column.name, column.output_name, raw_cell, column.qc)]
    values = file_parsing.run(column.file_parsing, raw_cell)
    return [
        qc.ResolvedField(column.name, output.name, value, output.qc)
        for output, value in zip(column.file_parsing, values)
    ]


def run_export(
    input_path: Path,
    config_path: Path | None,
    samples_path: Path | None,
    output_path: Path,
    qc_report_path: Path | None,
    output_delimiter: str | None = None,
    allow_file_parsing: bool = False,
) -> None:
    # Detect once and thread it through every read below, so nothing
    # re-sniffs and risks disagreeing with itself.
    input_delimiter = table_io.detect_delimiter(input_path)
    # if no --delimiter given, use whatever delimiter is used in the input
    effective_delimiter = output_delimiter or input_delimiter

    if config_path is None and samples_path is None and effective_delimiter == input_delimiter:
        # Nothing to filter, transform, or re-delimit, so copy the file
        table_io.copy_file_verbatim(input_path, output_path)
        total = table_io.count_rows(input_path, input_delimiter)
        report.log_no_qc_summary(total, total)
        return

    header = table_io.read_header(input_path, input_delimiter)
    name_to_indices = _build_name_index(header)

    config: ExportConfig | None = load_config(config_path) if config_path is not None else None
    if config is not None:
        _validate_columns_exist(config.columns, name_to_indices, input_path)
        _validate_file_parsing_allowed(config.columns, allow_file_parsing)
        output_header = [name for c in config.columns for name in c.output_names]
        # make column order match header
        column_index = {c.name: name_to_indices[c.name][0] for c in config.columns}
        column_by_name = {c.name: c for c in config.columns}
    else:
        # no config: pass every column through unchanged, in its original order.
        output_header = header
        column_index = {}
        column_by_name = {}

    requested_samples = _load_sample_list(samples_path) if samples_path is not None else None
    seen_samples: set[str] = set()

    output_rows: list[list[str]] = []
    all_failures: list[QCFailure] = []
    total_rows = 0  # every row seen, regardless of the sample filter
    candidates = 0  # rows in scope after sample-list filtering, before QC
    passed_count = 0

    for row in table_io.iter_rows(input_path, input_delimiter):
        # sample name should always be in the first column
        sample = row[0] if row else ""
        seen_samples.add(sample)
        total_rows += 1

        if requested_samples is not None and sample not in requested_samples:
            # skip samples not specified
            continue
        candidates += 1

        if config is not None:
            # A file_parsing column can resolve to several output fields
            # from one input column, so build a flat list instead of a
            # one-value-per-column dict.
            fields: list[qc.ResolvedField] = []
            for name, idx in column_index.items():
                fields.extend(_resolve_column(column_by_name[name], row[idx]))

            outcome = qc.evaluate_row(fields, sample)
            all_failures.extend(outcome.failures)
            if not outcome.passed:
                # row failed qc, do not add to output, skip to next item in loop
                continue
            output_rows.append([field.value for field in fields])
        else:
            output_rows.append(row)

        passed_count += 1

    if requested_samples is not None:
        # names in the sample list that never showed up in the input are warnings
        unknown = requested_samples - seen_samples
        if unknown:
            report.log_unknown_samples(unknown)

    table_io.write_tsv(output_path, output_header, output_rows, delimiter=effective_delimiter)

    if config is not None:
        report.log_summary(passed=passed_count, total=candidates)
        report.log_qc_failures(all_failures)
        if qc_report_path is not None:
            report.write_qc_report(qc_report_path, all_failures)
    else:
        # no config means no QC ever ran, so there's nothing to report
        report.log_no_qc_summary(candidates, total_rows)
