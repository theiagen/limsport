import pytest

from limsport.config import ColumnConfig
from limsport.qc import ResolvedField, evaluate_condition, evaluate_field, evaluate_row


def _condition(operator, value):
    return ColumnConfig.model_validate(
        {"name": "x", "qc": [{"operator": operator, "value": value}]}
    ).qc[0]


def _string_condition(operator, value, case_insensitive=False):
    return ColumnConfig.model_validate(
        {"name": "x", "qc": [{"operator": operator, "value": value, "case_insensitive": case_insensitive}]}
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
    def _field(value):
        return ResolvedField(column.name, column.output_name, value, column.qc)

    assert evaluate_field(_field("500"), "S1") is not None  # below range: fails
    assert evaluate_field(_field("5000"), "S1") is None  # within range: passes
    assert evaluate_field(_field("2000000"), "S1") is not None  # above range: fails


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


def test_contains_operator_passes_on_substring():
    condition = _condition("contains", "Escherichia")
    passed, reason = evaluate_condition("Escherichia coli", condition)
    assert passed is True
    assert reason is None


def test_contains_operator_fails_when_substring_missing():
    condition = _condition("contains", "Escherichia")
    passed, reason = evaluate_condition("Salmonella enterica", condition)
    assert passed is False
    assert reason is not None and "does not contain" in reason


def test_contains_operator_is_case_sensitive_by_default():
    condition = _condition("contains", "Escherichia")
    passed, _ = evaluate_condition("escherichia coli", condition)
    assert passed is False


def test_contains_operator_case_insensitive_option():
    condition = _string_condition("contains", "Escherichia", case_insensitive=True)
    passed, _ = evaluate_condition("escherichia coli", condition)
    assert passed is True


def test_does_not_contain_operator_passes_when_substring_missing():
    condition = _condition("does_not_contain", "Escherichia")
    passed, reason = evaluate_condition("Salmonella enterica", condition)
    assert passed is True
    assert reason is None


def test_does_not_contain_operator_fails_when_substring_present():
    condition = _condition("does_not_contain", "Escherichia")
    passed, reason = evaluate_condition("Escherichia coli", condition)
    assert passed is False
    assert reason is not None and "contains" in reason


def test_does_not_contain_operator_case_insensitive_option():
    condition = _string_condition("does_not_contain", "Escherichia", case_insensitive=True)
    passed, _ = evaluate_condition("escherichia coli", condition)
    assert passed is False


def test_does_not_contain_operator_fails_on_blank_cell_same_as_other_operators():
    # A blank cell hits the "missing value" guard before any operator-specific
    # logic runs, same as every other operator -- including does_not_contain,
    # even though a blank cell technically "does not contain" the substring.
    condition = _condition("does_not_contain", "Escherichia")
    passed, reason = evaluate_condition("", condition)
    assert passed is False
    assert reason == "missing value"


def test_equality_operator_case_insensitive_option():
    condition = _string_condition("=", "PASS", case_insensitive=True)
    passed, _ = evaluate_condition("pass", condition)
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


def test_field_with_no_qc_always_passes():
    assert evaluate_field(ResolvedField("notes", "notes", "anything", []), "S1") is None
    assert evaluate_field(ResolvedField("notes", "notes", "", []), "S1") is None


def test_evaluate_field_reports_source_column_and_output_column_separately():
    # A file_parsing output's failure should point at both the source
    # column it came from and the specific output that failed, even when
    # those names differ.
    condition = _condition(">=", 1000)
    field = ResolvedField("coverage_tsv", "mean_depth", "500", [condition])
    failure = evaluate_field(field, "S1")
    assert failure is not None
    assert failure.column == "coverage_tsv"
    assert failure.output_column == "mean_depth"


def test_evaluate_row_aggregates_failures_across_fields():
    read_count_qc = _condition(">=", 1000)
    status_qc = _condition("=", "PASS")
    fields = [
        ResolvedField("read_count", "read_count", "500", [read_count_qc]),
        ResolvedField("status", "status", "FAIL", [status_qc]),
    ]
    outcome = evaluate_row(fields, "S1")
    assert outcome.passed is False
    assert {f.column for f in outcome.failures} == {"read_count", "status"}


def test_evaluate_row_all_pass():
    read_count_qc = _condition(">=", 1000)
    fields = [ResolvedField("read_count", "read_count", "5000", [read_count_qc])]
    outcome = evaluate_row(fields, "S1")
    assert outcome.passed is True
    assert outcome.failures == []


def test_evaluate_row_multiple_outputs_from_one_source_column_report_independently():
    # Two outputs sharing the same source column (a multi-output
    # file_parsing case) fail independently -- one failing shouldn't
    # suppress or merge with the other.
    depth_qc = _condition(">=", 30)
    mapq_qc = _condition(">=", 50)
    fields = [
        ResolvedField("coverage_tsv", "mean_depth", "10", [depth_qc]),
        ResolvedField("coverage_tsv", "mean_mapq", "60", [mapq_qc]),
    ]
    outcome = evaluate_row(fields, "S1")
    assert outcome.passed is False
    assert len(outcome.failures) == 1
    assert outcome.failures[0].output_column == "mean_depth"


def _unmatched_field(reason="no matching rule"):
    return ResolvedField("assembly_length", "assembly_length", "5000000", [], reason)


def test_evaluate_row_reports_unmatched_conditional_qc_field_as_a_failure():
    # A conditional-qc field with no matching rule and no default has an
    # empty qc list, same shape as "no QC configured" -- but
    # unmatched_reason distinguishes it, so it fails instead of silently
    # passing.
    #
    # NOTE: this asserts the ACTIVE behavior from transform.py's
    # _resolve_qc DECISION POINT. If that's switched to the silent-pass
    # ALTERNATIVE, this test needs to change too (unmatched_reason would
    # never be set, so this scenario can't arise this way anymore).
    outcome = evaluate_row([_unmatched_field()], "S1")
    assert outcome.passed is False
    failure = outcome.failures[0]
    assert failure.column == "assembly_length"
    assert failure.actual == "5000000"
    assert failure.reason == "no matching rule"
    assert failure.operator is None
    assert failure.expected is None


def test_evaluate_row_unmatched_conditional_qc_field_does_not_suppress_other_fields():
    passing_qc = _condition(">=", 1)
    fields = [
        _unmatched_field(),
        ResolvedField("read_count", "read_count", "5000", [passing_qc]),
    ]
    outcome = evaluate_row(fields, "S1")
    assert outcome.passed is False
    assert len(outcome.failures) == 1
    assert outcome.failures[0].column == "assembly_length"
