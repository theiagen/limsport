import pytest

from limsport import cli, table_io
from factories import (
    config_qc_range,
    config_unknown_column,
    file_parsing_scenario,
    input_basic,
    input_single_column,
    samples_subset,
)


def test_end_to_end_no_config_no_samples_writes_nothing(tmp_path):
    # Nothing would change, so nothing is written -- writing a copy under
    # a different name could look like a transformation happened when
    # none did.
    src = input_basic(tmp_path)
    out = tmp_path / "out.tsv"
    rc = cli.main(["--input", str(src), "--output", str(out)])
    assert rc == 0
    assert not out.exists()


def test_output_defaults_to_limsport_tsv_in_the_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    input_path = input_basic(tmp_path)
    config_path = config_qc_range(tmp_path)
    rc = cli.main(["--input", str(input_path), "--config", str(config_path)])
    assert rc == 0
    assert (tmp_path / "limsport.tsv").exists()


def test_end_to_end_with_config_and_qc_report(tmp_path, capsys):
    out = tmp_path / "out.tsv"
    report_path = tmp_path / "report.tsv"
    rc = cli.main(
        [
            "--input",
            str(input_basic(tmp_path)),
            "--config",
            str(config_qc_range(tmp_path)),
            "--output",
            str(out),
            "--qc-report",
            str(report_path),
        ]
    )
    assert rc == 0
    rows = list(table_io.iter_rows(out))
    assert [row[0] for row in rows] == ["SAMPLE_001"]

    report_rows = list(table_io.iter_rows(report_path))
    assert len(report_rows) == 4  # SAMPLE_002, 003, 004, 005 each fail exactly one configured column

    stderr = capsys.readouterr().err
    assert "1/5 samples passed QC" in stderr


def test_missing_required_input_exits_2(tmp_path):
    out = tmp_path / "out.tsv"
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--output", str(out)])
    assert exc_info.value.code == 2


def test_nonexistent_input_path_exits_2(tmp_path, capsys):
    out = tmp_path / "out.tsv"
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--input", str(tmp_path / "does_not_exist.tsv"), "--output", str(out)])
    assert exc_info.value.code == 2
    assert "file not found" in capsys.readouterr().err


def test_malformed_config_returns_1_without_traceback(tmp_path, capsys):
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text("columns: [this is not: valid: yaml")
    out = tmp_path / "out.tsv"
    rc = cli.main(
        [
            "--input",
            str(input_basic(tmp_path)),
            "--config",
            str(bad_config),
            "--output",
            str(out),
        ]
    )
    assert rc == 1
    assert "Traceback" not in capsys.readouterr().err


def test_unknown_column_config_returns_1_no_output(tmp_path):
    out = tmp_path / "out.tsv"
    rc = cli.main(
        [
            "--input",
            str(input_basic(tmp_path)),
            "--config",
            str(config_unknown_column(tmp_path)),
            "--output",
            str(out),
        ]
    )
    assert rc == 1
    assert not out.exists()


def test_samples_flag_filters_through_cli(tmp_path):
    out = tmp_path / "out.tsv"
    rc = cli.main(
        [
            "--input",
            str(input_basic(tmp_path)),
            "--samples",
            str(samples_subset(tmp_path)),
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    rows = list(table_io.iter_rows(out))
    assert {row[0] for row in rows} == {"SAMPLE_001", "SAMPLE_003"}


def test_help_lists_all_flags(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    for flag in [
        "--input",
        "--config",
        "--samples",
        "--output",
        "--qc-report",
        "--delimiter",
        "--allow-file-parsing",
    ]:
        assert flag in out


def test_delimiter_flag_converts_output(tmp_path):
    out = tmp_path / "out.csv"
    rc = cli.main(
        [
            "--input",
            str(input_basic(tmp_path)),
            "--output",
            str(out),
            "--delimiter",
            ",",
        ]
    )
    assert rc == 0
    assert table_io.read_header(out, delimiter=",") == ["sample_id", "read_count", "status", "notes"]


def test_undetectable_delimiter_returns_1(tmp_path, capsys):
    out = tmp_path / "out.tsv"
    rc = cli.main(
        ["--input", str(input_single_column(tmp_path)), "--output", str(out)]
    )
    assert rc == 1
    assert not out.exists()
    assert "could not auto-detect a delimiter" in capsys.readouterr().err


def test_unwritable_output_path_returns_1_without_traceback(tmp_path, capsys):
    # --output's parent directory doesn't exist -- argparse can't check
    # this up front (unlike --input's existing_file type), so it surfaces
    # as an OSError from the write itself, which must still be reported
    # cleanly rather than as a raw traceback. A --config is required here
    # so there's actually something to write -- otherwise this hits the
    # no-op path, which never touches --output at all.
    out = tmp_path / "does_not_exist_dir" / "out.tsv"
    rc = cli.main(
        [
            "--input",
            str(input_basic(tmp_path)),
            "--config",
            str(config_qc_range(tmp_path)),
            "--output",
            str(out),
        ]
    )
    assert rc == 1
    stderr = capsys.readouterr().err
    assert "Traceback" not in stderr


def test_omitting_qc_report_defaults_to_qc_report_tsv_in_the_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "out.tsv"
    input_path = input_basic(tmp_path)
    config_path = config_qc_range(tmp_path)
    rc = cli.main(
        [
            "--input",
            str(input_path),
            "--config",
            str(config_path),
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    assert (tmp_path / "qc_report.tsv").exists()


def test_file_parsing_without_flag_returns_1(tmp_path, capsys):
    input_tsv, config = file_parsing_scenario(
        tmp_path, data_content="hello\n", command='cat "$FILE"'
    )
    out = tmp_path / "out.tsv"
    rc = cli.main(["--input", str(input_tsv), "--config", str(config), "--output", str(out)])
    assert rc == 1
    assert not out.exists()
    assert "allow-file-parsing" in capsys.readouterr().err


def test_file_parsing_with_flag_succeeds(tmp_path):
    input_tsv, config = file_parsing_scenario(
        tmp_path, data_content="hello\n", command='cat "$FILE"'
    )
    out = tmp_path / "out.tsv"
    rc = cli.main(
        [
            "--input",
            str(input_tsv),
            "--config",
            str(config),
            "--output",
            str(out),
            "--allow-file-parsing",
        ]
    )
    assert rc == 0
    rows = list(table_io.iter_rows(out))
    assert rows == [["SAMPLE_001", "hello"]]
