"""End-to-end file_parsing tests through pipeline.run_export -- unit-level
coverage of the file_parsing module itself lives in test_file_parsing.py."""

import logging

import pytest
from factories import file_parsing_scenario

from limsport import file_parsing, pipeline, table_io
from limsport.exceptions import ConfigError, FileParsingError, ToolNotFoundError


def _cut_scenario(tmp_path):
    """The colon-delimited data file + `cut`-and-QC config shared by the
    file_parsing tests below."""
    return file_parsing_scenario(
        tmp_path,
        data_content="abc:123:xyz\n",
        command="'cut -d: -f2 \"$FILE\"'",
        qc_yaml='        qc:\n          - {operator: "=", value: "123"}\n',
    )


def test_file_parsing_requires_allow_flag(tmp_path):
    input_tsv, config = _cut_scenario(tmp_path)
    out = tmp_path / "out.tsv"
    with pytest.raises(ConfigError, match="allow-file-parsing"):
        pipeline.run_export(
            input_tsv, config, None, out, None
        )  # allow_file_parsing defaults to False
    assert not out.exists()


def test_file_parsing_result_flows_through_qc_and_output(tmp_path):
    input_tsv, config = _cut_scenario(tmp_path)
    out = tmp_path / "out.tsv"
    pipeline.run_export(input_tsv, config, None, out, None, allow_file_parsing=True)

    header = table_io.get_input_header(out)
    rows = list(table_io.iter_rows(out))
    assert header == ["sample_id", "extracted"]
    # The parsed value ("123", extracted from the file) is what's output and
    # what QC saw -- not the raw file path, and the sample passes because
    # the QC rule (`= "123"`) is checked against the parsed result.
    assert rows == [["SAMPLE_001", "123"]]


def _mixed_scenario(tmp_path, command, extra_qc=""):
    """Two samples whose data files differ only in content, so `command` can be
    written to succeed for one and fail for the other."""
    good = tmp_path / "good.txt"
    good.write_text("123\n")
    bad = tmp_path / "bad.txt"
    bad.write_text("nope\n")
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(
        f"sample_id\tdata_path\nSAMPLE_GOOD\t{good}\nSAMPLE_BAD\t{bad}\n"
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "columns:\n"
        "  - input_column: sample_id\n"
        "  - input_column: data_path\n"
        "    file_parsing:\n"
        "      - output_column: extracted\n"
        f"        command: {command}\n" + extra_qc
    )
    return input_tsv, config


def test_file_parsing_failure_on_every_row_aborts_the_run(tmp_path):
    # One bad file among many is data and fails only its own row, but *every* file
    # failing means the command or path template is broken -- there'd be nothing
    # left to write, and a header-only table plus exit 0 would call that a success.
    input_tsv, config = _cut_scenario(tmp_path)
    config.write_text(config.read_text().replace("'cut -d: -f2 \"$FILE\"'", "exit 1"))
    out = tmp_path / "out.tsv"

    with pytest.raises(FileParsingError, match="every row"):
        pipeline.run_export(input_tsv, config, None, out, None, allow_file_parsing=True)
    assert not out.exists()


def test_every_row_failing_aborts_even_under_a_generous_failure_limit(tmp_path):
    # --max-file-parsing-failures tunes how much genuinely bad data to tolerate.
    # "did anything work at all" is a different question, so a high limit does not
    # buy you an empty output.
    input_tsv, config = _mixed_scenario(tmp_path, "exit 1")
    with pytest.raises(FileParsingError, match="every row"):
        pipeline.run_export(
            input_tsv,
            config,
            None,
            tmp_path / "out.tsv",
            None,
            allow_file_parsing=True,
            max_file_parsing_failures=999,
        )


def test_file_parsing_failure_drops_only_the_failing_row(tmp_path):
    # grep exits 1 when it finds nothing, so SAMPLE_BAD's file fails and
    # SAMPLE_GOOD's does not
    input_tsv, config = _mixed_scenario(tmp_path, 'grep 123 "$FILE"')
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc.tsv"

    pipeline.run_export(
        input_tsv, config, None, out, qc_report, allow_file_parsing=True
    )

    # the whole point: one unreadable file no longer costs you every other sample
    assert list(table_io.iter_rows(out)) == [["SAMPLE_GOOD", "123"]]
    report_rows = list(table_io.iter_rows(qc_report))
    assert [r[0] for r in report_rows] == ["SAMPLE_BAD"]


def test_file_parsing_failure_fails_the_row_even_with_no_qc_configured(tmp_path):
    # There is no condition to fail, but there is also no value to write, so the
    # row still has to be dropped rather than written with a blank cell.
    input_tsv, config = _mixed_scenario(tmp_path, 'grep 123 "$FILE"')
    out = tmp_path / "out.tsv"

    pipeline.run_export(input_tsv, config, None, out, None, allow_file_parsing=True)

    assert list(table_io.iter_rows(out)) == [["SAMPLE_GOOD", "123"]]


def test_file_parsing_failure_reports_the_path_it_could_not_parse(tmp_path):
    input_tsv, config = _mixed_scenario(tmp_path, 'grep 123 "$FILE"')
    qc_report = tmp_path / "qc.tsv"
    pipeline.run_export(
        input_tsv,
        config,
        None,
        tmp_path / "out.tsv",
        qc_report,
        allow_file_parsing=True,
    )
    sample, input_column, output_column, _, _, actual, reason = next(
        iter(table_io.iter_rows(qc_report))
    )
    assert (sample, input_column, output_column) == (
        "SAMPLE_BAD",
        "data_path",
        "extracted",
    )
    # `actual` names the path that wouldn't parse, not the value that never existed
    assert actual.endswith("bad.txt")
    assert "exit 1" in reason


def test_file_parsing_warns_about_the_rows_it_dropped(tmp_path, caplog):
    input_tsv, config = _mixed_scenario(tmp_path, 'grep 123 "$FILE"')
    with caplog.at_level(logging.WARNING, logger="limsport"):
        pipeline.run_export(
            input_tsv,
            config,
            None,
            tmp_path / "out.tsv",
            None,
            allow_file_parsing=True,
        )
    assert any(
        "1/2 samples dropped" in r.message and "file_parsing" in r.message
        for r in caplog.records
    )


def test_max_file_parsing_failures_zero_restores_the_fatal_behaviour(tmp_path):
    input_tsv, config = _mixed_scenario(tmp_path, 'grep 123 "$FILE"')
    out = tmp_path / "out.tsv"
    # an export already sitting at the output path -- a re-run that aborts must not
    # damage it, which is the whole reason rows are staged before being committed
    out.write_text("PREVIOUS EXPORT\n")

    with pytest.raises(FileParsingError, match="max-file-parsing-failures"):
        pipeline.run_export(
            input_tsv,
            config,
            None,
            out,
            None,
            allow_file_parsing=True,
            max_file_parsing_failures=0,
        )

    assert out.read_text() == "PREVIOUS EXPORT\n"
    assert not out.with_name(out.name + ".tmp").exists()


def test_max_file_parsing_failures_tolerates_up_to_the_limit(tmp_path):
    input_tsv, config = _mixed_scenario(tmp_path, 'grep 123 "$FILE"')
    out = tmp_path / "out.tsv"
    pipeline.run_export(
        input_tsv,
        config,
        None,
        out,
        None,
        allow_file_parsing=True,
        max_file_parsing_failures=1,
    )
    assert list(table_io.iter_rows(out)) == [["SAMPLE_GOOD", "123"]]


def test_missing_cloud_tool_still_aborts_the_whole_run(tmp_path, monkeypatch):
    # A missing gcloud fails every row identically, so it stays fatal rather than
    # producing one indistinguishable QC failure per sample.
    monkeypatch.setattr(file_parsing.shutil, "which", lambda tool: None)
    data = tmp_path / "data.txt"
    data.write_text("123\n")
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text("sample_id\tdata_path\nSAMPLE_001\tgs://bucket/f.txt\n")
    config = tmp_path / "config.yaml"
    config.write_text(
        "columns:\n"
        "  - input_column: sample_id\n"
        "  - input_column: data_path\n"
        "    file_parsing:\n"
        "      - output_column: extracted\n"
        '        command: cat "$FILE"\n'
    )
    out = tmp_path / "out.tsv"
    with pytest.raises(ToolNotFoundError, match="gcloud"):
        pipeline.run_export(input_tsv, config, None, out, None, allow_file_parsing=True)
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
        "columns:\n  - input_column: sample_id\n  - input_column: data_path\n    file_parsing:\n"
        "      - output_column: extracted\n        command: cat\n"
    )

    calls = []
    monkeypatch.setattr(
        file_parsing,
        "run",
        lambda outputs, original_path: calls.append(original_path) or ["ok"],
    )

    out = tmp_path / "out.tsv"
    pipeline.run_export(input_tsv, config, samples, out, None, allow_file_parsing=True)
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
    input_tsv.write_text(
        f"sample_id\tpath_a\tpath_b\nSAMPLE_001\t{data_file}\t{data_file}\n"
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "columns:\n"
        "  - input_column: sample_id\n"
        "  - input_column: path_a\n"
        "    file_parsing:\n      - output_column: a_out\n        command: cat\n"
        "  - input_column: path_b\n"
        "    file_parsing:\n      - output_column: b_out\n        command: cat\n"
    )

    calls = []
    monkeypatch.setattr(
        file_parsing,
        "run",
        lambda outputs, original_path: calls.append(original_path) or ["ok"],
    )

    out = tmp_path / "out.tsv"
    pipeline.run_export(input_tsv, config, None, out, None, allow_file_parsing=True)
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
        "  - input_column: sample_id\n"
        "  - input_column: coverage_tsv\n"
        "    file_parsing:\n"
        "      - output_column: mean_depth\n"
        '        command: awk -F"\\t" \'{print $2}\' "$FILE"\n'
        "        qc:\n"
        '          - {operator: ">=", value: 30}\n'
        "      - output_column: coverage_pct\n"
        '        command: awk -F"\\t" \'{print $3}\' "$FILE"\n'
        "        qc:\n"
        '          - {operator: ">=", value: 95}\n'
        "      - output_column: mean_mapq\n"
        '        command: awk -F"\\t" \'{print $4}\' "$FILE"\n'
    )
    return input_tsv, config


def test_file_parsing_multi_output_produces_multiple_columns_from_one_source(tmp_path):
    input_tsv, config = _multi_output_scenario(tmp_path)
    out = tmp_path / "out.tsv"
    pipeline.run_export(input_tsv, config, None, out, None, allow_file_parsing=True)

    header = table_io.get_input_header(out)
    rows = list(table_io.iter_rows(out))
    assert header == ["sample_id", "mean_depth", "coverage_pct", "mean_mapq"]
    assert rows == [["SAMPLE_001", "42.5", "99.98", "60.0"]]


def test_file_parsing_multi_output_qc_applies_independently_per_output(tmp_path):
    # Only one of the three outputs (coverage_pct) fails its own
    # threshold; the other two outputs from the same source column pass.
    input_tsv, config = _multi_output_scenario(
        tmp_path, coverage_pct="50.0"
    )  # fails >= 95
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc_report.tsv"
    pipeline.run_export(
        input_tsv, config, None, out, qc_report, allow_file_parsing=True
    )

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
    config.write_text(
        "columns:\n  - input_column: sample_id\n  - input_column: read_count\n"
    )

    out_with_flag = tmp_path / "with_flag.tsv"
    out_without_flag = tmp_path / "without_flag.tsv"
    pipeline.run_export(
        input_tsv, config, None, out_with_flag, None, allow_file_parsing=True
    )
    pipeline.run_export(input_tsv, config, None, out_without_flag, None)
    assert out_with_flag.read_text() == out_without_flag.read_text()
