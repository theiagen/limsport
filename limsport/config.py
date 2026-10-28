"""
Pydantic models for the YAML export config, plus the loader that turns a
config file on disk into a validated ExportConfig.

The config's `sample_qc` list (formerly `columns`, still accepted) works as a column
allow-list: if one is given, only the columns listed in it make it into the output. Each
column can also be given a different output name and QC rules. `sample_qc` can be
omitted entirely if the config only exists for its `set_qc` (run-level) rules

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
    AliasChoices,
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
        """Check that this operator is compatible with both `value` and any modifiers

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
            raise ValueError(  # noqa: TRY004 -- we want this to fail as a config error
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
            # string operators can't do numerical comparisons
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

    A row's `match_column` value is looked up in `cases` first, then in `pattern_cases`,
    then in `regex_cases`; within the last two, the first match in config order wins.
    A row matching none of them uses `default` if given. Without a `default`, it's
    reported as a QC fail

    Attributes:
        - match_column: the name of the column to match on
        - cases: exact, case-sensitive match values and their QCCondition(s)
        - pattern_cases: case-sensitive substrings and their QCCondition(s)
        - regex_cases: `re.search` patterns and their QCCondition(s)
        - default: the default QCCondition(s) to apply if no match was found
    """

    match_column: str = Field(min_length=1)
    cases: dict[str, list[QCCondition]] = Field(default_factory=dict)
    pattern_cases: dict[str, list[QCCondition]] = Field(default_factory=dict)
    regex_cases: dict[str, list[QCCondition]] = Field(default_factory=dict)
    default: list[QCCondition] | None = None

    @model_validator(mode="after")
    def _cases_are_not_empty(self) -> "ConditionalQC":
        """
        A conditional `qc` has to configure at least one case, and every regex must
        compile.

        Returns:
            `self` unchanged

        Raises:
            ValueError: if `cases`, `pattern_cases`, and `regex_cases` are all empty, or
              a `regex_cases` key isn't a valid regex.
        """
        if not (self.cases or self.pattern_cases or self.regex_cases):
            raise ValueError(
                "conditional qc must configure at least one of cases, pattern_cases, or regex_cases"
            )
        for pattern in self.regex_cases:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"invalid regex_cases key {pattern!r}: {e}") from e
        return self

    # conditions_for() runs once per row, so the patterns are compiled on first use.
    # _cases_are_not_empty() has already proven they compile.
    @functools.cached_property
    def _compiled_regex_cases(self) -> list[tuple[re.Pattern[str], list[QCCondition]]]:
        """
        The `regex_cases` keys compiled, paired with their conditions, in config order.
        """
        return [(re.compile(k), v) for k, v in self.regex_cases.items()]

    def conditions_for(self, value: str) -> list[QCCondition] | None:
        """
        Picks the conditions for one row's `match_column` value.

        Args:
            value: the row's value in `match_column`.

        Returns:
            The first matching case's conditions, checked exact, then pattern, then
            regex; else `default`, which is None when not configured.
        """
        if value in self.cases:
            return self.cases[value]
        for pattern, conditions in self.pattern_cases.items():
            if pattern in value:
                return conditions
        for regex, conditions in self._compiled_regex_cases:
            if regex.search(value) is not None:
                return conditions
        return self.default


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

    `qc` can be either a plain list[QCCondition], or the ConditionalQC form.

    Attributes:
        - output_column: the name of the output column that will be generated
        - command: the parsing command to perform on the file to generate the output
        - timeout_seconds: how long to try the command before giving up
        - qc: the list of QC to perform on the generated output
    """

    output_column: str = Field(min_length=1)
    command: str = Field(min_length=1)
    timeout_seconds: float | None = None
    qc: list[QCCondition] | ConditionalQC = Field(default_factory=list)

    @field_validator("command")
    @classmethod
    def _command_is_not_blank(cls, command: str) -> str:
        """
        `command` should not be blank.

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
    An entry in the config's `sample_qc` list: one column to keep in the output, with an
    optional output name and QC rules.

    Attributes:
        - input_column: the name of the input column
        - output_column: the name of the output column
        - output: whether this column's value is written to the output file
        - qc: the QC to perform on the column content; either a plain list[QCCondition]
          or the ConditionalQC form
        - file_parsing: if this column requires file parsing
    """

    input_column: str
    output_column: str | None = None
    output: bool = True
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

    @model_validator(mode="after")
    def _output_false_excludes_output_column_and_file_parsing(self) -> "ColumnConfig":
        """
        A column excluded from the output (output=False) can't also name an
        output_column or use file_parsing.

        Returns:
            `self` unchanged

        Raises:
            ValueError: if a column with output=False also sets `output_column` or
              `file_parsing`.
        """
        if not self.output:
            if self.output_column is not None:
                raise ValueError(
                    "output_column is not valid when output is False; "
                    "the column never reaches the output, so it has no output name"
                )
            if self.file_parsing is not None:
                raise ValueError(
                    "file_parsing is not valid when output is False; "
                    "file_parsing already names its own output column(s)"
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
    def has_qc(self) -> bool:
        """
        Whether this column checks anything at sample level.

        Returns:
            True if the column's own `qc`, or any of its file_parsing outputs' `qc`, is
            set.
        """
        if self.file_parsing is not None:
            return any(_qc_is_set(output.qc) for output in self.file_parsing)
        return _qc_is_set(self.qc)

    @property
    def contains_file_parsing(self) -> bool:
        """
        Whether producing this column's output value(s) are generated via file parsing

        Used because `file_parsing` spawns a subprocess (and maybe a download) per cell,
        which is used to determine if a pre-process of set_qc would be beneficial.

        Returns:
            True if this column has file parsing, which is computationally expensive
        """
        return self.file_parsing is not None

    @property
    def generated_output_column_names(self) -> list[str]:
        """
        Every output name this column contributes to the output header.

        Returns:
            One name per file_parsing output, or just `output_column_name` for a column
            that doesn't use file_parsing. Empty when `output` is False.
        """
        if not self.output:
            return []
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
        # make list of provided methods
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
                # make sure regex is valid
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
        - qc: the qc to perform; either a plain list[QCCondition] or the ConditionalQC
          form
    """

    input_column: str = Field(min_length=1)
    qc: list[QCCondition] | ConditionalQC

    @field_validator("qc")
    @classmethod
    def _qc_is_not_empty(
        cls, qc: list[QCCondition] | ConditionalQC
    ) -> list[QCCondition] | ConditionalQC:
        """
        A set_qc check has to check something.

        Args:
            qc: the check's configured qc.

        Returns:
            `qc` unchanged

        Raises:
            ValueError: if `qc` is an empty list.
        """
        if not _qc_is_set(qc):
            raise ValueError("set_qc check qc must not be empty")
        return qc


class SetQCRule(_StrictModel):
    """
    A run-level (set) QC check in which every sample identified by `match_samples`
    must pass every check in `checks` (and, with `include_sample_qc`, every per-row
    `qc` in `sample_qc`) or else the entire run is considered a QC fail.

    A rule that matches zero samples in a given run is a LIMSport error, not a QC.

    Attributes:
        - rule_name: the name of the rule
        - match_samples: the samples to match
        - checks: the list of QC checks to apply to the matched samples
        - include_sample_qc: whether a matched sample failing its per-row `qc` also
          fails the run
    """

    rule_name: str = Field(min_length=1)
    match_samples: SetQCMatch
    checks: list[SetQCCheck] = Field(default_factory=list)
    include_sample_qc: bool = False

    @model_validator(mode="after")
    def _checks_something(self) -> "SetQCRule":
        """
        A rule needs at least one check, or include_sample_qc to check with.

        Returns:
            `self` unchanged

        Raises:
            ValueError: if `checks` is empty and `include_sample_qc` is off.
        """
        if not self.checks and not self.include_sample_qc:
            raise ValueError(
                f"set_qc rule {self.rule_name!r} must list at least one check, or set include_sample_qc: true"
            )
        return self

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
    run-level (`set_qc`) checks and samples that skip sample-level QC.

    Attributes:
        - sample_qc: the list of columns to keep in the output with any modifications/qc
          checks; read from `columns` in older configs
        - set_qc: a list of set-level qc rules to apply
        - skip_sample_qc: the samples (e.g. negative controls) that skip per-row `qc`
          but are still checked by `set_qc`, or None if every sample gets per-row `qc`
    """

    sample_qc: list[ColumnConfig] | None = Field(
        default=None, validation_alias=AliasChoices("sample_qc", "columns")
    )
    set_qc: list[SetQCRule] = Field(default_factory=list)
    skip_sample_qc: SetQCMatch | None = None

    @model_validator(mode="before")
    @classmethod
    def _not_both_sample_qc_and_columns(cls, data: object) -> object:
        """
        `columns` is the old name for `sample_qc`, so a config can't use both.

        Args:
            data: the raw config mapping.

        Returns:
            `data` unchanged

        Raises:
            ValueError: if both `sample_qc` and `columns` are given.
        """
        if isinstance(data, dict) and "sample_qc" in data and "columns" in data:
            raise ValueError(
                "config sets both 'sample_qc' and 'columns'; 'columns' is the old name for 'sample_qc', so use only 'sample_qc'"
            )
        return data

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
            ValueError: if the config configures nothing, every column is excluded from
              the output, or two columns claim the same output name.
        """
        if self.sample_qc is None:
            if not self.set_qc:
                raise ValueError(
                    "config must configure at least one of 'sample_qc' or 'set_qc' (an empty config does nothing)"
                )
            return self

        if not self.sample_qc:
            raise ValueError(
                "config 'sample_qc' must not be empty; omit it entirely if you don't want to perform any column operations"
            )

        if not any(c.output for c in self.sample_qc):
            raise ValueError(
                "config 'sample_qc' must include at least one column with output=True "
                "(an all-hidden list would produce an output table with no columns); "
                "omit 'sample_qc' entirely if you only want set_qc"
            )

        _reject_duplicates(
            (c.input_column for c in self.sample_qc), "column name(s) in config"
        )

        _reject_duplicates(
            (name for c in self.sample_qc for name in c.generated_output_column_names),
            "output column name(s) in config",
        )
        return self

    @model_validator(mode="after")
    def _include_sample_qc_has_qc_to_include(self) -> "ExportConfig":
        """
        A set_qc rule with include_sample_qc needs some sample-level QC to include.

        Returns:
            `self` unchanged

        Raises:
            ValueError: if a rule sets include_sample_qc but no `sample_qc` column has
              any `qc`.
        """
        if any(c.has_qc for c in self.sample_qc or []):
            return self
        names = [rule.rule_name for rule in self.set_qc if rule.include_sample_qc]
        if names:
            raise ValueError(
                f"set_qc rule(s) {names} set include_sample_qc, but no column in sample_qc has qc to include"
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
