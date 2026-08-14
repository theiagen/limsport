import hashlib

import pytest

from limsport import table_io, transform
from limsport.exceptions import ConfigError, InputTableError


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_no_config_no_samples_is_byte_identical(fixtures_dir, tmp_path):
    src = fixtures_dir / "input_basic.tsv"
    out = tmp_path / "out.tsv"
    transform.run_export(src, None, None, out, None)
    assert _hash(out) == _hash(src)


def test_samples_only_filters_rows_preserves_columns(fixtures_dir, tmp_path):
    out = tmp_path / "out.tsv"
    transform.run_export(
        fixtures_dir / "input_with_dupes.tsv", None, fixtures_dir / "samples_subset.txt", out, None
    )
    header = table_io.read_header(out)
    rows = list(table_io.iter_rows(out))
    assert header == ["sample_id", "read_count", "read_count", "status"]
    assert len(rows) == 1
    assert rows[0] == ["SAMPLE_001", "5000", "5000", "PASS"]


def test_config_reorders_renames_and_drops_columns(fixtures_dir, tmp_path):
    out = tmp_path / "out.tsv"
    transform.run_export(
        fixtures_dir / "input_basic.tsv", fixtures_dir / "config_basic.yaml", None, out, None
    )
    header = table_io.read_header(out)
    assert header == ["sample_id", "total_reads", "Status", "notes"]
    rows = list(table_io.iter_rows(out))
    assert len(rows) == 5


def test_unknown_config_column_raises_before_output_created(fixtures_dir, tmp_path):
    out = tmp_path / "out.tsv"
    with pytest.raises(InputTableError):
        transform.run_export(
            fixtures_dir / "input_basic.tsv",
            fixtures_dir / "config_unknown_column.yaml",
            None,
            out,
            None,
        )
    assert not out.exists()


def test_qc_range_drops_expected_samples(fixtures_dir, tmp_path):
    out = tmp_path / "out.tsv"
    transform.run_export(
        fixtures_dir / "input_basic.tsv", fixtures_dir / "config_qc_range.yaml", None, out, None
    )
    rows = list(table_io.iter_rows(out))
    passing_samples = {row[0] for row in rows}
    # SAMPLE_001 passes; SAMPLE_002 (below range), SAMPLE_003 (above range),
    # SAMPLE_004 (status FAIL), SAMPLE_005 (non-numeric) all fail.
    assert passing_samples == {"SAMPLE_001"}


def test_qc_approx_tolerance_drops_expected_samples(fixtures_dir, tmp_path):
    out = tmp_path / "out.tsv"
    transform.run_export(
        fixtures_dir / "input_basic.tsv", fixtures_dir / "config_qc_approx.yaml", None, out, None
    )
    rows = list(table_io.iter_rows(out))
    passing_samples = {row[0] for row in rows}
    # read_count ~= 5000, tolerance_percent=10 -> passing range is [4500, 5500].
    # Only SAMPLE_001 (5000) falls inside it.
    assert passing_samples == {"SAMPLE_001"}


def test_ambiguous_duplicate_column_reference_raises(fixtures_dir, tmp_path):
    out = tmp_path / "out.tsv"
    with pytest.raises(InputTableError):
        transform.run_export(
            fixtures_dir / "input_with_dupes.tsv",
            fixtures_dir / "config_dupe_reference.yaml",
            None,
            out,
            None,
        )
    assert not out.exists()


def test_unknown_sample_name_warns_but_succeeds(fixtures_dir, tmp_path, caplog):
    out = tmp_path / "out.tsv"
    with caplog.at_level("WARNING"):
        transform.run_export(
            fixtures_dir / "input_basic.tsv",
            None,
            fixtures_dir / "samples_with_unknown.txt",
            out,
            None,
        )
    rows = list(table_io.iter_rows(out))
    assert [row[0] for row in rows] == ["SAMPLE_001"]
    assert any("SAMPLE_999" in record.message for record in caplog.records)


def test_empty_sample_intersection_produces_header_only_output(fixtures_dir, tmp_path):
    samples = tmp_path / "no_match.txt"
    samples.write_text("DOES_NOT_EXIST\n")
    out = tmp_path / "out.tsv"
    transform.run_export(fixtures_dir / "input_basic.tsv", None, samples, out, None)
    assert table_io.read_header(out) == ["sample_id", "read_count", "status", "notes"]
    assert list(table_io.iter_rows(out)) == []


def test_combined_config_and_samples_compose(fixtures_dir, tmp_path):
    out = tmp_path / "out.tsv"
    transform.run_export(
        fixtures_dir / "input_basic.tsv",
        fixtures_dir / "config_qc_range.yaml",
        fixtures_dir / "samples_subset.txt",
        out,
        None,
    )
    # samples_subset.txt = SAMPLE_001, SAMPLE_003; QC range drops SAMPLE_003 (above range).
    rows = list(table_io.iter_rows(out))
    assert [row[0] for row in rows] == ["SAMPLE_001"]


def test_column_with_empty_qc_list_never_drops_sample(fixtures_dir, tmp_path):
    out = tmp_path / "out.tsv"
    transform.run_export(
        fixtures_dir / "input_basic.tsv", fixtures_dir / "config_basic.yaml", None, out, None
    )
    rows = list(table_io.iter_rows(out))
    assert len(rows) == 5


def test_no_config_no_samples_with_comma_input_stays_byte_identical(fixtures_dir, tmp_path):
    # No --delimiter given: output keeps the input's own detected delimiter
    # (comma here) rather than being forced to tab.
    src = fixtures_dir / "input_comma.csv"
    out = tmp_path / "out.csv"
    transform.run_export(src, None, None, out, None)
    assert _hash(out) == _hash(src)


def test_delimiter_override_converts_output(fixtures_dir, tmp_path):
    src = fixtures_dir / "input_basic.tsv"
    out = tmp_path / "out.csv"
    transform.run_export(src, None, None, out, None, output_delimiter=",")
    assert _hash(out) != _hash(src)  # no longer byte-identical, by design
    assert table_io.read_header(out, delimiter=",") == ["sample_id", "read_count", "status", "notes"]
    rows = list(table_io.iter_rows(out, delimiter=","))
    assert len(rows) == 5
    assert rows[0] == ["SAMPLE_001", "5000", "PASS", "ok"]


def test_delimiter_override_composes_with_config_and_samples(fixtures_dir, tmp_path):
    out = tmp_path / "out.csv"
    transform.run_export(
        fixtures_dir / "input_basic.tsv",
        fixtures_dir / "config_qc_range.yaml",
        fixtures_dir / "samples_subset.txt",
        out,
        None,
        output_delimiter=",",
    )
    rows = list(table_io.iter_rows(out, delimiter=","))
    assert [row[0] for row in rows] == ["SAMPLE_001"]


def test_undetectable_delimiter_raises(fixtures_dir, tmp_path):
    out = tmp_path / "out.tsv"
    with pytest.raises(InputTableError):
        transform.run_export(fixtures_dir / "input_single_column.tsv", None, None, out, None)
    assert not out.exists()


def test_ragged_short_row_fast_path_stays_byte_identical(fixtures_dir, tmp_path):
    # No --config/--samples: row structure is never inspected, so a short
    # row elsewhere in the file doesn't stop the copy from succeeding.
    src = fixtures_dir / "input_ragged_short.tsv"
    out = tmp_path / "out.tsv"
    transform.run_export(src, None, None, out, None)
    assert _hash(out) == _hash(src)


def test_ragged_short_row_becomes_missing_value_not_a_crash(fixtures_dir, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "columns:\n"
        "  - name: sample_id\n"
        "  - name: notes\n"
        "    qc:\n"
        '      - {operator: "=", value: ok}\n'
    )
    out = tmp_path / "out.tsv"
    transform.run_export(fixtures_dir / "input_ragged_short.tsv", config, None, out, None)
    rows = list(table_io.iter_rows(out))
    passing_samples = {row[0] for row in rows}
    # SAMPLE_004's row is missing its trailing "notes" field entirely; that
    # must surface as an ordinary "missing value" QC failure, not a crash.
    assert "SAMPLE_004" not in passing_samples


def test_ragged_long_row_raises_before_output_created(fixtures_dir, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("columns:\n  - name: sample_id\n")
    out = tmp_path / "out.tsv"
    with pytest.raises(InputTableError):
        transform.run_export(fixtures_dir / "input_ragged_long.tsv", config, None, out, None)
    assert not out.exists()


def _file_parsing_scenario(tmp_path):
    """A data file + input TSV referencing its path + config that parses
    it, shared by the file_parsing tests below."""
    data_file = tmp_path / "data.txt"
    data_file.write_text("abc:123:xyz\n")

    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(f"sample_id\tdata_path\nSAMPLE_001\t{data_file}\n")

    config = tmp_path / "config.yaml"
    config.write_text(
        "columns:\n"
        "  - name: sample_id\n"
        "  - name: data_path\n"
        "    file_parsing:\n"
        "      - name: extracted\n"
        '        command: \'cut -d: -f2 "$LIMSPORT_FILE"\'\n'
        "        qc:\n"
        '          - {operator: "=", value: "123"}\n'
    )
    return input_tsv, config


def test_file_parsing_requires_allow_flag(tmp_path):
    input_tsv, config = _file_parsing_scenario(tmp_path)
    out = tmp_path / "out.tsv"
    with pytest.raises(ConfigError, match="allow-file-parsing"):
        transform.run_export(input_tsv, config, None, out, None)  # allow_file_parsing defaults to False
    assert not out.exists()


def test_file_parsing_result_flows_through_qc_and_output(tmp_path):
    input_tsv, config = _file_parsing_scenario(tmp_path)
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
        '        command: awk -F"\\t" \'{print $2}\' "$LIMSPORT_FILE"\n'
        "        qc:\n"
        '          - {operator: ">=", value: 30}\n'
        "      - name: coverage_pct\n"
        '        command: awk -F"\\t" \'{print $3}\' "$LIMSPORT_FILE"\n'
        "        qc:\n"
        '          - {operator: ">=", value: 95}\n'
        "      - name: mean_mapq\n"
        '        command: awk -F"\\t" \'{print $4}\' "$LIMSPORT_FILE"\n'
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


def test_allow_file_parsing_flag_is_harmless_when_config_has_no_file_parsing(fixtures_dir, tmp_path):
    # Passing --allow-file-parsing when the config never uses it at all
    # must behave identically to not passing it -- it's a gate, not a
    # behavior switch.
    out_with_flag = tmp_path / "with_flag.tsv"
    out_without_flag = tmp_path / "without_flag.tsv"
    transform.run_export(
        fixtures_dir / "input_basic.tsv",
        fixtures_dir / "config_basic.yaml",
        None,
        out_with_flag,
        None,
        allow_file_parsing=True,
    )
    transform.run_export(
        fixtures_dir / "input_basic.tsv", fixtures_dir / "config_basic.yaml", None, out_without_flag, None
    )
    assert out_with_flag.read_text() == out_without_flag.read_text()


def test_no_config_no_samples_logs_no_qc_not_passed_qc(fixtures_dir, tmp_path, caplog):
    # The byte-identical fast path never runs QC at all -- the summary
    # line shouldn't claim it did.
    out = tmp_path / "out.tsv"
    with caplog.at_level("INFO"):
        transform.run_export(fixtures_dir / "input_basic.tsv", None, None, out, None)
    messages = [r.message for r in caplog.records]
    assert any("5/5" in m and "no QC configured" in m for m in messages)
    assert not any("passed QC" in m for m in messages)


def test_samples_only_no_config_logs_no_qc_not_passed_qc(fixtures_dir, tmp_path, caplog):
    # Sample-list filtering with no --config also never runs QC.
    out = tmp_path / "out.tsv"
    with caplog.at_level("INFO"):
        transform.run_export(
            fixtures_dir / "input_basic.tsv", None, fixtures_dir / "samples_subset.txt", out, None
        )
    messages = [r.message for r in caplog.records]
    # samples_subset.txt requests 2 of the 5 samples in input_basic.tsv
    assert any("2/5" in m and "no QC configured" in m for m in messages)
    assert not any("passed QC" in m for m in messages)


def test_config_given_still_logs_passed_qc(fixtures_dir, tmp_path, caplog):
    out = tmp_path / "out.tsv"
    with caplog.at_level("INFO"):
        transform.run_export(
            fixtures_dir / "input_basic.tsv", fixtures_dir / "config_qc_range.yaml", None, out, None
        )
    messages = [r.message for r in caplog.records]
    assert any("passed QC" in m for m in messages)
    assert not any("no QC configured" in m for m in messages)


def test_no_config_writes_no_qc_report_even_if_path_given(fixtures_dir, tmp_path):
    # Fast path (no --config, no --samples): there's no QC to report on,
    # so --qc-report shouldn't produce a file at all, not even empty.
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc_report.tsv"
    transform.run_export(fixtures_dir / "input_basic.tsv", None, None, out, qc_report)
    assert not qc_report.exists()


def test_samples_only_no_config_writes_no_qc_report_even_if_path_given(fixtures_dir, tmp_path):
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc_report.tsv"
    transform.run_export(
        fixtures_dir / "input_basic.tsv", None, fixtures_dir / "samples_subset.txt", out, qc_report
    )
    assert not qc_report.exists()


def test_config_given_still_writes_qc_report(fixtures_dir, tmp_path):
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc_report.tsv"
    transform.run_export(
        fixtures_dir / "input_basic.tsv", fixtures_dir / "config_qc_range.yaml", None, out, qc_report
    )
    assert qc_report.exists()


def test_config_with_no_qc_rules_still_writes_header_only_report(fixtures_dir, tmp_path):
    # config_basic.yaml renames columns but has no `qc:` rules anywhere --
    # unlike the no-config case, a config was still given, so the report
    # file should exist (header-only), not be skipped.
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc_report.tsv"
    transform.run_export(
        fixtures_dir / "input_basic.tsv", fixtures_dir / "config_basic.yaml", None, out, qc_report
    )
    assert qc_report.exists()
    assert list(table_io.iter_rows(qc_report)) == []
