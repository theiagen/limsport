"""Pydantic models for the YAML export config, plus the loader that turns a
config file on disk into a validated ExportConfig.

The config works as a column allow-list: if one is given, only the columns
listed in it make it into the output (see transform.py). Each column can
also be renamed and given QC rules.
"""

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
    """The six comparison operators a QC condition can use."""

    GT = ">"
    GE = ">="
    EQ = "="
    LE = "<="
    LT = "<"
    APPROX = "~="  # within tolerance_percent of value


class QCCondition(BaseModel):
    """A single comparison: `operator value`, e.g. `>= 1000`.

    A column can have several of these (see ColumnConfig.qc) to express ranges
    like ">= 1000 and <= 1000000".

    `~=` requires `tolerance_percent`
    (e.g. `{operator: "~=", value: 1000000, tolerance_percent: 5}`
    passes for any value within 5% of 1000000, in either direction).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    operator: QCOperator
    value: int | float | str | bool
    tolerance_percent: float | None = None

    @model_validator(mode="after")
    def _validate_operator_constraints(self) -> "QCCondition":
        # reject booleans because `value: true` becomes 1.0, not "true"
        if isinstance(self.value, bool):
            # keep this functionality for now but we may want to confirm presence/absence of content
            # with a boolean later depending on conversation w/ analysts
            raise ValueError(
                f"QC value cannot be a boolean ({self.value!r}); "
                'quote it as a string (e.g. "true") if that\'s what you mean'
            )
        # strings can only use equivalence; raise error if a str is w/ any other comparator
        if self.operator is not QCOperator.EQ:
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


class ExportConfig(BaseModel):
    """The top-level shape of the YAML config file: just a list of columns.

    It's a list rather than a `{name: {...}}` mapping on purpose. A mapping
    would let a duplicate key silently overwrite itself before this code
    even runs; a list lets `_validate_columns` catch that instead.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    columns: list[ColumnConfig]

    @field_validator("columns")
    @classmethod
    def _validate_columns(cls, columns: list[ColumnConfig]) -> list[ColumnConfig]:
        if not columns:
            raise ValueError("config 'columns' must not be empty")

        dupes = _find_duplicates(c.name for c in columns)
        if dupes:
            raise ValueError(f"Duplicate column name(s) in config: {sorted(dupes)}")

        output_dupes = _find_duplicates(name for c in columns for name in c.output_names)
        if output_dupes:
            raise ValueError(f"Duplicate output column name(s) in config: {sorted(output_dupes)}")
        return columns


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
