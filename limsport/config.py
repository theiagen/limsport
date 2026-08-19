"""
Pydantic models for the YAML export config, plus the loader that turns a
config file on disk into a validated ExportConfig.

The config works as a column allow-list: if one is given, only the columns
listed in it make it into the output (see transform.py). Each column can
also be renamed and given QC rules. `columns` can be omitted entirely if
the config only exists for its `set_qc` (run-level) rules -- see
ExportConfig.

Included classes:
    - QCCondition
    - ConditionalQC
    - FileParsingOutput
    - ColumnConfig
    - SetQCCheck
    - SetQCRule
    - ExportConfig

External methods:
    - load_config()
"""

import re
from collections.abc import Iterable
from enum import Enum
from pathlib import Path
from typing import ClassVar

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .exceptions import ConfigError


def _find_duplicate_columns(columns: Iterable[str]) -> set[str]:
    """
    Find the names that show up more than once.

    Args:
        columns: the names to check, in any order.

    Returns:
        The set of names that appeared at least twice, empty if there were none.
    """
    seen: set[str] = set()
    duplicates: set[str] = set()

    for column_name in columns:
        if column_name in seen:
            duplicates.add(column_name)
        else:
            seen.add(column_name)
    return duplicates


class QCOperator(str, Enum):
    """The comparison operators a QC condition can use."""

    GT = ">"
    GE = ">="
    EQ = "="
    LE = "<="
    LT = "<"
    APPROX = "~="  # within tolerance_percent of value
    CONTAINS = "contains"
    DOES_NOT_CONTAIN = "does_not_contain"
    IS_EMPTY = "is_empty"
    IS_NOT_EMPTY = "is_not_empty"


class QCCondition(BaseModel):
    """
    A single comparison: `operator value`, e.g. `>= 1000`.

    A column can have several qc conditions (see ColumnConfig.qc) to express ranges
    like ">= 1000 and <= 1000000"

    Attributes:
        - model_config: pydantic
        - operator: the QCOperator for the condition
        - value: the value for the operator
        - tolerance_percent: the modifier for the APPROX condition
        - case_insensitive: the modifier for string conditions
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    operator: QCOperator
    value: int | float | str | bool | None = None
    tolerance_percent: float | None = None
    case_insensitive: bool = False

    @model_validator(mode="after")
    def _validate_operator_requirements(self) -> "QCCondition":
        """Check that this operator can take the `value` and modifiers it was given.

        Returns:
          `self`, unchanged, once every rule holds.

        Raises:
          ValueError: if the value or modifiers don't suit the operator.
        """
        # presence-absence operators and applicable modifiers
        if self.operator in (QCOperator.IS_EMPTY, QCOperator.IS_NOT_EMPTY):
            if self.value is not None:
                raise ValueError(
                    f"operator {self.operator.value!r} does not take a value"
                )
            if self.case_insensitive:
                raise ValueError(
                    f"case_insensitive is not valid with operator {self.operator.value!r}"
                )
            if self.tolerance_percent is not None:
                raise ValueError(
                    f"tolerance_percent is not valid with operator {self.operator.value!r}"
                )
            return self

        if self.value is None:
            raise ValueError(f"operator {self.operator.value!r} requires a value")

        # boolean operators
        if isinstance(self.value, bool):
            # reject booleans because `value: true` becomes 1.0, not "true"
            # keep this functionality for now but we may want to confirm presence/absence
            # of content with a boolean later depending on conversation w/ analysts??
            raise ValueError(  # noqa: TRY004 -- we want this to fail as a config error instead of type
                f'QC value cannot be a boolean ({self.value!r}); quote it as a string (e.g. "true") if that\'s what you mean'
            )

        # string-only operators
        if self.operator in (QCOperator.CONTAINS, QCOperator.DOES_NOT_CONTAIN):
            if not isinstance(self.value, str):
                # don't check for substrings in numbers that's silly
                raise ValueError(
                    f"operator {self.operator.value!r} requires a string value, got {self.value!r}"
                )
            if self.value == "":
                # an empty substring is always found (or never absent)
                raise ValueError(
                    f"operator {self.operator.value!r} requires a non-empty string value"
                )
        elif self.operator is not QCOperator.EQ:
            # string values can only use equivalence or substring operators; error if not
            if not isinstance(self.value, (int, float)):
                # ValueError for the same reason as the boolean check above
                raise ValueError(
                    f"operator {self.operator.value!r} requires a numeric value, "
                    f"got the string {self.value!r}"
                )

        # string-only modifiers
        if self.case_insensitive and not isinstance(self.value, str):
            raise ValueError(
                "case_insensitive=True is only valid when value is a string"
            )

        # numeric operators and modifiers
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


class ConditionalQC(BaseModel):
    """
    The conditional form of `qc` has a list of conditions that apply to a given row
    depending on that rows value in the `match` column

    A row whose `match` value isn't a key in `rules` uses `default` if given. Without a
    `default`, it's reported as a QC fail

    Attributes:
        - model_config: pydantic
        - match: the column to match on
        - rules: a dictionary of match conditions and their associated QCCondition(s)
        - default: the default QCCondition(s) to apply if no match was found
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    match: str = Field(min_length=1)
    rules: dict[str, list[QCCondition]]
    default: list[QCCondition] | None = None

    @field_validator("rules")
    @classmethod
    def _rules_are_not_empty(
        cls, rules: dict[str, list[QCCondition]]
    ) -> dict[str, list[QCCondition]]:
        """
        A conditional `qc` has to configure at least one rule.

        Args:
            rules: the `match` value to conditions mapping being validated.

        Returns:
            The validated rules mapping.

        Raises:
            ValueError: if the mapping is empty.
        """
        if not rules:
            raise ValueError("qc.rules must not be empty")
        return rules


def _qc_is_set(qc: list[QCCondition] | ConditionalQC) -> bool:
    """
    True if `qc` has content (either as a list or in conditional format)

    Args:
        qc: the configured `qc`, in either form.

    Returns:
        True for any ConditionalQC, or for a non-empty condition list.
    """
    return isinstance(qc, ConditionalQC) or bool(qc)


class FileParsingOutput(BaseModel):
    """
    One output value extracted from a column's file via `command`

    `qc` can be either a plain list[QCCondition], or the ConditionalQC form. Requires --allow-file-parsing on the CLI to avoid lawsuits probably

    Attributes:
        - model_config: pydantic
        - name: the name of the output column generated from the command
        - command: the parsing command to perform on the file
        - timeout_seconds: how long to try before giving up
        - qc: the list any QC to perform on the generated output
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    command: str = Field(min_length=1)
    timeout_seconds: float | None = None
    qc: list[QCCondition] | ConditionalQC = Field(default_factory=list)

    @field_validator("command")
    @classmethod
    def _command_is_not_blank(cls, command: str) -> str:
        """
        A `command` needs more than whitespace in it.

        Args:
            command: the command string being validated.

        Returns:
            The validated command string.

        Raises:
            ValueError: if the command is empty or only whitespace.
        """
        # min_length=1 lets whitespace-only strings (like "   ") through booo
        if not command.strip():
            raise ValueError("file_parsing command cannot be blank")
        return command

    @field_validator("timeout_seconds")
    @classmethod
    def _timeout_must_be_positive(cls, timeout_seconds: float | None) -> float | None:
        """
        `timeout_seconds`, when it's given at all, has to be a positive number.

        Args:
            timeout_seconds: the timeout being validated, or None for no timeout.

        Returns:
            The validated timeout, still None if none was set.

        Raises:
            ValueError: if the timeout is zero or negative.
        """
        # None means no timeout. 0 or negative are treated by subprocess as already
        # expired, so every command fails instantly; give an error
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError(
                f"timeout_seconds must be greater than 0 (use no timeout_seconds at all for unlimited), got {timeout_seconds!r}"
            )
        return timeout_seconds


class ColumnConfig(BaseModel):
    """
    An entry in the config's `columns` list: one column to keep in the
    output, with optional rename and QC rules.

    Attributes:
        - model_config: pydantic
        - name: the name of the input column
        - rename: the name of the output column
        - qc: the QC to perform on the column content
        - file_parsing: if this column requires file parsing

    `qc` is either a plain list[QCCondition] or the ConditionalQC form
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    name: str
    rename: str | None = None
    qc: list[QCCondition] | ConditionalQC = Field(default_factory=list)
    file_parsing: list[FileParsingOutput] | None = None

    @field_validator("file_parsing")
    @classmethod
    def _file_parsing_outputs_are_not_empty_and_unique(
        cls, outputs: list[FileParsingOutput] | None
    ) -> list[FileParsingOutput] | None:
        """
        A `file_parsing` list needs at least one output and no repeated names.

        Args:
            outputs: the file_parsing outputs being validated, or None if there are none.

        Returns:
            The validated outputs, still None if there were none.

        Raises:
            ValueError: if the list is empty or two outputs share a name.
        """
        if outputs is not None:
            if not outputs:
                raise ValueError("file_parsing must not be an empty list")
            dupes = _find_duplicate_columns(o.name for o in outputs)
            if dupes:
                raise ValueError(
                    f"Duplicate file_parsing output name(s): {sorted(dupes)}"
                )
        return outputs

    @model_validator(mode="after")
    def _file_parsing_excludes_rename_and_qc(self) -> "ColumnConfig":
        """
        A file_parsing column can't also carry a `rename` or its own `qc`.

        Returns:
            `self`, unchanged, once every rule holds.

        Raises:
            ValueError: if a file_parsing column sets `rename` or `qc`.
        """
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
        """
        The column's name in the output table.

        A file_parsing column uses `generated_output_names()` instead.

        Returns:
          The rename if given, else the original name.
        """
        return self.rename or self.name

    @property
    def generated_output_names(self) -> list[str]:
        """
        Every output name this column contributes to the output header.

        Returns:
          One name per file_parsing output, or just `output_name` for a column
          that doesn't use file_parsing.
        """
        if self.file_parsing is not None:
            return [o.name for o in self.file_parsing]
        return [self.output_name]


class SetQCMatch(BaseModel):
    """
    Identifies which sample(s) a `set_qc` rule applies to.

    Attributes:
        - model-config: pydantic

        Only one of the following attributes is accepted at a time:

        - sample_pattern: a case-sensitive substring match against sample name
        - sample_regex: `re.search` against the sample name, case-sensitive (use an inline `(?i)` flag for case-insensitive matching).
        - samples: an explicit, exact list of sample names.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    sample_pattern: str | None = None
    sample_regex: str | None = None
    samples: list[str] | None = None

    @model_validator(mode="after")
    def _only_one_match_method(self) -> "SetQCMatch":
        """
        A match picks its samples exactly one way, and that one way has to be usable.

        Returns:
            `self`, unchanged, once every rule holds.

        Raises:
            ValueError: if the match doesn't name exactly one usable method.
        """
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
                f"set_qc match must specify exactly one of sample_pattern, sample_regex, or samples; got {given or 'none'}"
            )
        if self.samples is not None and not self.samples:
            raise ValueError("set_qc match.samples must not be empty")
        if self.sample_regex is not None:
            try:
                re.compile(self.sample_regex)
            except re.error as e:
                raise ValueError(
                    f"invalid sample_regex {self.sample_regex!r}: {e}"
                ) from e
        return self

    def applies_to(self, sample: str) -> bool:
        """
        True if the qc match rule applies to this sample.

        Args:
            sample: the sample name to test.

        Returns:
            True if whichever match method was configured selects `sample`.
        """
        if self.sample_pattern is not None:
            return self.sample_pattern in sample
        if self.sample_regex is not None:
            return re.search(self.sample_regex, sample) is not None
        # confirm that the list of samples isn't "None"
        assert self.samples is not None
        return sample in self.samples


class SetQCCheck(BaseModel):
    """
    One column and QC condition list within a `SetQCRule`. A rule can list
    conditions, checked against the same matched sample(s)
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    column: str = Field(min_length=1)
    qc: list[QCCondition] = Field(min_length=1)


class SetQCRule(BaseModel):
    """A run-level (set) QC check: every sample identified by `match` must
    pass every check in `columns` or else the entire run is considered a QC fail.

    A rule that matches zero samples in a given run is a LIMSport error, not a QC
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    match: SetQCMatch
    columns: list[SetQCCheck] = Field(min_length=1)

    @field_validator("columns")
    @classmethod
    def _no_duplicate_columns_permitted(
        cls, columns: list[SetQCCheck]
    ) -> list[SetQCCheck]:
        """One set_qc rule can only check a given column once.

        Args:
          columns: the rule's checks being validated.

        Returns:
          The validated checks.

        Raises:
          ValueError: if two checks in the rule name the same column.
        """
        dupes = _find_duplicate_columns(c.column for c in columns)
        if dupes:
            raise ValueError(
                f"Duplicate column(s) within one set_qc rule: {sorted(dupes)}"
            )
        return columns


class ExportConfig(BaseModel):
    """The top-level shape of the YAML config file: a list of columns, plus optional
    run-level (`set_qc`) checks.

    `columns` lists every column to keep in the output and can be omitted if `set_qc`
    is provided.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    columns: list[ColumnConfig] | None = None
    set_qc: list[SetQCRule] = Field(default_factory=list)

    @field_validator("set_qc")
    @classmethod
    def _validate_set_qc(cls, set_qc: list[SetQCRule]) -> list[SetQCRule]:
        """Every set_qc rule needs its own name.

        Args:
          set_qc: the run-level rules being validated.

        Returns:
          The validated rules.

        Raises:
          ValueError: if two rules share a name.
        """
        dupes = _find_duplicate_columns(rule.name for rule in set_qc)
        if dupes:
            raise ValueError(f"Duplicate set_qc rule name(s): {sorted(dupes)}")
        return set_qc

    @model_validator(mode="after")
    def _validate_columns(self) -> "ExportConfig":
        """Check that the config asks for something and that output names don't collide.

        Returns:
          `self`, unchanged, once every rule holds.

        Raises:
          ValueError: if the config configures nothing, or two columns claim the same output name.
        """
        if self.columns is None:
            if not self.set_qc:
                raise ValueError(
                    "config must configure at least one of 'columns' or 'set_qc' (an empty config does nothing)"
                )
            return self

        if not self.columns:
            raise ValueError(
                "config 'columns' must not be empty; omit it entirely if you don't want to perform any column operations"
            )

        dupes = _find_duplicate_columns(c.name for c in self.columns)
        if dupes:
            raise ValueError(f"Duplicate column name(s) in config: {sorted(dupes)}")

        generated_output_dupes = _find_duplicate_columns(
            name for c in self.columns for name in c.generated_output_names
        )
        if generated_output_dupes:
            raise ValueError(
                f"Duplicate generated file_parsing output column name(s) in config: {sorted(generated_output_dupes)}"
            )
        return self


def load_config(path: Path) -> ExportConfig:
    """Read and validate a YAML config file

    Args:
      path: the config file to read.

    Returns:
      The validated ExportConfig the file describes.

    Raises:
      ConfigError: if the file isn't valid YAML, or doesn't validate as a config.
    """
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML: {e}") from e
    try:
        # try to validate the config
        return ExportConfig.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"{path}: invalid config: {e}") from e
