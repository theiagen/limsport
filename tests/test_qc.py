import pytest

from limsport.config import ColumnConfig
from limsport.qc import evaluate_column, evaluate_condition, evaluate_sample


def _condition(operator, value):
    return ColumnConfig.model_validate(
        {"name": "x", "qc": [{"operator": operator, "value": value}]}
    ).qc[0]


def _approx_condition(value, tolerance_percent):
    return ColumnConfig.model_validate(
        {
            "name": "x",
            "qc": [{"operator": "~=", "value": value, "tolerance_percent": tolerance_percent}],
        }
    ).qc[0]


@pytest.mark.parametrize(
    "operator,value,cell,expected",
    [
        (">", 10, "11", True),
        (">", 10, "10", False),
        (">=", 10, "10", True),
        (">=", 10, "9", False),
        ("<=", 10, "10", True),
        ("<=", 10, "11", False),
        ("<", 10, "9", True),
        ("<", 10, "10", False),
        ("=", 10, "10", True),
        ("=", 10, "10.0", True),
        ("=", 10, "11", False),
        # negative numbers, both sides
        (">", -10, "-5", True),
        (">", -10, "-15", False),
        ("<", -10, "-15", True),
        ("=", -10, "-10", True),
        (">=", -10, "-10", True),
    ],
)
def test_numeric_operators(operator, value, cell, expected):
    condition = _condition(operator, value)
    passed, reason = evaluate_condition(cell, condition)
    assert passed is expected
    if not expected:
        assert reason


def test_range_semantics_below_within_above():
    column = ColumnConfig.model_validate(
        {
            "name": "read_count",
            "qc": [{"operator": ">=", "value": 1000}, {"operator": "<=", "value": 1000000}],
        }
    )
    assert evaluate_column("500", column, "S1") is not None  # below range: fails
    assert evaluate_column("5000", column, "S1") is None  # within range: passes
    assert evaluate_column("2000000", column, "S1") is not None  # above range: fails


@pytest.mark.parametrize(
    "cell,expected",
    [
        ("1000", True),  # exact match
        ("900", True),  # lower boundary, inclusive
        ("1100", True),  # upper boundary, inclusive
        ("899", False),  # just below the lower boundary
        ("1101", False),  # just above the upper boundary
        ("500", False),  # far below
    ],
)
def test_approx_operator_tolerance_boundaries(cell, expected):
    # value=1000, tolerance_percent=10 -> passing range is [900, 1100]
    condition = _approx_condition(1000, 10)
    passed, reason = evaluate_condition(cell, condition)
    assert passed is expected
    if not expected:
        assert reason is not None and "not within 10%" in reason


def test_approx_operator_non_numeric_cell_fails_without_raising():
    condition = _approx_condition(1000000, 5)
    passed, reason = evaluate_condition("NA", condition)
    assert passed is False
    assert reason is not None and "non-numeric" in reason


@pytest.mark.parametrize(
    "cell,expected",
    [
        ("-1000000", True),  # exact match against a negative expected value
        ("-950000", True),  # upper boundary (less negative), inclusive
        ("-1050000", True),  # lower boundary (more negative), inclusive
        ("-949999", False),  # just outside the upper boundary
        ("-1050001", False),  # just outside the lower boundary
    ],
)
def test_approx_operator_handles_negative_expected_value(cell, expected):
    # value=-1000000, tolerance_percent=5 -> passing range is [-1050000, -950000].
    # tolerance has to be a magnitude, not inherit the sign of a negative
    # expected value -- otherwise even an exact match would fail.
    condition = _approx_condition(-1000000, 5)
    passed, _ = evaluate_condition(cell, condition)
    assert passed is expected


def test_approx_operator_zero_expected_value_only_matches_zero():
    # 5% of 0 is 0, so the passing range collapses to exactly {0}.
    condition = _approx_condition(0, 5)
    assert evaluate_condition("0", condition)[0] is True
    assert evaluate_condition("0.1", condition)[0] is False


def test_string_equality_is_case_sensitive():
    condition = _condition("=", "PASS")
    passed, _ = evaluate_condition("pass", condition)
    assert passed is False
    passed, _ = evaluate_condition("PASS", condition)
    assert passed is True


def test_non_numeric_cell_against_numeric_condition_fails_without_raising():
    condition = _condition(">=", 1000)
    passed, reason = evaluate_condition("NA", condition)
    assert passed is False
    assert reason is not None and "non-numeric" in reason


def test_empty_cell_fails_with_missing_value_reason():
    condition = _condition(">=", 1000)
    passed, reason = evaluate_condition("", condition)
    assert passed is False
    assert reason == "missing value"

    passed, reason = evaluate_condition(None, condition)
    assert passed is False
    assert reason == "missing value"


def test_whitespace_only_cell_treated_as_missing_value():
    # " ".strip() == "", so whitespace-only cells are missing values too,
    # not (e.g.) a value that fails to cast to a number.
    condition = _condition(">=", 1000)
    passed, reason = evaluate_condition("   ", condition)
    assert passed is False
    assert reason == "missing value"


def test_column_with_no_qc_always_passes():
    column = ColumnConfig.model_validate({"name": "notes"})
    assert evaluate_column("anything", column, "S1") is None
    assert evaluate_column("", column, "S1") is None


def test_evaluate_sample_aggregates_failures_across_columns():
    columns = [
        ColumnConfig.model_validate(
            {"name": "read_count", "qc": [{"operator": ">=", "value": 1000}]}
        ),
        ColumnConfig.model_validate({"name": "status", "qc": [{"operator": "=", "value": "PASS"}]}),
    ]
    row = {"read_count": "500", "status": "FAIL"}
    outcome = evaluate_sample(row, "S1", columns)
    assert outcome.passed is False
    assert {f.column for f in outcome.failures} == {"read_count", "status"}


def test_evaluate_sample_all_pass():
    columns = [
        ColumnConfig.model_validate(
            {"name": "read_count", "qc": [{"operator": ">=", "value": 1000}]}
        )
    ]
    outcome = evaluate_sample({"read_count": "5000"}, "S1", columns)
    assert outcome.passed is True
    assert outcome.failures == []
