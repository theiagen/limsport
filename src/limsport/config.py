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


class FileParsingOutput(BaseModel):
    """One named value extracted from a column's file: `command` runs via
    bash against the raw cell value (a file path, optionally a gs://
    URI), and its output becomes this output's value, flowing through
    its own `qc` and into the output table as its own column.

    A column's `file_parsing` is always a list -- one entry for
    each extracted value. All commands for one column's file_parsing run
    against the same localized copy of the file.

    Requires --allow-file-parsing on the CLI even when the config asks
    for it, since it means running a command the config author wrote.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    command: str = Field(min_length=1)
    timeout_seconds: float | None = None
    qc: list[QCCondition] = Field(default_factory=list)

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


class QCByRule(BaseModel):
    """Organism/discriminator-conditioned QC for one column: which
    condition list applies to a given row depends on another column's
    (`match`) raw value for that row, looked up in `rules`.

    A row whose `match` value isn't a key in `rules` uses `default` if
    given. Without a `default`, it's reported in the QC report
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    match: str = Field(min_length=1)
    rules: dict[str, list[QCCondition]]
    default: list[QCCondition] | None = None

    @field_validator("rules")
    @classmethod
    def _rules_not_empty(cls, rules: dict[str, list[QCCondition]]) -> dict[str, list[QCCondition]]:
        if not rules:
            raise ValueError("qc_by.rules must not be empty")
        return rules


class ColumnConfig(BaseModel):
    """An entry in the config's `columns` list: one column to keep in the
    output, with optional rename and QC rules.

    A column with `file_parsing` set gets its output name(s) and QC from
    a list instead. A column with `qc_by` set gets its QC conditions
    chosen per row from another column's value, instead of one fixed
    `qc` list.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    name: str
    rename: str | None = None
    qc: list[QCCondition] = Field(default_factory=list)
    qc_by: QCByRule | None = None
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
    def _mutually_exclusive_qc_declarations(self) -> "ColumnConfig":
        # A column's QC conditions come from exactly one place: its own
        # qc, its qc_by rules, or (for a file_parsing column) each
        # output's own qc. Allowing more than one to coexist would leave
        # it ambiguous which one actually governs a given row/output.
        if self.qc_by is not None and self.qc:
            raise ValueError(
                "qc and qc_by cannot both be set on the same column; "
                "use qc_by.default for a fallback instead of column-level qc"
            )
        if self.file_parsing is not None:
            if self.rename is not None:
                raise ValueError(
                    "rename is not valid on a file_parsing column; "
                    "set the output name via file_parsing[].name instead"
                )
            if self.qc or self.qc_by is not None:
                raise ValueError(
                    "qc/qc_by are not valid on a file_parsing column; "
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
    instead names the specific output that failed. Keeping both means the
    report is never ambiguous about which output cell a failure actually
    maps to.

    `operator`/`expected` are None for a qc_by column whose row matched
    no rule and has no default -- there's no condition to point at, only
    the fact that none applied (see `reason`).
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
