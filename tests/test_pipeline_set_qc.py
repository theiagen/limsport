"""End-to-end tests for `set_qc` (run-level QC rules) through
pipeline.run_export -- config-shape validation lives in
test_config_set_qc.py."""

import pytest

from limsport import pipeline, table_io
from limsport.exceptions import InputTableError


def _ntc_scenario(tmp_path, *, threshold, match_block='      sample_pattern: "NTC"\n'):
    """sample_id/reads input with one NTC and two real samples, plus a
    config gating the NTC's read count at `threshold` -- shared by the
    pass/fail tests below."""
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(
        "sample_id\treads\nNTC1\t500\nSAMPLE_A\t50000\nSAMPLE_B\t60000\n"
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "sample_qc:\n"
        "  - input_column: sample_id\n"
        "  - input_column: reads\n"
        "set_qc:\n"
        '  - rule_name: "NTC read count"\n'
        "    match_samples:\n"
        f"{match_block}"
        "    checks:\n"
        "      - input_column: reads\n"
        "        qc:\n"
        f'          - {{operator: "<=", value: {threshold}}}\n'
    )
    return input_tsv, config


def test_set_qc_pass_keeps_every_sample_in_output(tmp_path):
    input_tsv, config = _ntc_scenario(tmp_path, threshold=1000)  # NTC's 500 passes
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc_report.tsv"
    pipeline.run_export(input_tsv, config, None, out, qc_report)

    rows = list(table_io.iter_rows(out))
    assert [row[0] for row in rows] == ["NTC1", "SAMPLE_A", "SAMPLE_B"]
    assert list(table_io.iter_rows(qc_report)) == []


def test_set_qc_failure_zeroes_out_the_whole_run(tmp_path):
    input_tsv, config = _ntc_scenario(tmp_path, threshold=100)  # NTC's 500 fails
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc_report.tsv"
    pipeline.run_export(input_tsv, config, None, out, qc_report)

    # every sample dropped, not just the NTC
    assert list(table_io.iter_rows(out)) == []


def test_set_qc_failure_reports_full_detail_for_the_offending_sample(tmp_path):
    input_tsv, config = _ntc_scenario(tmp_path, threshold=100)
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc_report.tsv"
    pipeline.run_export(input_tsv, config, None, out, qc_report)

    report_rows = {row[0]: row for row in table_io.iter_rows(qc_report)}
    _sample, column, _output_column, operator, expected, actual, reason = report_rows[
        "NTC1"
    ]
    assert column == "reads"
    assert operator == "<="
    assert expected == "100"
    assert actual == "500"
    assert "500.0 <= 100" in reason


def test_set_qc_failure_reports_blank_collateral_row_for_other_samples(tmp_path):
    input_tsv, config = _ntc_scenario(tmp_path, threshold=100)
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc_report.tsv"
    pipeline.run_export(input_tsv, config, None, out, qc_report)

    report_rows = {row[0]: row for row in table_io.iter_rows(qc_report)}
    for sample in ("SAMPLE_A", "SAMPLE_B"):
        _, column, output_column, operator, expected, actual, reason = report_rows[
            sample
        ]
        assert column == ""
        assert output_column == ""
        assert operator == ""
        assert expected == ""
        assert actual == ""
        assert "NTC read count" in reason
        assert "run failed QC" in reason


def test_set_qc_matching_zero_samples_raises_before_output_created(tmp_path):
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text("sample_id\treads\nSAMPLE_A\t50000\n")
    config = tmp_path / "config.yaml"
    config.write_text(
        "sample_qc:\n"
        "  - input_column: sample_id\n"
        "  - input_column: reads\n"
        "set_qc:\n"
        '  - rule_name: "NTC read count"\n'
        "    match_samples:\n"
        '      sample_pattern: "NTC"\n'  # no sample in the input matches
        "    checks:\n"
        "      - input_column: reads\n"
        "        qc:\n"
        '          - {operator: "<=", value: 1000}\n'
    )
    out = tmp_path / "out.tsv"
    with pytest.raises(InputTableError, match="NTC read count"):
        pipeline.run_export(input_tsv, config, None, out, None)
    assert not out.exists()


def test_set_qc_check_input_column_not_in_header_raises_before_output_created(tmp_path):
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text("sample_id\tother_col\nNTC1\tx\n")
    config = tmp_path / "config.yaml"
    config.write_text(
        "sample_qc:\n"
        "  - input_column: sample_id\n"
        "set_qc:\n"
        '  - rule_name: "NTC read count"\n'
        "    match_samples:\n"
        '      sample_pattern: "NTC"\n'
        "    checks:\n"
        "      - input_column: reads\n"  # not a column in the input header
        "        qc:\n"
        '          - {operator: "<=", value: 1000}\n'
    )
    out = tmp_path / "out.tsv"
    with pytest.raises(InputTableError, match="reads"):
        pipeline.run_export(input_tsv, config, None, out, None)
    assert not out.exists()


def test_set_qc_check_input_column_need_not_be_in_output_columns_allow_list(tmp_path):
    # a check's `column` only needs to exist in the input header, same as
    # ConditionalQC.match -- it doesn't have to be kept in the output.
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text("sample_id\treads\nNTC1\t500\nSAMPLE_A\t50000\n")
    config = tmp_path / "config.yaml"
    config.write_text(
        "sample_qc:\n"
        "  - input_column: sample_id\n"  # "reads" deliberately not listed
        "set_qc:\n"
        '  - rule_name: "NTC read count"\n'
        "    match_samples:\n"
        '      sample_pattern: "NTC"\n'
        "    checks:\n"
        "      - input_column: reads\n"
        "        qc:\n"
        '          - {operator: "<=", value: 1000}\n'
    )
    out = tmp_path / "out.tsv"
    pipeline.run_export(input_tsv, config, None, out, None)
    rows = list(table_io.iter_rows(out))
    # "reads" isn't in the output
    assert rows == [["NTC1", "PASS"], ["SAMPLE_A", "PASS"]]


def test_set_qc_samples_matcher_identifies_by_exact_name(tmp_path):
    input_tsv, config = _ntc_scenario(
        tmp_path, threshold=100, match_block='      samples: ["NTC1"]\n'
    )
    out = tmp_path / "out.tsv"
    pipeline.run_export(input_tsv, config, None, out, None)
    assert list(table_io.iter_rows(out)) == []  # NTC1's 500 still exceeds 100


def test_set_qc_samples_matcher_with_one_missing_name_raises(tmp_path):
    # NTC1 exists and would otherwise satisfy the rule on its own, but a
    # samples: matcher names specific samples the caller expects to exist
    # -- every one of them must show up, not just at least one.
    input_tsv, config = _ntc_scenario(
        tmp_path, threshold=1000, match_block='      samples: ["NTC1", "NTC2"]\n'
    )
    out = tmp_path / "out.tsv"
    with pytest.raises(InputTableError, match=r"NTC read count.*\['NTC2'\]"):
        pipeline.run_export(input_tsv, config, None, out, None)
    assert not out.exists()


def test_set_qc_samples_matcher_with_every_name_missing_raises(tmp_path):
    input_tsv, config = _ntc_scenario(
        tmp_path, threshold=1000, match_block='      samples: ["NTC404", "NTC500"]\n'
    )
    out = tmp_path / "out.tsv"
    with pytest.raises(InputTableError, match=r"\['NTC404', 'NTC500'\]"):
        pipeline.run_export(input_tsv, config, None, out, None)
    assert not out.exists()


def test_set_qc_sample_regex_matcher(tmp_path):
    input_tsv, config = _ntc_scenario(
        tmp_path, threshold=1000, match_block='      sample_regex: "^NTC\\\\d+$"\n'
    )
    out = tmp_path / "out.tsv"
    pipeline.run_export(input_tsv, config, None, out, None)
    rows = list(table_io.iter_rows(out))
    assert [row[0] for row in rows] == [
        "NTC1",
        "SAMPLE_A",
        "SAMPLE_B",
    ]  # matched and passed


def _multi_column_ntc_config(tmp_path, *, contam_threshold):
    """One set_qc rule with TWO column checks under the same match -- the
    scenario multiple `checks` entries exist for: no need to repeat
    `match_samples:` across separate rules just to check more than one
    column on the same matched sample(s)."""
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(
        "sample_id\treads\tcontam_pct\nNTC1\t500\t1\nSAMPLE_A\t50000\t2\n"
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "sample_qc:\n"
        "  - input_column: sample_id\n"
        "  - input_column: reads\n"
        "  - input_column: contam_pct\n"
        "set_qc:\n"
        '  - rule_name: "NTC checks"\n'
        "    match_samples:\n"
        '      sample_pattern: "NTC"\n'
        "    checks:\n"
        "      - input_column: reads\n"
        "        qc:\n"
        '          - {operator: "<=", value: 1000}\n'
        "      - input_column: contam_pct\n"
        "        qc:\n"
        f'          - {{operator: "<=", value: {contam_threshold}}}\n'
    )
    return input_tsv, config


def test_set_qc_rule_with_multiple_checks_all_pass(tmp_path):
    input_tsv, config = _multi_column_ntc_config(
        tmp_path, contam_threshold=5
    )  # NTC's 1 passes
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc_report.tsv"
    pipeline.run_export(input_tsv, config, None, out, qc_report)

    rows = list(table_io.iter_rows(out))
    assert [row[0] for row in rows] == ["NTC1", "SAMPLE_A"]
    assert list(table_io.iter_rows(qc_report)) == []


def test_set_qc_rule_fails_the_whole_run_if_any_one_of_its_checks_fails(
    tmp_path,
):
    # reads (500 <= 1000) passes but contam_pct (1 <= 0) fails -- one failing
    # check within a multi-check rule is enough to fail the whole rule (and
    # so the whole run), same as an AND across multiple conditions on one
    # column.
    input_tsv, config = _multi_column_ntc_config(tmp_path, contam_threshold=0)
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc_report.tsv"
    pipeline.run_export(input_tsv, config, None, out, qc_report)

    assert list(table_io.iter_rows(out)) == []
    ntc_rows = [row for row in table_io.iter_rows(qc_report) if row[0] == "NTC1"]
    # only the failing check (contam_pct) produces a full-detail row -- the
    # passing check (reads) isn't itself a failure
    assert len(ntc_rows) == 1
    assert ntc_rows[0][1] == "contam_pct"
    assert ntc_rows[0][3] == "<="
    assert ntc_rows[0][5] == "1"


def test_set_qc_multiple_rules_failing_together_name_all_of_them_in_collateral_rows(
    tmp_path,
):
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(
        "sample_id\treads\tcontam_pct\nNTC1\t5000\t1\nSAMPLE_A\t50000\t2\n"
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "sample_qc:\n"
        "  - input_column: sample_id\n"
        "  - input_column: reads\n"
        "  - input_column: contam_pct\n"
        "set_qc:\n"
        '  - rule_name: "NTC read count"\n'
        "    match_samples:\n"
        '      sample_pattern: "NTC"\n'
        "    checks:\n"
        "      - input_column: reads\n"
        "        qc:\n"
        '          - {operator: "<=", value: 1000}\n'
        '  - rule_name: "NTC contamination"\n'
        "    match_samples:\n"
        '      sample_pattern: "NTC"\n'
        "    checks:\n"
        "      - input_column: contam_pct\n"
        "        qc:\n"
        '          - {operator: "<=", value: 0}\n'
    )
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc_report.tsv"
    pipeline.run_export(input_tsv, config, None, out, qc_report)

    assert list(table_io.iter_rows(out)) == []
    # NTC1 fails both rules directly -- two full-detail rows
    ntc_rows = [row for row in table_io.iter_rows(qc_report) if row[0] == "NTC1"]
    assert len(ntc_rows) == 2
    # SAMPLE_A is purely collateral -- one combined row naming both rules
    sample_a_rows = [
        row for row in table_io.iter_rows(qc_report) if row[0] == "SAMPLE_A"
    ]
    assert len(sample_a_rows) == 1
    reason = sample_a_rows[0][6]
    assert "NTC read count" in reason
    assert "NTC contamination" in reason


def test_set_qc_does_not_affect_a_config_that_does_not_use_it(tmp_path):
    # regression guard: a config with no set_qc key behaves identically to
    # before set_qc existed.
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text("sample_id\tread_count\nS1\t5000\nS2\t500\n")
    config = tmp_path / "config.yaml"
    config.write_text(
        "sample_qc:\n"
        "  - input_column: sample_id\n"
        "  - input_column: read_count\n"
        "    qc:\n"
        '      - {operator: ">=", value: 1000}\n'
    )
    out = tmp_path / "out.tsv"
    pipeline.run_export(input_tsv, config, None, out, None)
    rows = list(table_io.iter_rows(out))
    assert [row[0] for row in rows] == ["S1"]


def test_set_qc_with_columns_omitted_passes_every_input_column_through_unchanged(
    tmp_path,
):
    # a config that exists only for its set_qc rule -- no `sample_qc:` key at
    # all -- behaves like no config for column shaping, while set_qc still
    # gates the whole run.
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(
        "sample_id\treads\tnotes\n"
        "NTC1\t500\tblank control\n"
        "SAMPLE_A\t50000\treal sample\n"
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "set_qc:\n"
        '  - rule_name: "NTC read count"\n'
        "    match_samples:\n"
        '      sample_pattern: "NTC"\n'
        "    checks:\n"
        "      - input_column: reads\n"
        "        qc:\n"
        '          - {operator: "<=", value: 1000}\n'
    )
    out = tmp_path / "out.tsv"
    pipeline.run_export(input_tsv, config, None, out, None)
    rows = list(table_io.iter_rows(out))
    assert rows == [
        ["NTC1", "500", "blank control", "PASS"],
        ["SAMPLE_A", "50000", "real sample", "PASS"],
    ]


def test_set_qc_with_columns_omitted_still_fails_the_whole_run_on_a_set_qc_failure(
    tmp_path,
):
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text("sample_id\treads\nNTC1\t5000\nSAMPLE_A\t50000\n")
    config = tmp_path / "config.yaml"
    config.write_text(
        "set_qc:\n"
        '  - rule_name: "NTC read count"\n'
        "    match_samples:\n"
        '      sample_pattern: "NTC"\n'
        "    checks:\n"
        "      - input_column: reads\n"
        "        qc:\n"
        '          - {operator: "<=", value: 1000}\n'  # NTC's 5000 fails
    )
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc_report.tsv"
    pipeline.run_export(input_tsv, config, None, out, qc_report)
    assert list(table_io.iter_rows(out)) == []
    report_rows = {row[0]: row for row in table_io.iter_rows(qc_report)}
    assert report_rows["NTC1"][3] == "<="  # operator
    assert "run failed QC" in report_rows["SAMPLE_A"][6]


def test_set_qc_is_empty_lets_a_negative_control_pass_on_a_blank_result(tmp_path):
    # motivating use case: an NTC's "detected organism" column is expected
    # to be blank -- no organism should have been detected at all. Real
    # samples aren't checked by this rule, so their (non-blank) values in
    # the same column don't matter.
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(
        "sample_id\tdetected_organism\nNTC1\t\nSAMPLE_A\tEscherichia coli\n"
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "set_qc:\n"
        '  - rule_name: "NTC has no detected organism"\n'
        "    match_samples:\n"
        '      sample_pattern: "NTC"\n'
        "    checks:\n"
        "      - input_column: detected_organism\n"
        "        qc:\n"
        "          - {operator: is_empty}\n"
    )
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc_report.tsv"
    pipeline.run_export(input_tsv, config, None, out, qc_report)
    rows = list(table_io.iter_rows(out))
    assert [row[0] for row in rows] == ["NTC1", "SAMPLE_A"]
    assert list(table_io.iter_rows(qc_report)) == []


def test_set_qc_is_empty_fails_the_run_when_a_negative_control_has_contamination(
    tmp_path,
):
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(
        "sample_id\tdetected_organism\n"
        "NTC1\tEscherichia coli\n"  # contamination: should be blank
        "SAMPLE_A\tEscherichia coli\n"
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "set_qc:\n"
        '  - rule_name: "NTC has no detected organism"\n'
        "    match_samples:\n"
        '      sample_pattern: "NTC"\n'
        "    checks:\n"
        "      - input_column: detected_organism\n"
        "        qc:\n"
        "          - {operator: is_empty}\n"
    )
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc_report.tsv"
    pipeline.run_export(input_tsv, config, None, out, qc_report)
    assert list(table_io.iter_rows(out)) == []
    report_rows = {row[0]: row for row in table_io.iter_rows(qc_report)}
    assert report_rows["NTC1"][3] == "is_empty"  # operator itself is still shown
    assert report_rows["NTC1"][4] == ""  # expected is blank -- is_empty takes no value
    assert report_rows["NTC1"][6] == "value 'Escherichia coli' is not empty"


def test_set_qc_failure_still_runs_sample_level_qc_on_every_row(tmp_path):
    # Every sample's own QC results are reported even after the run has failed
    # set_qc, so every later row is still expanded and checked.
    data = tmp_path / "data.txt"
    data.write_text("42\n")
    low = tmp_path / "low.txt"
    low.write_text("5\n")
    calls = tmp_path / "calls.log"

    # NTC1 is the first row and violates the rule immediately
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(
        "sample_id\tdata_path\treads\n"
        f"NTC1\t{data}\t9999\n"
        + "".join(f"SAMPLE_{i:03d}\t{low if i == 5 else data}\t10\n" for i in range(20))
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "sample_qc:\n"
        "  - input_column: sample_id\n"
        "  - input_column: data_path\n"
        "    file_parsing:\n"
        "      - output_column: extracted\n"
        "        command: |\n"
        f'          echo call >> {calls}; cat "$FILE"\n'
        "        qc:\n"
        '          - {operator: ">=", value: 10}\n'
        "set_qc:\n"
        '  - rule_name: "NTC reads are low"\n'
        "    match_samples:\n"
        '      sample_pattern: "NTC"\n'
        "    checks:\n"
        "      - input_column: reads\n"
        "        qc:\n"
        '          - {operator: "<=", value: 1000}\n'
    )
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc_report.tsv"
    pipeline.run_export(
        input_tsv, config, None, out, qc_report, allow_file_parsing=True
    )

    # the whole run failed, so no rows, but every row was still parsed once
    assert list(table_io.iter_rows(out)) == []
    assert len(calls.read_text().splitlines()) == 21
    report_rows = list(table_io.iter_rows(qc_report))
    # NTC1's set_qc detail, SAMPLE_005's own failure, and 20 collateral rows
    assert len(report_rows) == 22
    assert ["SAMPLE_005", "data_path", "extracted"] in [r[:3] for r in report_rows]


def _late_control_file_parsing_scenario(tmp_path, *, ntc_reads):
    """20 ordinary samples with file_parsing, then the NTC as the very LAST row.
    `calls` counts file_parsing subprocess invocations."""
    data = tmp_path / "data.txt"
    data.write_text("42\n")
    calls = tmp_path / "calls.log"

    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(
        "sample_id\tdata_path\treads\n"
        + "".join(f"SAMPLE_{i:03d}\t{data}\t10\n" for i in range(20))
        + f"NTC1\t{data}\t{ntc_reads}\n"  # the control is last
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "sample_qc:\n"
        "  - input_column: sample_id\n"
        "  - input_column: data_path\n"
        "    file_parsing:\n"
        "      - output_column: extracted\n"
        "        command: |\n"
        f'          echo call >> {calls}; cat "$FILE"\n'
        "set_qc:\n"
        '  - rule_name: "NTC reads are low"\n'
        "    match_samples:\n"
        '      sample_pattern: "NTC"\n'
        "    checks:\n"
        "      - input_column: reads\n"
        "        qc:\n"
        '          - {operator: "<=", value: 1000}\n'
    )
    return input_tsv, config, calls


def test_set_qc_zero_match_raises_without_writing_output(tmp_path):
    # A rule matching no sample is a hard error, only detectable once the input is read.
    data = tmp_path / "data.txt"
    data.write_text("42\n")
    calls = tmp_path / "calls.log"
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(
        "sample_id\tdata_path\treads\n"
        + "".join(f"SAMPLE_{i:03d}\t{data}\t10\n" for i in range(20))
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "sample_qc:\n"
        "  - input_column: sample_id\n"
        "  - input_column: data_path\n"
        "    file_parsing:\n"
        "      - output_column: extracted\n"
        "        command: |\n"
        f'          echo call >> {calls}; cat "$FILE"\n'
        "set_qc:\n"
        '  - rule_name: "NTC reads are low"\n'
        "    match_samples:\n"
        '      sample_pattern: "NTC"\n'  # nothing in the input matches
        "    checks:\n"
        "      - input_column: reads\n"
        "        qc:\n"
        '          - {operator: "<=", value: 1000}\n'
    )
    out = tmp_path / "out.tsv"
    with pytest.raises(InputTableError, match="matched no samples"):
        pipeline.run_export(input_tsv, config, None, out, None, allow_file_parsing=True)
    assert not out.exists()


def test_set_qc_pass_with_file_parsing_still_expands_every_row_once(tmp_path):
    # every row is expanded exactly once and reaches the output
    input_tsv, config, calls = _late_control_file_parsing_scenario(
        tmp_path, ntc_reads=500
    )
    out = tmp_path / "out.tsv"
    pipeline.run_export(input_tsv, config, None, out, None, allow_file_parsing=True)

    rows = list(table_io.iter_rows(out))
    assert len(rows) == 21  # 20 samples + the NTC
    assert all(row[1] == "42" for row in rows)  # every one was really parsed
    assert len(calls.read_text().splitlines()) == 21  # once each, not twice


def _conditional_set_qc_scenario(tmp_path, *, ntc_reads):
    """An NTC whose read cap depends on its run type, via a conditional set_qc check."""
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(
        "sample_id\trun_type\treads\n"
        f"NTC1\tnextseq_2x150\t{ntc_reads}\n"
        "SAMPLE_A\tnextseq_2x150\t50000\n"
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "sample_qc:\n"
        "  - input_column: sample_id\n"
        "  - input_column: reads\n"
        "set_qc:\n"
        '  - rule_name: "NTC read count"\n'
        "    match_samples:\n"
        '      sample_pattern: "NTC"\n'
        "    checks:\n"
        "      - input_column: reads\n"
        "        qc:\n"
        "          match_column: run_type\n"
        "          cases:\n"
        "            miseq:\n"
        '              - {operator: "<=", value: 100}\n'
        "          pattern_cases:\n"
        "            nextseq:\n"
        '              - {operator: "<=", value: 1000}\n'
    )
    return input_tsv, config


@pytest.mark.parametrize(
    ("ntc_reads", "expected"), [(500, ["NTC1", "SAMPLE_A"]), (5000, [])]
)
def test_set_qc_conditional_check_picks_conditions_from_match_column(
    tmp_path, ntc_reads, expected
):
    input_tsv, config = _conditional_set_qc_scenario(tmp_path, ntc_reads=ntc_reads)
    out = tmp_path / "out.tsv"
    pipeline.run_export(input_tsv, config, None, out, None)
    assert [row[0] for row in table_io.iter_rows(out)] == expected


def test_set_qc_conditional_check_match_column_must_exist(tmp_path):
    input_tsv, config = _conditional_set_qc_scenario(tmp_path, ntc_reads=500)
    config.write_text(
        config.read_text().replace("match_column: run_type", "match_column: nope")
    )
    with pytest.raises(InputTableError, match="has qc matching on column"):
        pipeline.run_export(input_tsv, config, None, tmp_path / "out.tsv", None)


def test_failure_matched_by_two_include_sample_qc_rules_is_reported_once(tmp_path):
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text("sample_id\treads\nA\t50\nB\t10\n")
    config = tmp_path / "config.yaml"
    config.write_text(
        "sample_qc:\n"
        "  - input_column: reads\n"
        '    qc: [{operator: ">=", value: 20}]\n'
        "set_qc:\n"
        "  - rule_name: r1\n"
        "    match_samples: {samples: [B]}\n"
        "    include_sample_qc: true\n"
        "  - rule_name: r2\n"
        "    match_samples: {sample_pattern: B}\n"
        "    include_sample_qc: true\n"
    )
    qc_report = tmp_path / "qc_report.tsv"
    pipeline.run_export(input_tsv, config, None, tmp_path / "out.tsv", qc_report)
    samples = [row[0] for row in table_io.iter_rows(qc_report)]
    assert samples == ["B", "A"]


def test_set_qc_only_config_rejects_input_with_a_qc_status_column(tmp_path):
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text("sample_id\treads\tlimsport_qc_status\nNTC1\t500\tPASS\n")
    config = tmp_path / "config.yaml"
    config.write_text(
        "set_qc:\n"
        "  - rule_name: r\n"
        "    match_samples: {sample_pattern: NTC}\n"
        '    checks: [{input_column: reads, qc: [{operator: "<", value: 1000}]}]\n'
    )
    with pytest.raises(InputTableError, match="limsport_qc_status"):
        pipeline.run_export(input_tsv, config, None, tmp_path / "out.tsv", None)


def test_sample_names_are_stripped_before_matching(tmp_path):
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text("sample_id\treads\nA \t5\nB\t6\n")
    samples = tmp_path / "samples.txt"
    samples.write_text("A\n")
    out = tmp_path / "out.tsv"
    pipeline.run_export(input_tsv, None, samples, out, None)
    assert [row[0] for row in table_io.iter_rows(out)] == ["A "]
