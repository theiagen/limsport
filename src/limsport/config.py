"""Pydantic models for the YAML export config, plus the loader that turns a
config file on disk into a validated ExportConfig.

The config works as a column allow-list: if one is given, only the columns
listed in it make it into the output (see transform.py). Each column can
also be renamed and given QC rules.
"""

from enum import Enum
from pathlib import Path
from typing import ClassVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .exceptions import ConfigError


class QCOperator(str, Enum):
    """The six comparison operators a QC condition can use.

    Subclassing str means these compare equal to their symbol directly
    (QCOperator.GE == ">=" is True), which keeps YAML round trips and
    failure messages simple.
    """

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

    `~=` needs a companion `tolerance_percent`
    (e.g. `{operator: "~=", value: 1000000, tolerance_percent: 5}`
    passes for any value within 5% of 1000000, in either direction).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    operator: QCOperator
    value: int | float | str | bool
    tolerance_percent: float | None = None

    @model_validator(mode="after")
    def _validate_operator_constraints(self) -> "QCCondition":
        # bool is only in the value union because bool subclasses int in
        # Python. Reject it everywhere, including `=` -- otherwise
        # `value: true` would compare as the number 1.0 instead of the
        # text "true", which isn't what anyone means by writing `true`.
        if isinstance(self.value, bool):
            raise ValueError(
                f"QC value cannot be a boolean ({self.value!r}); "
                'quote it as a string (e.g. "true") if that\'s what you mean to match'
            )
        # only use equivalence for string values; >, >=, <=, <, and ~= all
        # require a genuine number to compare against.
        if self.operator is not QCOperator.EQ:
            if not isinstance(self.value, (int, float)):
                raise ValueError(
                    f"operator {self.operator.value!r} requires a numeric value, got {self.value!r}"
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


class FileParsingInstruction(BaseModel):
    """Runs via bash against the column's raw cell value (a file path,
    optionally a gs:// URI). Its output becomes the cell's real value and
    flows through QC and into the output like any other field.

    Requires --allow-file-parsing on the CLI even when the config asks
    for it, since it means running a command the config author wrote.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    command: str = Field(min_length=1)
    timeout_seconds: float | None = None

    @field_validator("command")
    @classmethod
    def _command_not_blank(cls, command: str) -> str:
        # min_length=1 lets a whitespace-only string like "   " through.
        # Bash treats that as a no-op, not a real command, so it's a config mistake
        if not command.strip():
            raise ValueError("file_parsing command cannot be blank")
        return command

    @field_validator("timeout_seconds")
    @classmethod
    def _timeout_must_be_positive(cls, timeout_seconds: float | None) -> float | None:
        # None means no timeout. 0 or negative are treated by subprocess
        # as if it has already expired, so every command would fail instantly.
        # Someone writing `timeout_seconds: 0` probably means "unlimited",
        # so this should be a clear config error, not a confusing runtime timeout.
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError(
                f"timeout_seconds must be greater than 0 (use no timeout_seconds at all for unlimited), got {timeout_seconds!r}"
            )
        return timeout_seconds


class ColumnConfig(BaseModel):
    """An entry in the config's `columns` list: one column to keep in the
    output, with optional rename and QC rules."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    name: str
    rename: str | None = None
    qc: list[QCCondition] = Field(default_factory=list)
    file_parsing: FileParsingInstruction | None = None

    @property
    def output_name(self) -> str:
        """The column's name in the output table: the rename if given, else the original name."""
        return self.rename or self.name


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
        seen: set[str] = set()
        dupes: set[str] = set()
        for c in columns:
            (dupes if c.name in seen else seen).add(c.name)
        if dupes:
            raise ValueError(f"Duplicate column name(s) in config: {sorted(dupes)}")
        return columns


class QCFailure(BaseModel):
    """One sample's failing check on one column, reported both as a log
    line and a row in the --qc-report TSV.

    `column` is always the input's original name. `output_column` is what
    it's renamed to (same as `column` if it wasn't renamed) -- keeping
    both means the report is never ambiguous about which output cell a
    failure actually maps to.
    """

    sample: str
    column: str
    output_column: str
    operator: QCOperator
    expected: int | float | str | bool
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
