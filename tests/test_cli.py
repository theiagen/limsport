from importlib.metadata import version
from pathlib import Path

import pytest
from factories import (
    config_missing_column,
    config_qc_range,
    file_parsing_scenario,
    input_basic,
    input_single_column,
    samples_subset,
)

from limsport import cli, table_io


def test_end_to_end_no_config_no_samples_writes_nothing(tmp_path):
    # Nothing would change, so nothing is written -- writing a copy under
    # a different name could look like a transformation happened when
    # none did.
    src = input_basic(tmp_path)
    out = tmp_path / "out_lims_report.tsv"
    rc = cli.main(["--input", str(src), "--output-prefix", str(out.parent / "out")])
    assert rc == 0
    assert not out.exists()


def test_output_defaults_to_limsport_lims_report_tsv_in_the_current_directory(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    input_path = input_basic(tmp_path)
    config_path = config_qc_range(tmp_path)
    rc = cli.main(["--input", str(input_path), "--config", str(config_path)])
    assert rc == 0
    assert (tmp_path / "limsport_lims_report.tsv").exists()


def test_end_to_end_with_config_and_qc_report(tmp_path, capsys):
    out = tmp_path / "out_lims_report.tsv"
    report_path = tmp_path / "out_qc_fail_report.tsv"
    rc = cli.main(
        [
            "--input",
            str(input_basic(tmp_path)),
            "--config",
            str(config_qc_range(tmp_path)),
            "--output-prefix",
            str(out.parent / "out"),
        ]
    )
    assert rc == 0
    rows = list(table_io.iter_rows(out))
    assert [row[0] for row in rows] == ["SAMPLE_001"]

    report_rows = list(table_io.iter_rows(report_path))
    assert (
        len(report_rows) == 4
    )  # SAMPLE_002, 003, 004, 005 each fail exactly one configured column

    stderr = capsys.readouterr().err
    assert "1/5 samples passed QC" in stderr


def test_missing_required_input_exits_2(tmp_path):
    out = tmp_path / "out_lims_report.tsv"
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--output-prefix", str(out.parent / "out")])
    assert exc_info.value.code == 2


def test_nonexistent_input_path_exits_2(tmp_path, capsys):
    out = tmp_path / "out_lims_report.tsv"
    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "--input",
                str(tmp_path / "does_not_exist.tsv"),
                "--output-prefix",
                str(out.parent / "out"),
            ]
        )
    assert exc_info.value.code == 2
    assert "file not found" in capsys.readouterr().err


def test_malformed_config_returns_1_without_traceback(tmp_path, capsys):
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text("sample_qc: [this is not: valid: yaml")
    out = tmp_path / "out_lims_report.tsv"
    rc = cli.main(
        [
            "--input",
            str(input_basic(tmp_path)),
            "--config",
            str(bad_config),
            "--output-prefix",
            str(out.parent / "out"),
        ]
    )
    assert rc == 1
    assert "Traceback" not in capsys.readouterr().err


def test_missing_column_config_returns_1_no_output(tmp_path):
    out = tmp_path / "out_lims_report.tsv"
    rc = cli.main(
        [
            "--input",
            str(input_basic(tmp_path)),
            "--config",
            str(config_missing_column(tmp_path)),
            "--output-prefix",
            str(out.parent / "out"),
        ]
    )
    assert rc == 1
    assert not out.exists()


def test_samples_flag_filters_through_cli(tmp_path):
    out = tmp_path / "out_lims_report.tsv"
    rc = cli.main(
        [
            "--input",
            str(input_basic(tmp_path)),
            "--samples",
            str(samples_subset(tmp_path)),
            "--output-prefix",
            str(out.parent / "out"),
        ]
    )
    assert rc == 0
    rows = list(table_io.iter_rows(out))
    assert {row[0] for row in rows} == {"SAMPLE_001", "SAMPLE_003"}


@pytest.mark.parametrize("verbose", [True, False])
def test_verbose_toggles_debug_logging(tmp_path, capsys, verbose):
    args = [
        "--input",
        str(input_basic(tmp_path)),
        "--config",
        str(config_qc_range(tmp_path)),
        "--output-prefix",
        str(tmp_path / "out"),
    ]
    rc = cli.main(args + ["--verbose"] if verbose else args)
    assert rc == 0
    assert ("DEBUG:" in capsys.readouterr().err) is verbose


def test_help_lists_all_flags(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    for flag in [
        "--input",
        "--config",
        "--samples",
        "--output-prefix",
        "--output-delimiter",
        "--include-qc-fails",
        "--allow-file-parsing",
        "--max-file-parsing-failures",
        "--verbose",
    ]:
        assert flag in out


def test_version_prints_installed_version(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--version"])
    assert exc_info.value.code == 0
    assert capsys.readouterr().out == f"limsport {version('limsport')}\n"


def test_delimiter_flag_converts_output(tmp_path):
    out = tmp_path / "out_lims_report.csv"
    rc = cli.main(
        [
            "--input",
            str(input_basic(tmp_path)),
            "--output-prefix",
            str(out.parent / "out"),
            "--output-delimiter",
            ",",
        ]
    )
    assert rc == 0
    assert table_io.get_input_header(out, delimiter=",") == [
        "sample_id",
        "read_count",
        "status",
        "notes",
    ]


def test_undetectable_delimiter_returns_1(tmp_path, capsys):
    out = tmp_path / "out_lims_report.tsv"
    rc = cli.main(
        [
            "--input",
            str(input_single_column(tmp_path)),
            "--output-prefix",
            str(out.parent / "out"),
        ]
    )
    assert rc == 1
    assert not out.exists()
    assert "could not auto-detect a delimiter" in capsys.readouterr().err


def test_unwritable_output_path_returns_1_without_traceback(tmp_path, capsys):
    # --output-prefix's parent directory doesn't exist -- argparse can't check
    # this up front (unlike --input's existing_file type), so it surfaces
    # as an OSError from the write itself, which must still be reported
    # cleanly rather than as a raw traceback. A --config is required here
    # so there's actually something to write -- otherwise this hits the
    # no-op path, which never touches the output at all.
    out = tmp_path / "does_not_exist_dir" / "out_lims_report.tsv"
    rc = cli.main(
        [
            "--input",
            str(input_basic(tmp_path)),
            "--config",
            str(config_qc_range(tmp_path)),
            "--output-prefix",
            str(out.parent / "out"),
        ]
    )
    assert rc == 1
    stderr = capsys.readouterr().err
    assert "Traceback" not in stderr


def test_qc_report_named_from_prefix_and_written_with_output_delimiter(tmp_path):
    rc = cli.main(
        [
            "--input",
            str(input_basic(tmp_path)),
            "--config",
            str(config_qc_range(tmp_path)),
            "--output-prefix",
            str(tmp_path / "out"),
            "--output-delimiter",
            ",",
        ]
    )
    assert rc == 0
    report_path = tmp_path / "out_qc_fail_report.csv"
    assert table_io.get_input_header(report_path, delimiter=",")[0] == "sample"


def test_file_parsing_without_flag_returns_1(tmp_path, capsys):
    input_tsv, config = file_parsing_scenario(
        tmp_path, data_content="hello\n", command='cat "$FILE"'
    )
    out = tmp_path / "out_lims_report.tsv"
    rc = cli.main(
        [
            "--input",
            str(input_tsv),
            "--config",
            str(config),
            "--output-prefix",
            str(out.parent / "out"),
        ]
    )
    assert rc == 1
    assert not out.exists()
    assert "allow-file-parsing" in capsys.readouterr().err


def test_file_parsing_with_flag_succeeds(tmp_path):
    input_tsv, config = file_parsing_scenario(
        tmp_path, data_content="hello\n", command='cat "$FILE"'
    )
    out = tmp_path / "out_lims_report.tsv"
    rc = cli.main(
        [
            "--input",
            str(input_tsv),
            "--config",
            str(config),
            "--output-prefix",
            str(out.parent / "out"),
            "--allow-file-parsing",
        ]
    )
    assert rc == 0
    rows = list(table_io.iter_rows(out))
    assert rows == [["SAMPLE_001", "hello"]]


@pytest.mark.parametrize(
    ("delimiter", "expected"),
    [("\t", "out/run.tsv"), (",", "out/run.csv"), ("|", "out/run.txt")],
)
def test_output_path_extension_follows_delimiter(delimiter, expected):
    assert cli.output_path("out/run", delimiter) == Path(expected)


def test_output_path_puts_suffix_before_extension():
    assert cli.output_path("run", ",", "_qc_fail_report") == Path(
        "run_qc_fail_report.csv"
    )


def test_qc_summary_lists_every_sample_with_its_status(tmp_path):
    rc = cli.main(
        [
            "--input",
            str(input_basic(tmp_path)),
            "--config",
            str(config_qc_range(tmp_path)),
            "--output-prefix",
            str(tmp_path / "out"),
            "--output-delimiter",
            ",",
        ]
    )
    assert rc == 0
    summary_path = tmp_path / "out_qc_summary.csv"
    assert table_io.get_input_header(summary_path, delimiter=",") == [
        "sample",
        "qc_status",
    ]
    assert list(table_io.iter_rows(summary_path, ",")) == [
        ["SAMPLE_001", "PASS"],
        ["SAMPLE_002", "FAIL"],
        ["SAMPLE_003", "FAIL"],
        ["SAMPLE_004", "FAIL"],
        ["SAMPLE_005", "FAIL"],
    ]


def test_max_file_parsing_failures_defaults_to_no_limit():
    args = cli.build_parser().parse_args(["--input", __file__])
    assert args.max_file_parsing_failures is None


def test_max_file_parsing_failures_accepts_zero():
    # 0 is meaningful -- it restores the old abort-on-first-failure behaviour --
    # so it must not be confused with "no limit"
    args = cli.build_parser().parse_args(
        ["--input", __file__, "--max-file-parsing-failures", "0"]
    )
    assert args.max_file_parsing_failures == 0


@pytest.mark.parametrize("value", ["-1", "two", "1.5"])
def test_max_file_parsing_failures_rejects_bad_values(value, capsys):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            ["--input", __file__, "--max-file-parsing-failures", value]
        )
    assert "--max-file-parsing-failures" in capsys.readouterr().err
