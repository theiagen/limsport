import hashlib

import pytest

from limsport import cli, table_io


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_end_to_end_no_config_no_samples_is_byte_identical(fixtures_dir, tmp_path):
    src = fixtures_dir / "input_basic.tsv"
    out = tmp_path / "out.tsv"
    rc = cli.main(["--input", str(src), "--output", str(out)])
    assert rc == 0
    assert _hash(out) == _hash(src)


def test_end_to_end_with_config_and_qc_report(fixtures_dir, tmp_path, capsys):
    out = tmp_path / "out.tsv"
    report_path = tmp_path / "report.tsv"
    rc = cli.main(
        [
            "--input",
            str(fixtures_dir / "input_basic.tsv"),
            "--config",
            str(fixtures_dir / "config_qc_range.yaml"),
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


def test_malformed_config_returns_1_without_traceback(fixtures_dir, tmp_path, capsys):
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text("columns: [this is not: valid: yaml")
    out = tmp_path / "out.tsv"
    rc = cli.main(
        [
            "--input",
            str(fixtures_dir / "input_basic.tsv"),
            "--config",
            str(bad_config),
            "--output",
            str(out),
        ]
    )
    assert rc == 1
    assert "Traceback" not in capsys.readouterr().err


def test_unknown_column_config_returns_1_no_output(fixtures_dir, tmp_path):
    out = tmp_path / "out.tsv"
    rc = cli.main(
        [
            "--input",
            str(fixtures_dir / "input_basic.tsv"),
            "--config",
            str(fixtures_dir / "config_unknown_column.yaml"),
            "--output",
            str(out),
        ]
    )
    assert rc == 1
    assert not out.exists()


def test_samples_flag_filters_through_cli(fixtures_dir, tmp_path):
    out = tmp_path / "out.tsv"
    rc = cli.main(
        [
            "--input",
            str(fixtures_dir / "input_basic.tsv"),
            "--samples",
            str(fixtures_dir / "samples_subset.txt"),
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


def test_delimiter_flag_converts_output(fixtures_dir, tmp_path):
    out = tmp_path / "out.csv"
    rc = cli.main(
        [
            "--input",
            str(fixtures_dir / "input_basic.tsv"),
            "--output",
            str(out),
            "--delimiter",
            ",",
        ]
    )
    assert rc == 0
    assert table_io.read_header(out, delimiter=",") == ["sample_id", "read_count", "status", "notes"]


def test_undetectable_delimiter_returns_1(fixtures_dir, tmp_path, capsys):
    out = tmp_path / "out.tsv"
    rc = cli.main(
        ["--input", str(fixtures_dir / "input_single_column.tsv"), "--output", str(out)]
    )
    assert rc == 1
    assert not out.exists()
    assert "could not auto-detect a delimiter" in capsys.readouterr().err


def test_unwritable_output_path_returns_1_without_traceback(fixtures_dir, tmp_path, capsys):
    # --output's parent directory doesn't exist -- argparse can't check
    # this up front (unlike --input's existing_file type), so it surfaces
    # as an OSError from the write itself, which must still be reported
    # cleanly rather than as a raw traceback.
    out = tmp_path / "does_not_exist_dir" / "out.tsv"
    rc = cli.main(["--input", str(fixtures_dir / "input_basic.tsv"), "--output", str(out)])
    assert rc == 1
    stderr = capsys.readouterr().err
    assert "Traceback" not in stderr


def test_omitting_qc_report_writes_no_report_file(fixtures_dir, tmp_path):
    out = tmp_path / "out.tsv"
    rc = cli.main(
        [
            "--input",
            str(fixtures_dir / "input_basic.tsv"),
            "--config",
            str(fixtures_dir / "config_qc_range.yaml"),
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    assert list(tmp_path.iterdir()) == [out]


def _file_parsing_config_and_input(tmp_path):
    data_file = tmp_path / "data.txt"
    data_file.write_text("hello\n")
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text(f"sample_id\tdata_path\nSAMPLE_001\t{data_file}\n")
    config = tmp_path / "config.yaml"
    config.write_text(
        "columns:\n"
        "  - name: sample_id\n"
        "  - name: data_path\n"
        "    file_parsing:\n"
        "      - name: extracted\n"
        '        command: cat "$LIMSPORT_FILE"\n'
    )
    return input_tsv, config


def test_file_parsing_without_flag_returns_1(tmp_path, capsys):
    input_tsv, config = _file_parsing_config_and_input(tmp_path)
    out = tmp_path / "out.tsv"
    rc = cli.main(["--input", str(input_tsv), "--config", str(config), "--output", str(out)])
    assert rc == 1
    assert not out.exists()
    assert "allow-file-parsing" in capsys.readouterr().err


def test_file_parsing_with_flag_succeeds(tmp_path):
    input_tsv, config = _file_parsing_config_and_input(tmp_path)
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
