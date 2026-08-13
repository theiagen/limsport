"""QC evaluation: given a cell's raw string value and a configured
condition/column/sample, decide pass or fail and why.
"""

import operator as op

from .config import ColumnConfig, QCCondition, QCFailure, QCOperator, QCOutcome

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


def evaluate_condition(cell: str | None, condition: QCCondition) -> tuple[bool, str | None]:
    """Check one cell against one condition.

    Returns (passed, reason_if_failed) — reason is always a string when
    passed is False, and always None when passed is True.
    """
    raw = (cell or "").strip()
    if not raw:
        return False, "missing value"

    if isinstance(condition.value, str):
        # case-sensitive
        passed = (raw == condition.value)
        return passed, None if passed else f"value {raw!r} != {condition.value!r}"

    try:
        actual = float(raw)
    except ValueError:
        # A cell that can't be cast to a number is considered a QC fail
        return False, (
            f"non-numeric value {raw!r} cannot be compared with "
            f"{condition.operator.value} {condition.value}"
        )

    # cast the config value, so "5" and 5.0 compare equal
    expected = float(condition.value)

    if condition.operator is QCOperator.APPROX:
        # double-check this, is already checked in config validation
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


def evaluate_column(cell: str | None, column: ColumnConfig, sample: str) -> QCFailure | None:
    """Check one cell against all of a column's conditions (&&)

    Exits on the first failing condition
    """
    for condition in column.qc:
        passed, reason = evaluate_condition(cell, condition)
        if not passed:
            # confirm reason is a string when fail
            assert reason is not None
            return QCFailure(
                sample=sample,
                column=column.name,
                output_column=column.output_name,
                operator=condition.operator,
                expected=condition.value,
                actual=cell,
                reason=reason,
            )
    return None


def evaluate_sample(row: dict[str, str], sample: str, columns: list[ColumnConfig]) -> QCOutcome:
    """Check every QC-configured column for one sample's row."""
    failures: list[QCFailure] = []
    for column in columns:
        if not column.qc:
            continue  # columns with no QC rules are never evaluated or cast
        failure = evaluate_column(row.get(column.name), column, sample)

        if failure is not None:
            failures.append(failure)

    return QCOutcome(sample=sample, passed=not failures, failures=failures)
