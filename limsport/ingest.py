"""
Reads the input table one row at a time: filters it to the requested samples, runs
run-level (set_qc) and per-row QC, and writes the rows that pass straight to the
caller's writer.

Nothing accumulates here that doesn't have to. A row's own QC verdict never depends
on a later row, so it is decided and written immediately -- see read_input() for the
one decision that can't be made until the last row is in.

Included classes:
    - IngestSummary

External methods:
    - read_input()
"""

from dataclasses import dataclass, field
from pathlib import Path

from . import file_parsing, qc, table_io
from .config import ColumnConfig, ConditionalQC, QCCondition, SetQCRule
from .exceptions import FileParsingError
from .layout import ExportLayout


@dataclass
class IngestSummary:
    """
    Information from reading the input file, and what got written while reading it.

    Attributes:
        sample_names: every sample surviving the sample-list filter. Only collected
          when there are set_qc rules, since nothing else reads it.
        seen_samples: a set of every sample name encountered
        set_qc_matched: each set_qc rule name mapped to the samples it matched.
        set_qc_failures: each set_qc rule name mapped to the failures it collected.
        set_qc_failed: True once any set_qc check has failed to prevent any further processing
        row_failures: per-row QC failures. Discarded when a set_qc rule fails the run,
          since the run-level verdict replaces them.
        last_parse_failure: the most recent file_parsing failure reason, quoted in the
          error when parsing fails too often or fails outright.
        total_rows: the number of rows in the input.
        candidate_rows: the number of rows surviving the sample-list filter, before QC.
        written_rows: how many rows were written to the staging file.
        parse_failed_rows: how many rows failed QC because file_parsing couldn't
          produce a value for them.
        expanded_rows: how many rows were expanded into QCInputs, i.e. how many
          actually attempted their file_parsing. The denominator for
          _check_file_parsing_produced_something().
    """

    sample_names: list[str] = field(default_factory=list)
    seen_samples: set[str] = field(default_factory=set)
    set_qc_matched: dict[str, list[str]] = field(default_factory=dict)
    set_qc_failures: dict[str, list[qc.QCFailure]] = field(default_factory=dict)
    set_qc_failed: bool = False
    row_failures: list[qc.QCFailure] = field(default_factory=list)
    last_parse_failure: str = ""
    total_rows: int = 0
    candidate_rows: int = 0
    written_rows: int = 0
    parse_failed_rows: int = 0
    expanded_rows: int = 0


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

    return qc.NoMatchingRule(
        f"no qc rule matches {qc_value.match_column}={match_value!r} for {column}, and no default is configured"
    )
    # return [] if unmatched conditionals should pass qc


def _build_qc_inputs(
    column: ColumnConfig, raw_cell: str, match_values: dict[str, str]
) -> list[qc.QCInput]:
    """
    Builds one QCInput per output that this column contributes to this row.

    A file_parsing column's raw cell is a path. QC and the output both see the parsed
    result(s) instead of the path. If parsing that path fails, every output it feeds
    comes back as qc.ParsingFailed, which fails the row at QC time.

    Args:
        column: the column's config, including any output_column, qc, and file_parsing.
        raw_cell: this row's value in that column.
        match_values: each conditional-qc match column mapped to this row's value.

    Returns:
        One QCInput per output name this column generates, in output order.
    """
    # if file parsing is present, the QC is nested inside
    if column.file_parsing is not None:
        try:
            command_outputs = file_parsing.run(column.file_parsing, raw_cell)
        except FileParsingError as e:
            # return as a qc fail
            return [
                qc.QCInput(
                    column.input_column,
                    output.output_column,
                    raw_cell,  # the report filename is what failed
                    qc.ParsingFailed(str(e)),
                    output=column.output,
                )
                for output in column.file_parsing
            ]
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
                    output=column.output,
                )
            )
        return qc_inputs

    # no file parsing
    conditions = _select_qc_conditions(
        column.qc, match_values, f"column {column.input_column!r}"
    )
    return [
        qc.QCInput(
            column.input_column,
            column.output_column_name,
            raw_cell,
            conditions,
            output=column.output,
        )
    ]


def read_input(
    input_path: Path,
    input_delimiter: str,
    layout: ExportLayout,
    set_qc_rules: list[SetQCRule],
    requested_samples: set[str] | None,
    writer,
    max_file_parsing_failures: int | None = None,
) -> IngestSummary:
    """
    Reads every input row, QCs it, and writes the passing ones straight to `writer`.

    When `writer=None`, this is a set_qc-only pre-pass where only set_qc is evaluated.

    Each row/QC pass is written immediately to a temp file that is finalized after all
    processing is complete with no set_qc failures.

    If a set_qc check fails, all subsequent rows skip expansion and QC entirely.

    Args:
        input_path: the input table to read.
        input_delimiter: the input table's delimiter.
        layout: what to read from each row, and whether rows need expanding.
        set_qc_rules: the config's run-level rules, or an empty list when there
          are none (or no config).
        requested_samples: the sample names to keep, or None to keep all.
        max_file_parsing_failures: how many rows may fail file_parsing before
          the run aborts, or None for no limit.
        writer: an open row writer from table_io.open_row_writer(), or None to
          evaluate set_qc only and write nothing.

    Returns:
        The sample names, the set_qc bookkeeping, and what was written.
    """
    summary = IngestSummary(
        set_qc_matched={rule.rule_name: [] for rule in set_qc_rules},
        set_qc_failures={rule.rule_name: [] for rule in set_qc_rules},
    )

    for row in table_io.iter_rows(input_path, input_delimiter):
        # sample name should always be in the first column -- potentially modify this
        # to be configurable in the future
        sample = row[0] if row else ""
        summary.total_rows += 1

        if requested_samples is not None:
            summary.seen_samples.add(sample)
            if sample not in requested_samples:
                # skip samples not specified
                continue
        summary.candidate_rows += 1
        if set_qc_rules:
            # keep a list of all outpu sample names in the file in case of a set qc fail
            summary.sample_names.append(sample)

        # This row's value for every column in match_index.
        # Conditional qc checks match_values to pick which conditions apply,
        # while a set_qc check treats the match_value as the column being checked.
        match_values = {name: row[idx] for name, idx in layout.match_index.items()}

        # Always evaluated, even once doomed: every rule still needs its full
        # matched-sample list for pipeline._check_set_qc_matched_samples(), and every failing
        # sample still earns its own full-detail report row.
        for rule in set_qc_rules:
            if not rule.match_samples.applies_to(sample):
                continue
            summary.set_qc_matched[rule.rule_name].append(sample)
            rule_qc_inputs = [
                qc.QCInput(
                    check.input_column,
                    # a set_qc check is only ever read, never output, so the
                    # input and output names are the same
                    check.input_column,
                    match_values[check.input_column],
                    check.qc,
                    output=False,
                )
                for check in rule.checks
            ]
            rule_outcome = qc.evaluate_row(rule_qc_inputs, sample)
            if not rule_outcome.passed:
                summary.set_qc_failures[rule.rule_name].extend(rule_outcome.failures)
                summary.set_qc_failed = True

        if writer is None or summary.set_qc_failed:
            # a set_qc-only pre-pass, or a run that can no longer produce output
            continue

        if not layout.config_has_columns:
            # pass-through: no QC configured, so the raw cells are the output row
            writer.writerow(row)
            summary.written_rows += 1
            continue

        # build list of qc inputs (perform file parsing as indicated)
        qc_inputs: list[qc.QCInput] = []
        for column, idx in layout.column_positions:
            qc_inputs.extend(_build_qc_inputs(column, row[idx], match_values))
        summary.expanded_rows += 1

        # were there any file parsing errors when qc inputs were build? too many might
        # indicate that bad command(s) were provided
        parse_failure = next(
            (
                item.qc.reason
                for item in qc_inputs
                if isinstance(item.qc, qc.ParsingFailed)
            ),
            None,
        )
        if parse_failure is not None:
            summary.parse_failed_rows += 1
            summary.last_parse_failure = parse_failure
            if (
                max_file_parsing_failures is not None
                and summary.parse_failed_rows > max_file_parsing_failures
            ):
                # too many files are failing for this, quit
                raise FileParsingError(
                    f"{input_path}: file_parsing failed on {summary.parse_failed_rows} "
                    f"row(s), over the --max-file-parsing-failures limit of "
                    f"{max_file_parsing_failures}; last failure: {parse_failure}"
                )

        # perform comparisons
        row_outcome = qc.evaluate_row(qc_inputs, sample)
        summary.row_failures.extend(row_outcome.failures)
        if not row_outcome.passed:
            # row failed qc, keep it out of the output
            continue
        # write output to temp file
        writer.writerow([qc_input.value for qc_input in qc_inputs if qc_input.output])
        summary.written_rows += 1

    return summary
