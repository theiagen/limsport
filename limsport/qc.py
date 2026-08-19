"""
This module performs QC evaluation; given a cell's raw string value and a configured condition/column/sample, it will decide if that row passes or fails and why.

Three classes are included:
    - QCFailure
    - QCResult
    - QCInput

Included external methods:
    - evaluate_condition()
    - evaluate_row()
"""

import operator as op
from typing import NamedTuple

from pydantic import BaseModel, Field

from .config import QCCondition, QCOperator

# ordering comparisons are matched to the stdlib operator functions.
_ORDERING_OPS = {
    QCOperator.GT: op.gt,
    QCOperator.GE: op.ge,
    QCOperator.LE: op.le,
    QCOperator.LT: op.lt,
}


class QCFailure(BaseModel):
    """
    One sample's failing check on one output, reported both as a log line and a row in
    the --qc-report TSV.

    `operator`, `expected`, and `actual` can all be None

    Attributes:
        sample: the name of the sample
        column: the original column name from the input
        output_column: the column name to output
        operator: the QC operator ('=', '>=', etc.)
        expected: the expected value (left hand of the operation)
        actual: the actual value from the row (right hand of the operation)
        reason: the reason why the sample failed the QC operation
    """

    sample: str
    column: str
    output_column: str
    operator: QCOperator | None
    expected: int | float | str | bool | None
    actual: str | None
    reason: str

    def to_list(self):
        """
        Returns the contents of QCFailure as a list
        """
        return [
            self.sample,
            self.column,
            self.output_column,
            self.operator.value if self.operator is not None else "",
            str(self.expected) if self.expected is not None else "",
            self.actual or "",
            self.reason,
        ]


class QCResult(BaseModel):
    """
    The result of evaluating every configured QC rule against one sample's row.

    Attributes:
        sample: the name of the sample
        passed: whether or not the sample passed QC (true = pass, false = fail)
        failures: a list of any QCFailure objects,
    """

    sample: str
    passed: bool
    failures: list[QCFailure] = Field(default_factory=list)


class NoMatchingRule(NamedTuple):
    """
    Stands in for a QCInput's QC conditions when a conditional `qc` had no rule
    matching this row and no `default` configured.

    The QCInput fails before any condition is evaluated and contains only the reason.
    Using this instead of an empty condition list keeps "nothing to check" different
    from "failed to match a rule" which would both look like [].

    Attributes:
        reason: the reason why this sample had no matching rule.
    """

    reason: str


class QCInput(NamedTuple):
    """
    A row ready for QC. This contains which source column it came from, its output name
    in the output header, its value, and the QC conditions to check it against.

    Attributes:
        column: the original column name from the input
        output_column: the column name to output
        value: the content of the row at column
        qc: a list of QC operations to perform, or an indication that no QC operations could be identified for the row.
    """

    column: str
    output_column: str
    value: str
    qc: list[QCCondition] | NoMatchingRule

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
            column=self.column,
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

    if substring in original_string:
        if negated:
            # condition was "does not contain" but it was found :(
            return False, f"value {raw_value!r} contains {condition.value!r}"
        else:
            # condition was "contains" and it was found :)
            return True, None
    elif negated:
        # condition was "does not contain" and it was NOT found :)
        return True, None

    # condition was "contains" and it was NOT found :(
    return False, f"value {raw_value!r} does not contain {condition.value!r}"


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

    if condition.operator is QCOperator.EQ:
        if raw_number == expected_number:
            return True, None

    else:
        if _ORDERING_OPS[condition.operator](raw_number, expected_number):
            return True, None

    return False, f"{raw_number} {condition.operator.value} {condition.value} is False"


def evaluate_qc_input(qc_input: QCInput, sample: str) -> QCFailure | None:
    """Check one QCInput against its own QC conditions (&&)

    Exits on the first failing condition. `NoMatchingRule` QCInputs have already failed and
    shouldn't be seen here

    Args:
      qc_input: the input to check; its `qc` must not be NoMatchingRule.
      sample: the sample name to record on any failure.

    Returns:
      The first QCFailure found, or None if every condition passed.
    """
    # confirm that the qc_input.qc is not `NoMatchingRule`
    assert not isinstance(qc_input.qc, NoMatchingRule)
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
        if isinstance(qc_input.qc, NoMatchingRule):
            # no rule matched, so there's no condition to evaluate or report
            failures.append(qc_input.to_failure(sample, qc_input.qc.reason))
            continue
        if not qc_input.qc:
            continue  # QCInputs with no QC rules are never evaluated or cast
        failure = evaluate_qc_input(qc_input, sample)

        if failure is not None:
            failures.append(failure)

    return QCResult(sample=sample, passed=not failures, failures=failures)
