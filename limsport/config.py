"""
Pydantic models for the YAML export config, plus the loader that turns a
config file on disk into a validated ExportConfig.

The config works as a column allow-list: if one is given, only the columns
listed in it make it into the output. Each column can also be given a different
output name and QC rules. `columns` can be omitted entirely if the config only exists for its `set_qc`
(run-level) rules

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

import functools
import re
from collections import Counter
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


def _reject_duplicates(items: Iterable[str], label: str) -> None:
    """
    Rejects a config list that names the same thing twice.

    Args:
        items: the names to check
        label: what the names are, used in the error message

    Raises:
        ValueError: if any name appears more than once.
    """
    duplicates = {item for item, count in Counter(items).items() if count > 1}
    if duplicates:
        raise ValueError(f"Duplicate {label}: {sorted(duplicates)}")


class _StrictModel(BaseModel):
    """
    Base for every config model: an unrecognised YAML key is an error, not ignored.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class QCOperator(str, Enum):
    """
    The comparison operators a QC condition can use.
    """

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


class QCCondition(_StrictModel):
    """
    A single comparison: `operator value`, e.g. `>= 1000`.

    A column can have several qc conditions to express ranges.

    Attributes:
        - operator: the QCOperator for the condition
        - value: the value for the operator
        - tolerance_percent: the modifier for the APPROX condition
        - case_insensitive: the modifier for string conditions
    """

    operator: QCOperator
    value: int | float | str | bool | None = None
    tolerance_percent: float | None = None
    case_insensitive: bool = False

    @model_validator(mode="after")
    def _validate_operator_requirements(self) -> "QCCondition":
        """Check that this operator can take the `value` and modifiers it was given.

        Returns:
          `self` unchanged

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

        # all other operators require a value; check for that
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


class ConditionalQC(_StrictModel):
    """
    The conditional form of `qc` has a list of conditions that apply to a given row
    depending on that rows value in the `match_column`

    A row whose `match_column` value isn't a key in `cases` uses `default` if given.
    Without a `default`, it's reported as a QC fail

    Attributes:
        - match_column: the column to match on
        - cases: a dictionary of match values and their associated QCCondition(s)
        - default: the default QCCondition(s) to apply if no match was found
    """

    match_column: str = Field(min_length=1)
    cases: dict[str, list[QCCondition]]
    default: list[QCCondition] | None = None

    @field_validator("cases")
    @classmethod
    def _cases_are_not_empty(
        cls, cases: dict[str, list[QCCondition]]
    ) -> dict[str, list[QCCondition]]:
        """
        A conditional `qc` has to configure at least one case.

        Args:
            cases: the dictionary of `match_column` value to qc conditions

        Returns:
            `cases` unchanged

        Raises:
            ValueError: if `cases` is empty.
        """
        if not cases:
            raise ValueError("qc.cases must not be empty")
        return cases


def _qc_is_set(qc: list[QCCondition] | ConditionalQC) -> bool:
    """
    True if `qc` has content (either as a list or in conditional format)

    Args:
        qc: the configured `qc`

    Returns:
        True for any ConditionalQC, or for a non-empty condition list.
    """
    return isinstance(qc, ConditionalQC) or bool(qc)


class FileParsingOutput(_StrictModel):
    """
    One output value extracted from a column's file via `command`

    `qc` can be either a plain list[QCCondition], or the ConditionalQC form. Requires
    --allow-file-parsing on the CLI, to avoid lawsuits probably

    Attributes:
        - output_column: the name of the output column generated from the command
        - command: the parsing command to perform on the file
        - timeout_seconds: how long to try before giving up
        - qc: the list any QC to perform on the generated output
    """

    output_column: str = Field(min_length=1)
    command: str = Field(min_length=1)
    timeout_seconds: float | None = None
    qc: list[QCCondition] | ConditionalQC = Field(default_factory=list)

    @field_validator("command")
    @classmethod
    def _command_is_not_blank(cls, command: str) -> str:
        """
        A `command` needs more than whitespace in it.

        Args:
            command: the command string

        Returns:
            `command` unchanged

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
        `timeout_seconds` when provided has to be a positive number.

        Args:
            timeout_seconds: the timeout input (None for no timeout)

        Returns:
            `timeout` unchanged

        Raises:
            ValueError: if the timeout is zero or negative.
        """
        # None means no timeout. 0 or negative are treated by subprocess as already
        # expired, so every command fails instantly; give an error
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError(
                f"timeout_seconds must be greater than 0 (exclude timeout_seconds for unlimited), got {timeout_seconds!r}"
            )
        return timeout_seconds


class ColumnConfig(_StrictModel):
    """
    An entry in the config's `columns` list: one column to keep in the output, with an
    optional output name and QC rules.

    Attributes:
        - input_column: the name of the input column
        - output_column: the name of the output column
        - qc: the QC to perform on the column content; either a plain list[QCCondition]
          or the ConditionalQC form
        - file_parsing: if this column requires file parsing
    """

    input_column: str
    output_column: str | None = None
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
            outputs: the file_parsing output column names

        Returns:
            `outputs` unchanged

        Raises:
            ValueError: if the list is empty or two outputs share a name.
        """
        if outputs is not None:
            if not outputs:
                raise ValueError("file_parsing must not be an empty list")
            _reject_duplicates(
                (o.output_column for o in outputs), "file_parsing output name(s)"
            )
        return outputs

    @model_validator(mode="after")
    def _file_parsing_excludes_output_column_and_qc(self) -> "ColumnConfig":
        """
        A column with file_parsing cannot have `output_column` or top-level `qc`.

        Returns:
            `self` unchanged

        Raises:
            ValueError: if a column with file_parsing also has `output_column` or top-level `qc`.
        """
        if self.file_parsing is not None:
            if self.output_column is not None:
                raise ValueError(
                    "output_column is not valid on a file_parsing column; "
                    "set the output name via file_parsing[].output_column instead"
                )
            if _qc_is_set(self.qc):
                raise ValueError(
                    "qc is not valid on a file_parsing column; "
                    "set qc per output inside file_parsing[].qc instead"
                )
        return self

    @property
    def output_column_name(self) -> str:
        """
        The column's name in the output table.

        A file_parsing column uses `generated_output_column_names()` instead.

        Returns:
            The output_column if given, else the input_column.
        """
        return self.output_column or self.input_column

    @property
    def expands_expensively(self) -> bool:
        """
        Whether producing this column's output value(s) costs more than string work.

        `file_parsing` spawns a subprocess (and maybe a download) per cell. Callers
        use this to decide whether it's worth reading the input an extra time to
        settle run-level QC before paying that cost -- see layout.build_layout().

        Returns:
            True if expanding this column for one row is expensive.
        """
        return self.file_parsing is not None

    @property
    def generated_output_column_names(self) -> list[str]:
        """
        Every output name this column contributes to the output header.

        Returns:
            One name per file_parsing output, or just `output_column_name` for a column
            that doesn't use file_parsing.
        """
        if self.file_parsing is not None:
            return [o.output_column for o in self.file_parsing]
        return [self.output_column_name]


class SetQCMatch(_StrictModel):
    """
    Identifies which sample(s) a `set_qc` rule applies to.

    Attributes:

        Only one of the following attributes is accepted at a time:

        - sample_pattern: a case-sensitive substring match against sample name
        - sample_regex: `re.search` against the sample name, case-sensitive (use an
          inline `(?i)` flag for case-insensitive matching).
        - samples: an explicit, exact list of sample names.
    """

    sample_pattern: str | None = None
    sample_regex: str | None = None
    samples: list[str] | None = None

    @model_validator(mode="after")
    def _only_one_match_method(self) -> "SetQCMatch":
        """
        Confirms only one valid match method is being used.

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

    # applies_to() runs once per row per rule, so the set and the compiled pattern
    # are built on first use rather than per row. _only_one_match_method() has
    # already proven the regex compiles.
    @functools.cached_property
    def _sample_set(self) -> frozenset[str]:
        """
        The `samples` list as a set, for O(1) membership from applies_to().
        """
        return frozenset(self.samples or ())

    @functools.cached_property
    def _compiled_regex(self) -> re.Pattern[str] | None:
        """
        The compiled `sample_regex`, or None when a different matcher is configured.
        """
        return re.compile(self.sample_regex) if self.sample_regex is not None else None

    def applies_to(self, sample: str) -> bool:
        """
        True if the qc match rule applies to this sample.

        Args:
            sample: the sample name to test.

        Returns:
            True if whichever match method was configured selects a `sample`.
        """
        if self.sample_pattern is not None:
            return self.sample_pattern in sample
        if self._compiled_regex is not None:
            return self._compiled_regex.search(sample) is not None
        # confirm that the list of samples isn't "None"
        assert self.samples is not None
        return sample in self._sample_set


class SetQCCheck(_StrictModel):
    """
    One column and QC condition list within a `SetQCRule`.

    A rule can list multiple conditions, checked against the same matched sample(s).

    Attributes:
        - input_column: the column on which to apply the QC check
        - qc: the qc to perform (no ConditionalQC permitted at this time)
    """

    input_column: str = Field(min_length=1)
    qc: list[QCCondition] = Field(min_length=1)


class SetQCRule(_StrictModel):
    """
    A run-level (set) QC check in which every sample identified by `match_samples`
    must pass every check in `checks` or else the entire run is considered a QC fail.

    A rule that matches zero samples in a given run is a LIMSport error, not a QC.

    Attributes:
        - rule_name: the name of the rule
        - match_samples: the samples to match
        - checks: the list of QC checks to apply to the matched samples
    """

    rule_name: str = Field(min_length=1)
    match_samples: SetQCMatch
    checks: list[SetQCCheck] = Field(min_length=1)

    @field_validator("checks")
    @classmethod
    def _no_duplicate_check_columns_permitted(
        cls, checks: list[SetQCCheck]
    ) -> list[SetQCCheck]:
        """
        One set_qc rule can only check a given input_column once.

        Args:
            checks: the rule's checks being validated.

        Returns:
            `checks` unchanged

        Raises:
            ValueError: if two checks in the rule name the same input_column.
        """
        _reject_duplicates(
            (c.input_column for c in checks), "column(s) within one set_qc rule"
        )
        return checks


class ExportConfig(_StrictModel):
    """
    The top-level shape of the YAML config file: a list of columns, plus optional
    run-level (`set_qc`) checks.

    Attributes:
        - columns: the list of columns to keep in the output with any modifications/qc checks
        - set_qc: a list of set-level qc rules to apply
    """

    columns: list[ColumnConfig] | None = None
    set_qc: list[SetQCRule] = Field(default_factory=list)

    @field_validator("set_qc")
    @classmethod
    def _validate_set_qc(cls, set_qc: list[SetQCRule]) -> list[SetQCRule]:
        """
        Every set_qc rule needs its own name.

        Args:
            set_qc: the run-level rules being validated.

        Returns:
            `set_qc` unchanged

        Raises:
            ValueError: if two rules share a name.
        """
        _reject_duplicates((rule.rule_name for rule in set_qc), "set_qc rule name(s)")
        return set_qc

    @model_validator(mode="after")
    def _validate_columns(self) -> "ExportConfig":
        """
        Checks that the config asks for something and that output names don't collide.

        Returns:
            `self` unchanged

        Raises:
            ValueError: if the config configures nothing, or two columns claim the same
              output name.
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

        _reject_duplicates(
            (c.input_column for c in self.columns), "column name(s) in config"
        )

        _reject_duplicates(
            (name for c in self.columns for name in c.generated_output_column_names),
            "output column name(s) in config",
        )
        return self


def load_config(path: Path) -> ExportConfig:
    """
    Reads and validates a YAML config file

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

    # try to validate config
    try:
        return ExportConfig.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"{path}: invalid config: {e}") from e
