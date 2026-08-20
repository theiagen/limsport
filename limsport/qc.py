"""
This module performs QC evaluation; given a cell's raw string value and a configured condition/column/sample, it will decide if that row passes or fails and why.

Three classes are included:
    - QCFailure
    - QCResult
    - QCInput

Plus the stand-ins for "this output could not be checked":
    - UncheckableOutput (base)
    - NoMatchingRule
    - ParsingFailed

Included external methods:
    - evaluate_condition()
    - evaluate_row()

Included constants:
    - REPORT_HEADER -- the --qc-report TSV's columns, in QCFailure.to_list() order
"""

import operator as op
from dataclasses import dataclass
from typing import NamedTuple

from pydantic import BaseModel

from .config import QCCondition, QCOperator

# numeric comparisons are matched to the stdlib operator functions.
_COMPARISON_OPS = {
    QCOperator.GT: op.gt,
    QCOperator.GE: op.ge,
    QCOperator.EQ: op.eq,
    QCOperator.LE: op.le,
    QCOperator.LT: op.lt,
}


# The --qc-report TSV's columns. Kept here because QCFailure.to_list() emits its
# cells in this exact order; the two have to be changed together.
REPORT_HEADER = [
    "sample",
    "input_column",
    "output_column",
    "operator",
    "expected",
    "actual",
    "reason",
]


class QCFailure(BaseModel):
    """
    One sample's failing check on one output, reported both as a log line and a row in
    the --qc-report TSV.

    `operator`, `expected`, and `actual` can all be None

    Attributes:
        sample: the name of the sample
        input_column: the original column name from the input
        output_column: the column name to output
        operator: the QC operator ('=', '>=', etc.)
        expected: the expected value (left hand of the operation)
        actual: the actual value from the row (right hand of the operation)
        reason: the reason why the sample failed the QC operation
    """

    sample: str
    input_column: str
    output_column: str
    operator: QCOperator | None
    expected: int | float | str | bool | None
    actual: str | None
    reason: str

    def to_list(self) -> list[str]:
        """
        Returns the contents of QCFailure as a list, in REPORT_HEADER order.
        """
        return [
            self.sample,
            self.input_column,
            self.output_column,
            self.operator.value if self.operator is not None else "",
            str(self.expected) if self.expected is not None else "",
            self.actual or "",
            self.reason,
        ]


class QCResult(NamedTuple):
    """
    The result of evaluating every configured QC rule against one sample's row.

    Attributes:
        failures: a list of any QCFailure objects; empty when the sample passed.
    """

    failures: list[QCFailure]

    @property
    def passed(self) -> bool:
        """
        True when the row cleared every QC condition it was checked against.
        """
        return not self.failures


@dataclass(frozen=True)
class UncheckableOutput:
    """
    Stands in for a QCInput's QC conditions when the output cannot be checked at all.

    The QCInput fails before any condition is evaluated and carries only the reason.
    Using this instead of an empty condition list keeps "nothing to check" distinct
    from "could not be checked", which would both otherwise look like [].

    That distinction is what makes an unproducible value fail its row even when no
    `qc` is configured on it: evaluate_row() tests for this before it skips inputs
    with no conditions.

    Attributes:
        reason: why this output could not be checked, reported to the user as-is.
    """

    reason: str


@dataclass(frozen=True)
class NoMatchingRule(UncheckableOutput):
    """
    A conditional `qc` had no rule matching this row and no `default` configured.
    """


@dataclass(frozen=True)
class ParsingFailed(UncheckableOutput):
    """
    file_parsing could not produce this output's value for this row, so there is no
    value to check or to write. The row fails QC and is left out of the output; the
    run carries on with the rows whose files did parse.
    """


class QCInput(NamedTuple):
    """
    A row ready for QC. This contains which source column it came from, its output name
    in the output header, its value, and the QC conditions to check it against.

    Attributes:
        input_column: the original column name from the input
        output_column: the column name to output
        value: the content of the row at column
        qc: a list of QC operations to perform, or an UncheckableOutput saying why
          this output could not be checked at all.
    """

    input_column: str
    output_column: str
    value: str
    qc: list[QCCondition] | UncheckableOutput

    def to_failure(
        self, sample: str, reason: str, condition: QCCondition | None = None
    ) -> QCFailure:
        """
        Reports this QCInput as a QC failure for `sample`.

        Args:
            sample: the sample name
            reason: the explanation of the failure
            condition: the specific QCCondition that failed

        Returns:
            A QCFailure describing this input's failure
        """
        return QCFailure(
            sample=sample,
            input_column=self.input_column,
            output_column=self.output_column,
            operator=None if condition is None else condition.operator,
            expected=None if condition is None else condition.value,
            actual=self.value,
            reason=reason,
        )


def _format_number(x: float) -> str:
    """
    Formats a float for a failure message and avoid scientific notation

    Args:
        x: the number to format

    Returns:
        The number as a string without trailing zeros
    """
    return f"{x:,.6f}".rstrip("0").rstrip(".")


def _remove_case(value: str, case_insensitive: bool) -> str:
    """
    Turns a string caseless with .casefold()

    Think of all the fancy ê's in the world!

    Args:
        value: the string to fold
        case_insensitive: whether to fold at all; False returns `value` unchanged.

    Returns:
        The folded string, or `value` untouched when case_insensitive is False.
    """
    if case_insensitive:
        return value.casefold()
    else:
        return value


def _evaluate_contains(
    raw_value: str, condition: QCCondition, *, negated: bool
) -> tuple[bool, str | None]:
    """
    Evaluates the `contains` and `does_not_contain` operators.

    Args:
        raw_value: the cell value to search
        condition: the QCCondition supplying the substring to look for
        negated: True for does_not_contain, False for contains.

    Returns:
        A tuple with the boolean result and the reason behind it (None if True)
    """
    assert isinstance(condition.value, str)
    original_string = _remove_case(raw_value, condition.case_insensitive)
    substring = _remove_case(condition.value, condition.case_insensitive)

    found = substring in original_string
    if found != negated:
        # "contains" and it was found, or "does not contain" and it wasn't :)
        return True, None
    verb = "contains" if found else "does not contain"
    return False, f"value {raw_value!r} {verb} {condition.value!r}"


def evaluate_condition(
    cell: str | None, condition: QCCondition
) -> tuple[bool, str | None]:
    """
    Checks one cell against one condition.

    Args:
        cell: the raw cell value, or None if the column was missing.
        condition: the single QCCondition to check it against.

    Returns:
        A tuple with the boolean result and the reason behind it (None if True)
    """
    raw_value = (cell or "").strip()

    # check for empty/not empty first -- otherwise empties fail QC
    if condition.operator is QCOperator.IS_EMPTY:
        if not raw_value:
            return True, None
        else:
            return False, f"value {raw_value!r} is not empty"
    if condition.operator is QCOperator.IS_NOT_EMPTY:
        if bool(raw_value):
            return True, None
        else:
            return False, "missing value"

    # fail QC if operator is not an empty/not empty
    if not raw_value:
        return False, "missing value"

    # string checks
    if condition.operator is QCOperator.CONTAINS:
        return _evaluate_contains(raw_value, condition, negated=False)
    if condition.operator is QCOperator.DOES_NOT_CONTAIN:
        return _evaluate_contains(raw_value, condition, negated=True)

    if isinstance(condition.value, str):
        # the only other option is equivalence
        if _remove_case(raw_value, condition.case_insensitive) == _remove_case(
            condition.value, condition.case_insensitive
        ):
            return True, None
        else:
            return (
                False,
                f"value {raw_value!r} != {condition.value!r} (case insensitive: {condition.case_insensitive})",
            )

    # numeric checks
    try:
        # cast the raw value to a float
        raw_number = float(raw_value)
    except ValueError:
        # cells that can't be cast to a numbers are QC fails
        return False, (
            f"non-numeric value {raw_value!r} cannot be compared with {condition.operator.value} {condition.value}"
        )

    # double-check, shouldn't ever be false
    assert condition.value is not None

    # cast the config value, so "5" and 5.0 compare equal
    expected_number = float(condition.value)

    if condition.operator is QCOperator.APPROX:
        # double-check
        assert condition.tolerance_percent is not None

        tolerance = abs(expected_number) * (condition.tolerance_percent / 100)
        if abs(raw_number - expected_number) <= tolerance:
            return True, None
        return (
            False,
            f"{raw_number} is not within {condition.tolerance_percent:g}% of {_format_number(expected_number)} (allowed range {_format_number(expected_number - tolerance)} to {_format_number(expected_number + tolerance)})",
        )

    if _COMPARISON_OPS[condition.operator](raw_number, expected_number):
        return True, None

    return False, f"{raw_number} {condition.operator.value} {condition.value} is False"


def evaluate_qc_input(qc_input: QCInput, sample: str) -> QCFailure | None:
    """Check one QCInput against its own QC conditions (&&)

    Exits on the first failing condition. An UncheckableOutput QCInput has already
    failed and shouldn't be seen here

    Args:
      qc_input: the input to check; its `qc` must not be UncheckableOutput.
      sample: the sample name to record on any failure.

    Returns:
      The first QCFailure found, or None if every condition passed.
    """
    # confirm that the qc_input.qc is a real condition list
    assert not isinstance(qc_input.qc, UncheckableOutput)
    for condition in qc_input.qc:
        passed, reason = evaluate_condition(qc_input.value, condition)
        if not passed:
            # confirm reason has content, then create QCFailure object
            assert reason is not None
            return qc_input.to_failure(sample, reason, condition)
    return None


def evaluate_row(qc_inputs: list[QCInput], sample: str) -> QCResult:
    """
    Checks every QC-configured QCInput for one sample's row.

    Args:
        qc_inputs: every QC check needed to perform on this row
        sample: the sample name

    Returns:
        A QCResult holding the pass/fail verdict and a list of every failure found.
    """
    failures: list[QCFailure] = []
    for qc_input in qc_inputs:
        if isinstance(qc_input.qc, UncheckableOutput):
            # nothing to evaluate -- the input already carries its own reason
            failures.append(qc_input.to_failure(sample, qc_input.qc.reason))
            continue
        if not qc_input.qc:
            continue  # QCInputs with no QC rules are never evaluated or cast
        failure = evaluate_qc_input(qc_input, sample)

        if failure is not None:
            failures.append(failure)

    return QCResult(failures=failures)
