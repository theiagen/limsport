"""End-to-end file_parsing tests through transform.run_export -- unit-level
coverage of the file_parsing module itself lives in test_file_parsing.py."""

import pytest

from limsport import table_io, transform
from limsport.exceptions import ConfigError
from factories import file_parsing_scenario


def _cut_scenario(tmp_path):
    """The colon-delimited data file + `cut`-and-QC config shared by the
    file_parsing tests below."""
    return file_parsing_scenario(
        tmp_path,
        data_content="abc:123:xyz\n",
        command='\'cut -d: -f2 "$FILE"\'',
        qc_yaml='        qc:\n          - {operator: "=", value: "123"}\n',
    )


def test_file_parsing_requires_allow_flag(tmp_path):
    input_tsv, config = _cut_scenario(tmp_path)
    out = tmp_path / "out.tsv"
    with pytest.raises(ConfigError, match="allow-file-parsing"):
        transform.run_export(input_tsv, config, None, out, None)  # allow_file_parsing defaults to False
    assert not out.exists()


def test_file_parsing_result_flows_through_qc_and_output(tmp_path):
    input_tsv, config = _cut_scenario(tmp_path)
    out = tmp_path / "out.tsv"
    transform.run_export(input_tsv, config, None, out, None, allow_file_parsing=True)

    header = table_io.read_header(out)
    rows = list(table_io.iter_rows(out))
    assert header == ["sample_id", "extracted"]
    # The parsed value ("123", extracted from the file) is what's output and
    # what QC saw -- not the raw file path, and the sample passes because
    # the QC rule (`= "123"`) is checked against the parsed result.
    assert rows == [["SAMPLE_001", "123"]]


def test_file_parsing_command_failure_aborts_whole_export(tmp_path):
    data_file = tmp_path / "data.txt"
    data_file.write_text("irrelevant\n")
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(f"sample_id\tdata_path\nSAMPLE_001\t{data_file}\n")
    config = tmp_path / "config.yaml"
    config.write_text(
        "columns:\n"
        "  - name: sample_id\n"
        "  - name: data_path\n"
        "    file_parsing:\n"
        "      - name: extracted\n"
        "        command: exit 1\n"
    )
    out = tmp_path / "out.tsv"
    with pytest.raises(Exception, match="exit 1"):
        transform.run_export(input_tsv, config, None, out, None, allow_file_parsing=True)
    assert not out.exists()


def test_file_parsing_not_invoked_for_samples_filtered_out(tmp_path, monkeypatch):
    data_file = tmp_path / "data.txt"
    data_file.write_text("irrelevant\n")
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(
        f"sample_id\tdata_path\nSAMPLE_001\t{data_file}\nSAMPLE_002\t{data_file}\n"
    )
    samples = tmp_path / "samples.txt"
    samples.write_text("SAMPLE_001\n")  # SAMPLE_002 deliberately not requested
    config = tmp_path / "config.yaml"
    config.write_text(
        "columns:\n  - name: sample_id\n  - name: data_path\n    file_parsing:\n"
        "      - name: extracted\n        command: cat\n"
    )

    calls = []
    monkeypatch.setattr(
        transform.file_parsing, "run", lambda outputs, raw_value: calls.append(raw_value) or ["ok"]
    )

    out = tmp_path / "out.tsv"
    transform.run_export(input_tsv, config, samples, out, None, allow_file_parsing=True)
    # Only SAMPLE_001's row should ever have triggered file_parsing -- an
    # expensive command (or a cloud download) must never run for a row
    # that's about to be discarded by the sample filter anyway.
    assert calls == [str(data_file)]


def test_file_parsing_runs_independently_per_column_no_caching(tmp_path, monkeypatch):
    # Two columns pointing at the *same* raw path must each trigger their
    # own file_parsing.run call -- there's no cross-column memoization,
    # matching the explicit "no caching" design decision.
    data_file = tmp_path / "data.txt"
    data_file.write_text("irrelevant\n")
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(f"sample_id\tpath_a\tpath_b\nSAMPLE_001\t{data_file}\t{data_file}\n")
    config = tmp_path / "config.yaml"
    config.write_text(
        "columns:\n"
        "  - name: sample_id\n"
        "  - name: path_a\n"
        "    file_parsing:\n      - name: a_out\n        command: cat\n"
        "  - name: path_b\n"
        "    file_parsing:\n      - name: b_out\n        command: cat\n"
    )

    calls = []
    monkeypatch.setattr(
        transform.file_parsing, "run", lambda outputs, raw_value: calls.append(raw_value) or ["ok"]
    )

    out = tmp_path / "out.tsv"
    transform.run_export(input_tsv, config, None, out, None, allow_file_parsing=True)
    assert calls == [str(data_file), str(data_file)]


def _multi_output_scenario(tmp_path, coverage_pct="99.98"):
    """A single tab-delimited report file with three columns of interest,
    referenced by one config column that pulls all three into separate
    output columns, shared by the multi-output tests below.
    `coverage_pct` (>= 95 to pass its QC) is parameterized so a caller
    can drive that output's QC failure independently of the other two."""
    data_file = tmp_path / "coverage.tsv"
    data_file.write_text(f"chr1\t42.5\t{coverage_pct}\t60.0\n")

    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(f"sample_id\tcoverage_tsv\nSAMPLE_001\t{data_file}\n")

    config = tmp_path / "config.yaml"
    config.write_text(
        "columns:\n"
        "  - name: sample_id\n"
        "  - name: coverage_tsv\n"
        "    file_parsing:\n"
        "      - name: mean_depth\n"
        '        command: awk -F"\\t" \'{print $2}\' "$FILE"\n'
        "        qc:\n"
        '          - {operator: ">=", value: 30}\n'
        "      - name: coverage_pct\n"
        '        command: awk -F"\\t" \'{print $3}\' "$FILE"\n'
        "        qc:\n"
        '          - {operator: ">=", value: 95}\n'
        "      - name: mean_mapq\n"
        '        command: awk -F"\\t" \'{print $4}\' "$FILE"\n'
    )
    return input_tsv, config


def test_file_parsing_multi_output_produces_multiple_columns_from_one_source(tmp_path):
    input_tsv, config = _multi_output_scenario(tmp_path)
    out = tmp_path / "out.tsv"
    transform.run_export(input_tsv, config, None, out, None, allow_file_parsing=True)

    header = table_io.read_header(out)
    rows = list(table_io.iter_rows(out))
    assert header == ["sample_id", "mean_depth", "coverage_pct", "mean_mapq"]
    assert rows == [["SAMPLE_001", "42.5", "99.98", "60.0"]]


def test_file_parsing_multi_output_qc_applies_independently_per_output(tmp_path):
    # Only one of the three outputs (coverage_pct) fails its own
    # threshold; the other two outputs from the same source column pass.
    input_tsv, config = _multi_output_scenario(tmp_path, coverage_pct="50.0")  # fails >= 95
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc_report.tsv"
    transform.run_export(input_tsv, config, None, out, qc_report, allow_file_parsing=True)

    rows = list(table_io.iter_rows(out))
    assert rows == []  # the sample is dropped: one failing output fails the whole row

    report_rows = list(table_io.iter_rows(qc_report))
    assert len(report_rows) == 1
    sample, column, output_column, *_ = report_rows[0]
    assert sample == "SAMPLE_001"
    # column identifies the shared source column; output_column identifies
    # which specific extracted output actually failed.
    assert column == "coverage_tsv"
    assert output_column == "coverage_pct"


def test_allow_file_parsing_flag_is_harmless_when_config_has_no_file_parsing(tmp_path):
    # Passing --allow-file-parsing when the config never uses it at all
    # must behave identically to not passing it -- it's a gate, not a
    # behavior switch.
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text("sample_id\tread_count\nSAMPLE_001\t5000\n")
    config = tmp_path / "config.yaml"
    config.write_text("columns:\n  - name: sample_id\n  - name: read_count\n")

    out_with_flag = tmp_path / "with_flag.tsv"
    out_without_flag = tmp_path / "without_flag.tsv"
    transform.run_export(input_tsv, config, None, out_with_flag, None, allow_file_parsing=True)
    transform.run_export(input_tsv, config, None, out_without_flag, None)
    assert out_with_flag.read_text() == out_without_flag.read_text()
