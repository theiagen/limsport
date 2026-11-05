from limsport import report, table_io
from limsport.config import QCOperator
from limsport.qc import QCFailure


def _failure(
    sample="S1",
    input_column="read_count",
    output_column=None,
    operator=QCOperator.GE,
    expected=1000,
    actual="500",
    reason="500.0 >= 1000 is False",
):
    return QCFailure(
        sample=sample,
        input_column=input_column,
        output_column=output_column if output_column is not None else input_column,
        operator=operator,
        expected=expected,
        actual=actual,
        reason=reason,
    )


_HEADER = [
    "sample",
    "input_column",
    "output_column",
    "operator",
    "expected",
    "actual",
    "reason",
]


def test_write_qc_report_rows(tmp_path):
    path = tmp_path / "report.tsv"
    report.write_qc_report(
        path, [_failure("S1", "read_count"), _failure("S2", "status")]
    )
    header = table_io.get_input_header(path)
    rows = list(table_io.iter_rows(path))
    assert header == _HEADER
    assert rows[0][0] == "S1"
    assert rows[1][0] == "S2"


def test_write_qc_report_includes_output_column(tmp_path):
    path = tmp_path / "report.tsv"
    report.write_qc_report(
        path, [_failure("S1", "read_count", output_column="total_reads")]
    )
    rows = list(table_io.iter_rows(path))
    assert rows[0][1] == "read_count"  # original name
    assert rows[0][2] == "total_reads"  # renamed/output name


def test_write_qc_report_header_only_when_no_failures(tmp_path):
    path = tmp_path / "report.tsv"
    report.write_qc_report(path, [])
    assert table_io.get_input_header(path) == _HEADER
    assert list(table_io.iter_rows(path)) == []


def test_write_qc_report_blanks_operator_and_expected_when_none(tmp_path):
    # A conditional-qc failure with no matching rule (and no default) has
    # no condition to point at -- operator/expected are None, and should
    # write out as empty cells rather than "None".
    path = tmp_path / "report.tsv"
    failure = _failure(
        input_column="assembly_length",
        operator=None,
        expected=None,
        actual="5000000",
        reason="no qc rule matches taxon='Mystery Bug' for column 'assembly_length', and no default is configured",
    )
    report.write_qc_report(path, [failure])
    rows = list(table_io.iter_rows(path))
    assert rows[0][3] == ""  # operator
    assert rows[0][4] == ""  # expected


def test_write_qc_summary_one_row_per_sample_in_order(tmp_path):
    path = tmp_path / "summary.tsv"
    report.write_qc_summary(
        path,
        ["S1", "S2", "S1", "S3"],
        [_failure("S2"), _failure("S2", "status")],
        sample_qc_failed={"S2"},
    )
    assert table_io.get_input_header(path) == [
        "sample",
        "sample_qc_status",
        "set_qc_status",
        "rationale",
    ]
    assert list(table_io.iter_rows(path)) == [
        ["S1", "PASS", "SKIPPED", ""],
        [
            "S2",
            "FAIL",
            "SKIPPED",
            "read_count: 500.0 >= 1000 is False; status: 500.0 >= 1000 is False",
        ],
        ["S3", "PASS", "SKIPPED", ""],
    ]


def test_write_qc_summary_skipped_sample_is_skipped_whatever_set_qc_says(tmp_path):
    path = tmp_path / "summary.tsv"
    ntc_failure = _failure("NTC2")
    for set_qc_status in ("PASS", "FAIL"):
        report.write_qc_summary(
            path,
            ["NTC1", "NTC2", "S1"],
            [ntc_failure] if set_qc_status == "FAIL" else [],
            skipped={"NTC1", "NTC2"},
            set_qc_status=set_qc_status,
        )
        assert list(table_io.iter_rows(path)) == [
            ["NTC1", "SKIPPED", set_qc_status, ""],
            [
                "NTC2",
                "SKIPPED",
                set_qc_status,
                "read_count: 500.0 >= 1000 is False" if set_qc_status == "FAIL" else "",
            ],
            ["S1", "PASS", set_qc_status, ""],
        ]


def test_write_qc_summary_rationale_is_the_bare_reason_for_a_run_level_failure(
    tmp_path,
):
    path = tmp_path / "summary.tsv"
    run_failed = _failure("S1", "", reason="run failed QC due to set_qc rule(s): ['r']")
    report.write_qc_summary(path, ["S1"], [run_failed], set_qc_status="FAIL")
    assert list(table_io.iter_rows(path)) == [
        ["S1", "PASS", "FAIL", "run failed QC due to set_qc rule(s): ['r']"]
    ]


def test_log_summary_emits_info_line(caplog):
    with caplog.at_level("INFO"):
        report.log_summary(passed=3, total=5)
    assert any("3/5" in r.message for r in caplog.records)


def test_log_no_qc_summary_emits_info_line_without_qc_wording(caplog):
    with caplog.at_level("INFO"):
        report.log_no_qc_summary(5, 5)
    messages = [r.message for r in caplog.records]
    assert any("5/5" in m for m in messages)
    # must not claim QC happened when none did
    assert not any("passed QC" in m for m in messages)


def test_log_nothing_to_do_emits_info_line_without_qc_wording(caplog):
    with caplog.at_level("INFO"):
        report.log_nothing_to_do()
    messages = [r.message for r in caplog.records]
    assert any("nothing to do" in m for m in messages)
    assert not any("passed QC" in m for m in messages)


def test_log_qc_failures_emits_warning_per_failure(caplog):
    with caplog.at_level("WARNING"):
        report.log_qc_failures([_failure("S1", "read_count"), _failure("S2", "status")])
    messages = [r.message for r in caplog.records]
    assert any("S1" in m and "read_count" in m for m in messages)
    assert any("S2" in m and "status" in m for m in messages)


def test_log_qc_failures_mentions_output_column_name_only_when_renamed(caplog):
    with caplog.at_level("WARNING"):
        report.log_qc_failures(
            [
                _failure("S1", "read_count"),  # not renamed
                _failure("S2", "read_count", output_column="total_reads"),  # renamed
            ]
        )
    messages = [r.message for r in caplog.records]
    assert "output" not in messages[0]
    assert "total_reads" in messages[1] and "output" in messages[1]


def test_log_missing_samples(caplog):
    with caplog.at_level("WARNING"):
        report.log_missing_samples({"SAMPLE_999"})
    assert any("SAMPLE_999" in r.message for r in caplog.records)


def test_log_qc_failures_omits_column_for_run_level_failure(caplog):
    with caplog.at_level("WARNING"):
        report.log_qc_failures(
            [_failure("S1", "", output_column="", reason="run failed QC")]
        )
    assert caplog.records[0].message == "S1: failed QC (run failed QC)"
