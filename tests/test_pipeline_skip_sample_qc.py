"""End-to-end tests for `skip_sample_qc` and `include_qc_fails` through
pipeline.run_export."""

import pytest
from pydantic import ValidationError

from limsport import pipeline, table_io
from limsport.config import ExportConfig
from limsport.exceptions import InputTableError


def _control_scenario(tmp_path, *, ntc_reads=500, skip_sample_qc=True):
    """NTC1 with a read count far below the per-row minimum, plus one passing
    and one failing real sample. A set_qc rule caps the NTC's reads at 1000."""
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(
        f"sample_id\treads\nNTC1\t{ntc_reads}\nSAMPLE_A\t50000\nSAMPLE_B\t10\n"
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "sample_qc:\n"
        "  - input_column: sample_id\n"
        "  - input_column: reads\n"
        "    qc:\n"
        '      - {operator: ">=", value: 1000}\n'
        + ('skip_sample_qc:\n  sample_pattern: "NTC"\n' if skip_sample_qc else "")
        + "set_qc:\n"
        '  - rule_name: "NTC read count"\n'
        "    match_samples:\n"
        '      sample_pattern: "NTC"\n'
        "    checks:\n"
        "      - input_column: reads\n"
        "        qc:\n"
        '          - {operator: "<=", value: 1000}\n'
    )
    return input_tsv, config


def _run(tmp_path, input_tsv, config, **kwargs):
    out = tmp_path / "out.tsv"
    report_path = tmp_path / "report.tsv"
    summary_path = tmp_path / "summary.tsv"
    pipeline.run_export(
        input_tsv,
        config,
        None,
        out,
        report_path,
        qc_summary_path=summary_path,
        **kwargs,
    )
    return (
        list(table_io.iter_rows(out)),
        list(table_io.iter_rows(report_path)),
        list(table_io.iter_rows(summary_path)),
    )


def test_without_skip_sample_qc_the_ntc_fails_row_qc(tmp_path):
    rows, _, summary = _run(
        tmp_path, *_control_scenario(tmp_path, skip_sample_qc=False)
    )
    assert [row[0] for row in rows] == ["SAMPLE_A"]
    assert ["NTC1", "FAIL"] in summary


def test_skipped_sample_skips_row_qc_and_is_written(tmp_path):
    rows, report_rows, summary = _run(tmp_path, *_control_scenario(tmp_path))
    assert [row[0] for row in rows] == ["NTC1", "SAMPLE_A"]
    assert [row[0] for row in report_rows] == ["SAMPLE_B"]
    assert summary == [
        ["NTC1", "CONTROL"],
        ["SAMPLE_A", "PASS"],
        ["SAMPLE_B", "FAIL"],
    ]


def test_skipped_sample_still_fails_set_qc(tmp_path):
    rows, report_rows, summary = _run(
        tmp_path, *_control_scenario(tmp_path, ntc_reads=5000)
    )
    assert rows == []
    assert report_rows[0][:2] == ["NTC1", "reads"]
    assert all(status == "FAIL" for _, status in summary)


def test_skipped_sample_file_parsing_failure_is_blank_and_not_counted(tmp_path):
    good = tmp_path / "good.txt"
    good.write_text("42\n")
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(
        f"sample_id\tdata_path\nNTC1\t{tmp_path / 'missing.txt'}\nSAMPLE_A\t{good}\n"
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "sample_qc:\n"
        "  - input_column: sample_id\n"
        "  - input_column: data_path\n"
        "    file_parsing:\n"
        "      - output_column: extracted\n"
        '        command: cat "$FILE"\n'
        "skip_sample_qc:\n"
        "  samples: [NTC1]\n"
    )
    rows, report_rows, summary = _run(
        tmp_path,
        input_tsv,
        config,
        allow_file_parsing=True,
        max_file_parsing_failures=0,
    )
    assert rows == [["NTC1", ""], ["SAMPLE_A", "42"]]
    assert report_rows == []
    assert summary == [["NTC1", "CONTROL"], ["SAMPLE_A", "PASS"]]


def test_include_qc_fails_writes_failing_rows(tmp_path):
    rows, report_rows, summary = _run(
        tmp_path,
        *_control_scenario(tmp_path, skip_sample_qc=False),
        include_qc_fails=True,
    )
    assert [row[0] for row in rows] == ["NTC1", "SAMPLE_A", "SAMPLE_B"]
    assert [row[0] for row in report_rows] == ["NTC1", "SAMPLE_B"]
    assert summary == [["NTC1", "FAIL"], ["SAMPLE_A", "PASS"], ["SAMPLE_B", "FAIL"]]


def test_include_qc_fails_writes_every_row_of_a_set_qc_failed_run(tmp_path):
    rows, _, summary = _run(
        tmp_path,
        *_control_scenario(tmp_path, ntc_reads=5000),
        include_qc_fails=True,
    )
    assert rows == [["NTC1", "5000"], ["SAMPLE_A", "50000"], ["SAMPLE_B", "10"]]
    assert all(status == "FAIL" for _, status in summary)


def test_include_qc_fails_skips_the_set_qc_prepass_and_writes_parsed_values(tmp_path):
    data = tmp_path / "data.txt"
    data.write_text("42\n")
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(
        f"sample_id\tdata_path\treads\nSAMPLE_A\t{data}\t10\nNTC1\t{data}\t9999\n"
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "sample_qc:\n"
        "  - input_column: sample_id\n"
        "  - input_column: data_path\n"
        "    file_parsing:\n"
        "      - output_column: extracted\n"
        '        command: cat "$FILE"\n'
        "set_qc:\n"
        '  - rule_name: "NTC reads are low"\n'
        "    match_samples:\n"
        '      sample_pattern: "NTC"\n'
        "    checks:\n"
        "      - input_column: reads\n"
        "        qc:\n"
        '          - {operator: "<=", value: 1000}\n'
    )
    rows, _, summary = _run(
        tmp_path, input_tsv, config, allow_file_parsing=True, include_qc_fails=True
    )
    assert rows == [["SAMPLE_A", "42"], ["NTC1", "42"]]
    assert summary == [["SAMPLE_A", "FAIL"], ["NTC1", "FAIL"]]


def test_include_qc_fails_blanks_values_file_parsing_could_not_produce(tmp_path):
    good = tmp_path / "good.txt"
    good.write_text("42\n")
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(
        "sample_id\tdata_path\n"
        f"SAMPLE_A\t{tmp_path / 'missing.txt'}\n"
        f"SAMPLE_B\t{good}\n"
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "sample_qc:\n"
        "  - input_column: sample_id\n"
        "  - input_column: data_path\n"
        "    file_parsing:\n"
        "      - output_column: extracted\n"
        '        command: cat "$FILE"\n'
    )
    rows, _, summary = _run(
        tmp_path, input_tsv, config, allow_file_parsing=True, include_qc_fails=True
    )
    assert rows == [["SAMPLE_A", ""], ["SAMPLE_B", "42"]]
    assert summary == [["SAMPLE_A", "FAIL"], ["SAMPLE_B", "PASS"]]


def test_skip_sample_qc_rejects_two_match_methods():
    with pytest.raises(ValidationError, match="exactly one of"):
        ExportConfig.model_validate(
            {
                "sample_qc": [{"input_column": "sample_id"}],
                "skip_sample_qc": {"sample_pattern": "NTC", "samples": ["NTC1"]},
            }
        )


def _sample_qc_scenario(tmp_path, *, pc_reads, skip_sample_qc=False):
    """A positive control and two real samples under a per-row read minimum, plus a
    set_qc rule pulling the per-row qc in for the positive control."""
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(
        f"sample_id\treads\nPC1\t{pc_reads}\nSAMPLE_A\t50000\nSAMPLE_B\t10\n"
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "sample_qc:\n"
        "  - input_column: sample_id\n"
        "  - input_column: reads\n"
        "    qc:\n"
        '      - {operator: ">=", value: 1000}\n'
        + ('skip_sample_qc:\n  sample_pattern: "PC"\n' if skip_sample_qc else "")
        + "set_qc:\n"
        '  - rule_name: "PC passes production qc"\n'
        "    match_samples:\n"
        '      sample_pattern: "PC"\n'
        "    include_sample_qc: true\n"
    )
    return input_tsv, config


def test_include_sample_qc_failure_fails_the_run(tmp_path):
    rows, report_rows, summary = _run(
        tmp_path, *_sample_qc_scenario(tmp_path, pc_reads=10)
    )
    assert rows == []
    assert report_rows[0][:2] == ["PC1", "reads"]
    assert "PC passes production qc" in report_rows[1][-1]
    assert all(status == "FAIL" for _, status in summary)


def test_include_sample_qc_pass_leaves_other_samples_to_their_own_qc(tmp_path):
    rows, _, summary = _run(tmp_path, *_sample_qc_scenario(tmp_path, pc_reads=5000))
    assert [row[0] for row in rows] == ["PC1", "SAMPLE_A"]
    assert summary == [["PC1", "PASS"], ["SAMPLE_A", "PASS"], ["SAMPLE_B", "FAIL"]]


def test_include_sample_qc_on_a_skipped_sample_raises(tmp_path):
    with pytest.raises(InputTableError, match="include_sample_qc"):
        _run(
            tmp_path,
            *_sample_qc_scenario(tmp_path, pc_reads=5000, skip_sample_qc=True),
        )


def test_include_sample_qc_checks_file_parsed_values_after_the_prepass(tmp_path):
    low = tmp_path / "low.txt"
    low.write_text("5\n")
    high = tmp_path / "high.txt"
    high.write_text("500\n")
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(f"sample_id\tdata_path\nSAMPLE_A\t{high}\nPC1\t{low}\n")
    config = tmp_path / "config.yaml"
    config.write_text(
        "sample_qc:\n"
        "  - input_column: sample_id\n"
        "  - input_column: data_path\n"
        "    file_parsing:\n"
        "      - output_column: extracted\n"
        '        command: cat "$FILE"\n'
        "        qc:\n"
        '          - {operator: ">=", value: 100}\n'
        "set_qc:\n"
        '  - rule_name: "PC passes production qc"\n'
        "    match_samples:\n"
        "      samples: [PC1]\n"
        "    include_sample_qc: true\n"
    )
    rows, report_rows, summary = _run(
        tmp_path, input_tsv, config, allow_file_parsing=True
    )
    assert rows == []
    assert ["PC1", "data_path", "extracted"] in [row[:3] for row in report_rows]
    assert summary == [["SAMPLE_A", "FAIL"], ["PC1", "FAIL"]]
