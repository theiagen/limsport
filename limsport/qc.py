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
    """Format a float for a failure message and avoid scientific notation"""
    return f"{x:,.6f}".rstrip("0").rstrip(".")


def _remove_case(value: str, case_insensitive: bool) -> str:
    """Turns a string caseless with .casefold()"""
    # using casefold instead of lower b/c it's better for non-ascii characters
    # think of the fancy ê's in the world!!!
    if case_insensitive:
        return value.casefold()
    else:
        return value


def _evaluate_contains(
    raw_value: str, condition: QCCondition, *, negated: bool
) -> tuple[bool, str | None]:
    """Evalutes the contains and does_not_contain operators.
    Returns (passed, reason_if_passed)"""
    # double check the value is a string
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
    """Check one cell against one condition.

    Returns (passed, reason_if_failed) — reason is always a string when
    passed is False, and always None when passed is True.
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
            # confirm reason has content
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
