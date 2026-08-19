from limsport import table_io, report
from limsport.config import QCFailure, QCOperator


def _failure(
    sample="S1",
    column="read_count",
    output_column=None,
    operator=QCOperator.GE,
    expected=1000,
    actual="500",
    reason="500.0 >= 1000 is False",
):
    return QCFailure(
        sample=sample,
        column=column,
        output_column=output_column if output_column is not None else column,
        operator=operator,
        expected=expected,
        actual=actual,
        reason=reason,
    )


_HEADER = [
    "sample",
    "column",
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
    header = table_io.read_header(path)
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
    assert table_io.read_header(path) == _HEADER
    assert list(table_io.iter_rows(path)) == []


def test_write_qc_report_blanks_operator_and_expected_when_none(tmp_path):
    # A conditional-qc failure with no matching rule (and no default) has
    # no condition to point at -- operator/expected are None, and should
    # write out as empty cells rather than "None".
    path = tmp_path / "report.tsv"
    failure = _failure(
        column="assembly_length",
        operator=None,
        expected=None,
        actual="5000000",
        reason="no qc rule matches taxon='Mystery Bug' for column 'assembly_length', and no default is configured",
    )
    report.write_qc_report(path, [failure])
    rows = list(table_io.iter_rows(path))
    assert rows[0][3] == ""  # operator
    assert rows[0][4] == ""  # expected


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


def test_log_qc_failures_mentions_output_name_only_when_renamed(caplog):
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


def test_log_unknown_samples(caplog):
    with caplog.at_level("WARNING"):
        report.log_unknown_samples({"SAMPLE_999"})
    assert any("SAMPLE_999" in r.message for r in caplog.records)
