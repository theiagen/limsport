"""End-to-end qc_by tests through transform.run_export -- config-shape
validation for qc_by lives in test_config.py."""

import pytest

from limsport import table_io, transform
from limsport.exceptions import InputTableError


def _qc_by_scenario(tmp_path, default_block=""):
    """sample_id/taxon/assembly_length input plus a qc_by config with
    per-organism thresholds for two taxa, shared by the qc_by tests
    below. default_block lets a caller opt a run into (or out of) a
    qc_by.default fallback."""
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(
        "sample_id\ttaxon\tassembly_length\n"
        "S1\tEscherichia coli\t5000000\n"       # passes E. coli's range
        "S2\tKlebsiella pneumoniae\t5000000\n"  # below Klebsiella's floor
        "S3\tMystery Bug\t5000000\n"            # taxon has no rule
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "columns:\n"
        "  - name: sample_id\n"
        "  - name: taxon\n"
        "  - name: assembly_length\n"
        "    qc_by:\n"
        "      match: taxon\n"
        "      rules:\n"
        '        "Escherichia coli":\n'
        '          - {operator: ">=", value: 4600000}\n'
        '          - {operator: "<=", value: 5900000}\n'
        '        "Klebsiella pneumoniae":\n'
        '          - {operator: ">=", value: 5200000}\n'
        '          - {operator: "<=", value: 5900000}\n'
        f"{default_block}"
    )
    return input_tsv, config


def test_qc_by_applies_different_thresholds_per_organism_row(tmp_path):
    input_tsv, config = _qc_by_scenario(tmp_path)
    out = tmp_path / "out.tsv"
    transform.run_export(input_tsv, config, None, out, None)
    rows = list(table_io.iter_rows(out))
    # S1 passes under E. coli's floor (>= 4.6M); S2 fails Klebsiella's
    # higher floor (>= 5.2M) at the same raw value -- proving the two
    # rows were checked against genuinely different thresholds.
    assert [row[0] for row in rows] == ["S1"]


def test_qc_by_default_used_when_no_rule_matches(tmp_path):
    input_tsv, config = _qc_by_scenario(
        tmp_path, default_block='      default:\n        - {operator: ">=", value: 100}\n'
    )
    out = tmp_path / "out.tsv"
    transform.run_export(input_tsv, config, None, out, None)
    rows = list(table_io.iter_rows(out))
    # S3's taxon ("Mystery Bug") matches no rule, but the lenient default
    # (>= 100) passes its real value, so it's still in the output.
    assert "S3" in [row[0] for row in rows]


def test_qc_by_unmatched_with_no_default_fails_and_reports_blank_operator(tmp_path):
    # NOTE: asserts the ACTIVE behavior from transform.py's _resolve_qc_by
    # DECISION POINT (unmatched + no default -> QC failure). If that's
    # switched to the silent-pass ALTERNATIVE, this test's expectations
    # need to flip: S3 would stay in `out` and qc_report would have no
    # row for it at all.
    input_tsv, config = _qc_by_scenario(tmp_path)  # no default block
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc_report.tsv"
    transform.run_export(input_tsv, config, None, out, qc_report)

    rows = list(table_io.iter_rows(out))
    assert "S3" not in [row[0] for row in rows]  # dropped: no rule, no default

    report_rows = {row[0]: row for row in table_io.iter_rows(qc_report)}
    sample, column, output_column, operator, expected, actual, reason = report_rows["S3"]
    assert column == "assembly_length"
    assert operator == ""
    assert expected == ""
    assert actual == "5000000"
    assert "no qc_by rule matches taxon='Mystery Bug'" in reason


def test_qc_by_match_column_not_in_header_raises_before_output_created(tmp_path):
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text("sample_id\tassembly_length\nS1\t5000000\n")
    config = tmp_path / "config.yaml"
    config.write_text(
        "columns:\n"
        "  - name: sample_id\n"
        "  - name: assembly_length\n"
        "    qc_by:\n"
        "      match: taxon\n"  # not a column in the input header
        "      rules:\n"
        '        "x":\n          - {operator: ">=", value: 1}\n'
    )
    out = tmp_path / "out.tsv"
    with pytest.raises(InputTableError, match="taxon"):
        transform.run_export(input_tsv, config, None, out, None)
    assert not out.exists()
