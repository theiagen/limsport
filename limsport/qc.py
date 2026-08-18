"""QC evaluation: given a cell's raw string value and a configured
condition/column/sample, decide pass or fail and why.
"""

import operator as op
from typing import NamedTuple

from .config import QCCondition, QCFailure, QCOperator, QCOutcome

# ordering comparisons are matched to the stdlib operator functions.
_ORDERING_OPS = {
    QCOperator.GT: op.gt,
    QCOperator.GE: op.ge,
    QCOperator.LE: op.le,
    QCOperator.LT: op.lt,
}


def _format_number(x: float) -> str:
    """Format a float for a failure message without scientific notation."""
    return f"{x:,.6f}".rstrip("0").rstrip(".")


def _fold(value: str, case_insensitive: bool) -> str:
    return value.casefold() if case_insensitive else value


def _evaluate_contains(raw: str, condition: QCCondition, *, negate: bool) -> tuple[bool, str | None]:
    assert isinstance(condition.value, str)
    haystack = _fold(raw, condition.case_insensitive)
    needle = _fold(condition.value, condition.case_insensitive)
    contains = needle in haystack
    passed = (not contains) if negate else contains
    if passed:
        return True, None
    verb = "contains" if negate else "does not contain"
    return False, f"value {raw!r} {verb} {condition.value!r}"


def evaluate_condition(cell: str | None, condition: QCCondition) -> tuple[bool, str | None]:
    """Check one cell against one condition.

    Returns (passed, reason_if_failed) — reason is always a string when
    passed is False, and always None when passed is True.
    """
    raw = (cell or "").strip()

    if condition.operator is QCOperator.IS_EMPTY:
        passed = not raw
        return passed, None if passed else f"value {raw!r} is not empty"
    if condition.operator is QCOperator.IS_NOT_EMPTY:
        passed = bool(raw)
        return passed, None if passed else "missing value"

    if not raw:
        return False, "missing value"

    if condition.operator is QCOperator.CONTAINS:
        return _evaluate_contains(raw, condition, negate=False)
    if condition.operator is QCOperator.DOES_NOT_CONTAIN:
        return _evaluate_contains(raw, condition, negate=True)

    if isinstance(condition.value, str):  # only equivalence for strings
        passed = _fold(raw, condition.case_insensitive) == _fold(condition.value, condition.case_insensitive)
        return passed, None if passed else f"value {raw!r} != {condition.value!r}"

    try:
        # cast the raw value to a float
        actual = float(raw)
    except ValueError:
        # cells that can't be cast to a numbers are QC fails
        return False, (
            f"non-numeric value {raw!r} cannot be compared with "
            f"{condition.operator.value} {condition.value}"
        )

    # cast the config value, so "5" and 5.0 compare equal
    expected = float(condition.value)

    if condition.operator is QCOperator.APPROX:
        # assert-only: config validation already guarantees this
        assert condition.tolerance_percent is not None

        tolerance = abs(expected) * (condition.tolerance_percent / 100)
        passed = abs(actual - expected) <= tolerance
        if passed:
            return True, None
        return False, (
            f"{actual} is not within {condition.tolerance_percent:g}% of {_format_number(expected)} "
            f"(allowed range {_format_number(expected - tolerance)} to {_format_number(expected + tolerance)})"
        )

    if condition.operator is QCOperator.EQ:
        passed = (actual == expected)
    else:
        passed = _ORDERING_OPS[condition.operator](actual, expected)
    return passed, None if passed else f"{actual} {condition.operator.value} {condition.value} is False"


class ResolvedField(NamedTuple):
    """One value bound for a row, ready for QC: which source column it
    came from, its output name in the output header, its resolved
    value, and the QC conditions (if any) to check it against.

    `unmatched_reason` is set only for a conditional `qc` whose row
    matched no rule and has no default
    """

    column: str
    output_column: str
    value: str
    qc: list[QCCondition]
    unmatched_reason: str | None = None


def evaluate_field(field: ResolvedField, sample: str) -> QCFailure | None:
    """Check one resolved field against its own QC conditions (&&)

    Exits on the first failing condition
    """
    for condition in field.qc:
        passed, reason = evaluate_condition(field.value, condition)
        if not passed:
            # confirm reason is a string when fail
            assert reason is not None
            return QCFailure(
                sample=sample,
                column=field.column,
                output_column=field.output_column,
                operator=condition.operator,
                expected=condition.value,
                actual=field.value,
                reason=reason,
            )
    return None


def evaluate_row(fields: list[ResolvedField], sample: str) -> QCOutcome:
    """Check every QC-configured field for one sample's row."""
    failures: list[QCFailure] = []
    for field in fields:
        if field.unmatched_reason is not None:
            failures.append(
                QCFailure(
                    sample=sample,
                    column=field.column,
                    output_column=field.output_column,
                    operator=None,
                    expected=None,
                    actual=field.value,
                    reason=field.unmatched_reason,
                )
            )
            continue
        if not field.qc:
            continue  # fields with no QC rules are never evaluated or cast
        failure = evaluate_field(field, sample)

        if failure is not None:
            failures.append(failure)

    return QCOutcome(sample=sample, passed=not failures, failures=failures)
