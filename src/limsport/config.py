"""Pydantic models for the YAML export config, plus the loader that turns a
config file on disk into a validated ExportConfig.

The config works as a column allow-list: if one is given, only the columns
listed in it make it into the output (see transform.py). Each column can
also be renamed and given QC rules. `columns` can be omitted entirely if
the config only exists for its `set_qc` (run-level) rules -- see
ExportConfig.
"""

import re
from collections.abc import Iterable
from enum import Enum
from pathlib import Path
from typing import ClassVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .exceptions import ConfigError


def _find_duplicates(names: Iterable[str]) -> set[str]:
    """Return every name that appears more than once in `names`."""
    seen: set[str] = set()
    dupes: set[str] = set()
    for name in names:
        (dupes if name in seen else seen).add(name)
    return dupes


class QCOperator(str, Enum):
    """The comparison operators a QC condition can use."""

    GT = ">"
    GE = ">="
    EQ = "="
    LE = "<="
    LT = "<"
    APPROX = "~="  # within tolerance_percent of value
    CONTAINS = "contains"  # string value is a substring of the cell
    DOES_NOT_CONTAIN = "does_not_contain"  # string value is NOT a substring of the cell
    IS_EMPTY = "is_empty"  # cell is blank/whitespace-only
    IS_NOT_EMPTY = "is_not_empty"  # cell has real content


class QCCondition(BaseModel):
    """A single comparison: `operator value`, e.g. `>= 1000`.

    A column can have several of these (see ColumnConfig.qc) to express ranges
    like ">= 1000 and <= 1000000".

    `~=` requires `tolerance_percent`
    (e.g. `{operator: "~=", value: 1000000, tolerance_percent: 5}`
    passes for any value within 5% of 1000000, in either direction).

    `contains`/`does_not_contain` take a string `value` and check whether it's a
    substring of the cell, e.g. `{operator: "contains", value: "Escherichia"}`
    passes for a cell of "Escherichia coli".

    `case_insensitive` (default False) controls string comparison for `=`,
    `contains`, and `does_not_contain`; it's a config error to set it on a
    condition whose value isn't a string.

    `is_empty`/`is_not_empty` take no `value` at all -- they check whether
    the cell itself is blank (or whitespace-only). This is the one place a
    blank cell isn't an automatic failure (see qc.py): useful for a
    negative control where an empty result is the expected, passing state.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    operator: QCOperator
    value: int | float | str | bool | None = None
    tolerance_percent: float | None = None
    case_insensitive: bool = False

    @model_validator(mode="after")
    def _validate_operator_constraints(self) -> "QCCondition":
        if self.operator in (QCOperator.IS_EMPTY, QCOperator.IS_NOT_EMPTY):
            if self.value is not None:
                raise ValueError(f"operator {self.operator.value!r} does not take a value")
            if self.case_insensitive:
                raise ValueError(f"case_insensitive is not valid with operator {self.operator.value!r}")
            if self.tolerance_percent is not None:
                raise ValueError(f"tolerance_percent is not valid with operator {self.operator.value!r}")
            return self

        if self.value is None:
            raise ValueError(f"operator {self.operator.value!r} requires a value")

        # reject booleans because `value: true` becomes 1.0, not "true"
        if isinstance(self.value, bool):
            # keep this functionality for now but we may want to confirm presence/absence of content
            # with a boolean later depending on conversation w/ analysts
            raise ValueError(
                f"QC value cannot be a boolean ({self.value!r}); "
                'quote it as a string (e.g. "true") if that\'s what you mean'
            )
        if self.operator in (QCOperator.CONTAINS, QCOperator.DOES_NOT_CONTAIN):
            # substring checks only make sense against a string value
            if not isinstance(self.value, str):
                raise ValueError(
                    f"operator {self.operator.value!r} requires a string value, got {self.value!r}"
                )
            if self.value == "":
                # an empty substring is always found (or never absent), so
                # this would silently check nothing
                raise ValueError(f"operator {self.operator.value!r} requires a non-empty string value")
        elif self.operator is not QCOperator.EQ:
            # strings can only use equivalence or substring operators; raise
            # error if a str is w/ any other comparator
            if not isinstance(self.value, (int, float)):
                raise ValueError(
                    f"operator {self.operator.value!r} requires a numeric value, "
                    f"got the string {self.value!r}"
                )
        if self.operator is QCOperator.APPROX:
            if self.tolerance_percent is None:
                raise ValueError("operator '~=' requires tolerance_percent to be set")
            if self.tolerance_percent <= 0:
                raise ValueError("tolerance_percent must be greater than 0")
        elif self.tolerance_percent is not None:
            raise ValueError(
                f"tolerance_percent is only valid with operator '~=', got operator {self.operator.value!r}"
            )
        if self.case_insensitive and not isinstance(self.value, str):
            raise ValueError("case_insensitive=True is only valid when value is a string")
        return self


class QCByRule(BaseModel):
    """The conditional form of `qc`: which condition list applies to a
    given row depends on another column's (`match`) raw value for that
    row, looked up in `rules`.

    A row whose `match` value isn't a key in `rules` uses `default` if
    given. Without a `default`, it's reported as its own QC failure (see
    qc.py) instead of silently passing -- an unrecognized value is a
    data/config gap worth surfacing, not hiding.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    match: str = Field(min_length=1)
    rules: dict[str, list[QCCondition]]
    default: list[QCCondition] | None = None

    @field_validator("rules")
    @classmethod
    def _rules_not_empty(cls, rules: dict[str, list[QCCondition]]) -> dict[str, list[QCCondition]]:
        if not rules:
            raise ValueError("qc.rules must not be empty")
        return rules


def _qc_is_set(qc: list[QCCondition] | QCByRule) -> bool:
    """True if `qc` carries real conditions -- a non-empty list, or the
    conditional form (which is never "empty" the way a list can be)."""
    return isinstance(qc, QCByRule) or bool(qc)


class FileParsingOutput(BaseModel):
    """One named value extracted from a column's file: `command` runs
    against the raw cell value (a file path, potentially GCP uri) which
    becomes its own column.

    `qc` accepts the same two forms as ColumnConfig.qc: a plain
    list[QCCondition], or the conditional QCByRule form.

    Requires --allow-file-parsing on the CLI even when the config asks
    for it, since it means running a command the config author wrote.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    command: str = Field(min_length=1)
    timeout_seconds: float | None = None
    qc: list[QCCondition] | QCByRule = Field(default_factory=list)

    @field_validator("command")
    @classmethod
    def _command_not_blank(cls, command: str) -> str:
        # min_length=1 lets whitespace-only strings (like "   ") through
        # which is not a command but a config mistake
        if not command.strip():
            raise ValueError("file_parsing command cannot be blank")
        return command

    @field_validator("timeout_seconds")
    @classmethod
    def _timeout_must_be_positive(cls, timeout_seconds: float | None) -> float | None:
        # None means no timeout. 0 or negative are treated by subprocess
        # as already expired, so every command fails instantly; give an error
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError(
                f"timeout_seconds must be greater than 0 (use no timeout_seconds at all for unlimited), got {timeout_seconds!r}"
            )
        return timeout_seconds


class ColumnConfig(BaseModel):
    """An entry in the config's `columns` list: one column to keep in the
    output, with optional rename and QC rules.

    `qc` is either a plain list[QCCondition] (the same fixed list for
    every row) or the conditional QCByRule form (which condition list
    applies is chosen per row from another column's value; see QCByRule).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    name: str
    rename: str | None = None
    qc: list[QCCondition] | QCByRule = Field(default_factory=list)
    file_parsing: list[FileParsingOutput] | None = None

    @field_validator("file_parsing")
    @classmethod
    def _file_parsing_outputs_not_empty_and_unique(
        cls, outputs: list[FileParsingOutput] | None
    ) -> list[FileParsingOutput] | None:
        if outputs is not None:
            if not outputs:
                raise ValueError("file_parsing must not be an empty list")
            dupes = _find_duplicates(o.name for o in outputs)
            if dupes:
                raise ValueError(f"Duplicate file_parsing output name(s): {sorted(dupes)}")
        return outputs

    @model_validator(mode="after")
    def _file_parsing_excludes_rename_and_qc(self) -> "ColumnConfig":
        # Once file_parsing is set, output identity and QC come from its
        # outputs list -- letting rename/qc coexist would leave it
        # ambiguous which one actually governs a given output.
        if self.file_parsing is not None:
            if self.rename is not None:
                raise ValueError(
                    "rename is not valid on a file_parsing column; "
                    "set the output name via file_parsing[].name instead"
                )
            if _qc_is_set(self.qc):
                raise ValueError(
                    "qc is not valid on a file_parsing column; "
                    "set qc per output inside file_parsing[].qc instead"
                )
        return self

    @property
    def output_name(self) -> str:
        """The column's name in the output table: the rename if given, else the original name.

        Not meaningful for a file_parsing column -- use output_names instead.
        """
        return self.rename or self.name

    @property
    def output_names(self) -> list[str]:
        """Every name this column contributes to the output header, in order.

        A file_parsing column can contribute one name per configured output
        """
        if self.file_parsing is not None:
            return [o.name for o in self.file_parsing]
        return [self.output_name]


class SetQCMatch(BaseModel):
    """Identifies which sample(s) a `set_qc` rule applies to. Exactly one of
    these three must be given.

    `sample_pattern`: a case-sensitive substring match against the sample
    name (the input table's first column).
    `sample_regex`: `re.search` against the sample name, case-sensitive
    (use an inline `(?i)` flag for case-insensitive matching).
    `samples`: an explicit, exact list of sample names.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    sample_pattern: str | None = None
    sample_regex: str | None = None
    samples: list[str] | None = None

    @model_validator(mode="after")
    def _exactly_one_matcher(self) -> "SetQCMatch":
        given = [
            name
            for name, value in (
                ("sample_pattern", self.sample_pattern),
                ("sample_regex", self.sample_regex),
                ("samples", self.samples),
            )
            if value is not None
        ]
        if len(given) != 1:
            raise ValueError(
                "set_qc match must specify exactly one of sample_pattern, sample_regex, or samples; "
                f"got {given or 'none'}"
            )
        if self.samples is not None and not self.samples:
            raise ValueError("set_qc match.samples must not be empty")
        if self.sample_regex is not None:
            try:
                re.compile(self.sample_regex)
            except re.error as e:
                raise ValueError(f"invalid sample_regex {self.sample_regex!r}: {e}") from e
        return self

    def matches(self, sample: str) -> bool:
        """True if `sample` is identified by this matcher."""
        if self.sample_pattern is not None:
            return self.sample_pattern in sample
        if self.sample_regex is not None:
            return re.search(self.sample_regex, sample) is not None
        assert self.samples is not None
        return sample in self.samples


class SetQCCheck(BaseModel):
    """One column + QC condition list within a `SetQCRule`. A rule can list
    several of these, checked against the same matched sample(s) -- e.g. one
    rule keyed on "is this the NTC" checking both read count and
    contamination percent, instead of writing two separate rules that repeat
    the same `match`."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    column: str = Field(min_length=1)
    qc: list[QCCondition] = Field(min_length=1)


class SetQCRule(BaseModel):
    """A run-level (set) QC check: every sample identified by `match` must
    pass every check in `columns` -- each is its own column and QC condition
    list, all read from the same matched sample(s)' row.

    Unlike per-row `qc`, a failure here fails the *entire run* -- every
    sample is dropped from output, not just the one(s) `match` identifies
    (see transform.py's run_export). None of `columns[].column` need be
    listed in the config's `columns` allow-list -- the same "readable but
    not necessarily exported" treatment `QCByRule.match` already gets.

    A rule that matches zero samples in a given run is a hard error (there's
    no sample to attach a QC failure to), not a QC failure.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    match: SetQCMatch
    columns: list[SetQCCheck] = Field(min_length=1)

    @field_validator("columns")
    @classmethod
    def _no_duplicate_columns(cls, columns: list[SetQCCheck]) -> list[SetQCCheck]:
        dupes = _find_duplicates(c.column for c in columns)
        if dupes:
            raise ValueError(f"Duplicate column(s) within one set_qc rule: {sorted(dupes)}")
        return columns


class ExportConfig(BaseModel):
    """The top-level shape of the YAML config file: a list of columns, plus
    optional run-level (`set_qc`) checks.

    `columns` lists every column to keep in the output -- a list rather
    than a `{name: {...}}` mapping on purpose (a mapping would let a
    duplicate key silently overwrite itself before this code even runs).
    It can be omitted entirely: every input column then passes through
    unfiltered and unrenamed, same as running with no config at all, except
    `set_qc` still applies -- but only if `set_qc` configures at least one
    rule, since a config with neither does nothing.

    An explicit `columns: []` is always rejected, regardless of `set_qc`:
    unlike omitting the key, writing an empty list looks like a config that
    meant to list columns and didn't, not a deliberate "pass everything
    through."
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    columns: list[ColumnConfig] | None = None
    set_qc: list[SetQCRule] = Field(default_factory=list)

    @field_validator("set_qc")
    @classmethod
    def _validate_set_qc(cls, set_qc: list[SetQCRule]) -> list[SetQCRule]:
        dupes = _find_duplicates(rule.name for rule in set_qc)
        if dupes:
            raise ValueError(f"Duplicate set_qc rule name(s): {sorted(dupes)}")
        return set_qc

    @model_validator(mode="after")
    def _validate_columns(self) -> "ExportConfig":
        if self.columns is None:
            if not self.set_qc:
                raise ValueError(
                    "config must configure at least one of 'columns' or 'set_qc' "
                    "(an empty config does nothing)"
                )
            return self

        if not self.columns:
            raise ValueError(
                "config 'columns' must not be empty; omit it entirely to pass every "
                "input column through unfiltered instead"
            )

        dupes = _find_duplicates(c.name for c in self.columns)
        if dupes:
            raise ValueError(f"Duplicate column name(s) in config: {sorted(dupes)}")

        output_dupes = _find_duplicates(name for c in self.columns for name in c.output_names)
        if output_dupes:
            raise ValueError(f"Duplicate output column name(s) in config: {sorted(output_dupes)}")
        return self


class QCFailure(BaseModel):
    """One sample's failing check on one output, reported both as a log
    line and a row in the --qc-report TSV.

    `column` is always the input's original (source) column name.
    `output_column` is that column's single output name -- its rename, or
    itself if there was no rename -- except for a file_parsing column,
    where several outputs can share one `column` and `output_column`
    instead names the specific output that failed.

    `operator`/`expected` are None for a conditional `qc` whose row
    matched no rule and has no default
    """

    sample: str
    column: str
    output_column: str
    operator: QCOperator | None
    expected: int | float | str | bool | None
    actual: str | None
    reason: str


class QCOutcome(BaseModel):
    """The result of evaluating every configured QC rule against one sample's row."""

    sample: str
    passed: bool
    failures: list[QCFailure] = Field(default_factory=list)


def load_config(path: Path) -> ExportConfig:
    """Read and validate a YAML config file"""
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML: {e}") from e
    try:
        return ExportConfig.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"{path}: invalid config: {e}") from e
