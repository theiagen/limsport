"""
Works out what the config asks for against one input table's header: validates every
column the config names, then builds the ExportLayout that every later step reads --
the output header, where in each input row to find each value, and whether run-level
QC is worth its own read of the input.

Nothing here reads a data row or writes anything; it all runs before the first byte
of output, so a config that can't work against this input fails before an old export
at the output path is disturbed.

Included classes:
    - ExportLayout

External methods:
    - build_layout()
    - validate_file_parsing_allowed()
"""

from dataclasses import dataclass
from pathlib import Path

from .config import ColumnConfig, ConditionalQC, ExportConfig, SetQCRule
from .exceptions import ConfigError, InputTableError


@dataclass(frozen=True)
class ExportLayout:
    """
    What the config asks for, worked out once against this input's header.

    Attributes:
        output_header: the output table's header row
        column_positions: each configured column paired with the header index it
          reads from.
        match_index: the columns that are used in QC mapped to to its header index.
          ConditionalQC-> contains the columns upon which the match/qc conditions apply;
          set_qc -> contains the columns that are being checked
        config_has_columns: True when the config lists `columns`, so each row is
          expanded into QCInputs. False when there is no config or only `set_qc`.
        set_qc_prepass: True when both file_parsing and set_qc are used in the config
    """

    output_header: list[str]
    column_positions: list[tuple[ColumnConfig, int]]
    match_index: dict[str, int]
    config_has_columns: bool
    set_qc_prepass: bool


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


def validate_file_parsing_allowed(
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


def build_layout(
    config: ExportConfig | None,
    header: list[str],
    input_path: Path,
) -> ExportLayout:
    """
    Validates the config against this input's header, then works out what has to
    be read from each row and what the output header looks like.

    Args:
        config: the loaded config, or None to pass every column through unchanged.
        header: the input file's header row, in file order.
        input_path: the input table's path, used in the error messages.

    Returns:
        The plan every later step reads from.

    Raises:
        InputTableError: if the config or a set_qc rule references a column that is
          missing or duplicated in the header.
    """
    column_name_indices = _build_column_name_index(header)

    # no config at all: every input column passes through untouched
    if config is None:
        return ExportLayout(
            output_header=header,
            column_positions=[],
            match_index={},
            config_has_columns=False,
            set_qc_prepass=False,
        )

    _validate_columns_exist(config.columns or [], column_name_indices, input_path)
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

    # A set_qc failure fails the whole run, so per-row work done before it is
    # discovered is wasted. Reading for set_qc alone first avoids that, but costs an
    # extra parse -- worth it only when expanding a row is expensive, which each
    # column reports for itself.
    expansion_is_expensive = any(c.expands_expensively for c in (config.columns or []))

    return ExportLayout(
        output_header=output_header,
        column_positions=column_positions,
        match_index={name: column_name_indices[name][0] for name in match_columns},
        config_has_columns=config.columns is not None,
        set_qc_prepass=bool(config.set_qc) and expansion_is_expensive,
    )
