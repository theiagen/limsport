import pytest
from factories import (
    config_basic,
    config_qc_range,
    config_unknown_column,
    hash_file,
    input_basic,
    input_comma,
    input_ragged_long,
    input_ragged_short,
    input_single_column,
    input_with_dupes,
    samples_subset,
)

from limsport import table_io, transform
from limsport.exceptions import InputTableError


def config_qc_approx(tmp_path):
    path = tmp_path / "config_qc_approx.yaml"
    path.write_text(
        "columns:\n"
        "  - input_column: sample_id\n"
        "  - input_column: read_count\n"
        "    qc:\n"
        '      - {operator: "~=", value: 5000, tolerance_percent: 10}\n'
    )
    return path


def config_dupe_reference(tmp_path):
    path = tmp_path / "config_dupe_reference.yaml"
    path.write_text(
        "columns:\n  - input_column: sample_id\n  - input_column: read_count\n"
    )
    return path


def samples_with_unknown(tmp_path):
    path = tmp_path / "samples_with_unknown.txt"
    path.write_text("SAMPLE_001\nSAMPLE_999\n")
    return path


def test_no_config_no_samples_writes_nothing(tmp_path):
    src = input_basic(tmp_path)
    out = tmp_path / "out.tsv"
    transform.run_export(src, None, None, out, None)
    assert not out.exists()


def test_samples_only_filters_rows_preserves_columns(tmp_path):
    out = tmp_path / "out.tsv"
    transform.run_export(
        input_with_dupes(tmp_path), None, samples_subset(tmp_path), out, None
    )
    header = table_io.get_input_header(out)
    rows = list(table_io.iter_rows(out))
    assert header == ["sample_id", "read_count", "read_count", "status"]
    assert len(rows) == 1
    assert rows[0] == ["SAMPLE_001", "5000", "5000", "PASS"]


def test_config_reorders_output_columns_and_drops_columns(tmp_path):
    out = tmp_path / "out.tsv"
    transform.run_export(input_basic(tmp_path), config_basic(tmp_path), None, out, None)
    header = table_io.get_input_header(out)
    assert header == ["sample_id", "total_reads", "Status", "notes"]
    rows = list(table_io.iter_rows(out))
    assert len(rows) == 5


def test_config_order_wins_over_input_order_when_they_differ(tmp_path):
    # input_basic's header is sample_id, read_count, status, notes -- this
    # config lists the same columns in a deliberately different order, to
    # prove the output follows the config's declared order rather than
    # coincidentally matching the input's (config_basic above happens to
    # list its columns in input order, so it can't tell the two apart).
    # See the DECISION POINT in run_export if this ever needs to flip to
    # input order instead.
    config = tmp_path / "config.yaml"
    config.write_text(
        "columns:\n"
        "  - input_column: status\n"
        "  - input_column: sample_id\n"
        "  - input_column: notes\n"
        "  - input_column: read_count\n"
        "    output_column: total_reads\n"
    )
    out = tmp_path / "out.tsv"
    transform.run_export(input_basic(tmp_path), config, None, out, None)
    header = table_io.get_input_header(out)
    assert header == ["status", "sample_id", "notes", "total_reads"]


def test_unknown_config_column_raises_before_output_created(tmp_path):
    out = tmp_path / "out.tsv"
    with pytest.raises(InputTableError):
        transform.run_export(
            input_basic(tmp_path),
            config_unknown_column(tmp_path),
            None,
            out,
            None,
        )
    assert not out.exists()


def test_qc_range_drops_expected_samples(tmp_path):
    out = tmp_path / "out.tsv"
    transform.run_export(
        input_basic(tmp_path), config_qc_range(tmp_path), None, out, None
    )
    rows = list(table_io.iter_rows(out))
    passing_samples = {row[0] for row in rows}
    # SAMPLE_001 passes; SAMPLE_002 (below range), SAMPLE_003 (above range),
    # SAMPLE_004 (status FAIL), SAMPLE_005 (non-numeric) all fail.
    assert passing_samples == {"SAMPLE_001"}


def test_qc_approx_tolerance_drops_expected_samples(tmp_path):
    out = tmp_path / "out.tsv"
    transform.run_export(
        input_basic(tmp_path), config_qc_approx(tmp_path), None, out, None
    )
    rows = list(table_io.iter_rows(out))
    passing_samples = {row[0] for row in rows}
    # read_count ~= 5000, tolerance_percent=10 -> passing range is [4500, 5500].
    # Only SAMPLE_001 (5000) falls inside it.
    assert passing_samples == {"SAMPLE_001"}


def test_ambiguous_duplicate_column_reference_raises(tmp_path):
    out = tmp_path / "out.tsv"
    with pytest.raises(InputTableError):
        transform.run_export(
            input_with_dupes(tmp_path),
            config_dupe_reference(tmp_path),
            None,
            out,
            None,
        )
    assert not out.exists()


def test_unknown_sample_name_warns_but_succeeds(tmp_path, caplog):
    out = tmp_path / "out.tsv"
    with caplog.at_level("WARNING"):
        transform.run_export(
            input_basic(tmp_path),
            None,
            samples_with_unknown(tmp_path),
            out,
            None,
        )
    rows = list(table_io.iter_rows(out))
    assert [row[0] for row in rows] == ["SAMPLE_001"]
    assert any("SAMPLE_999" in record.message for record in caplog.records)


def test_empty_sample_intersection_produces_header_only_output(tmp_path):
    samples = tmp_path / "no_match.txt"
    samples.write_text("DOES_NOT_EXIST\n")
    out = tmp_path / "out.tsv"
    transform.run_export(input_basic(tmp_path), None, samples, out, None)
    assert table_io.get_input_header(out) == [
        "sample_id",
        "read_count",
        "status",
        "notes",
    ]
    assert list(table_io.iter_rows(out)) == []


def test_combined_config_and_samples_compose(tmp_path):
    out = tmp_path / "out.tsv"
    transform.run_export(
        input_basic(tmp_path),
        config_qc_range(tmp_path),
        samples_subset(tmp_path),
        out,
        None,
    )
    # samples_subset = SAMPLE_001, SAMPLE_003; QC range drops SAMPLE_003 (above range).
    rows = list(table_io.iter_rows(out))
    assert [row[0] for row in rows] == ["SAMPLE_001"]


def test_column_with_empty_qc_list_never_drops_sample(tmp_path):
    out = tmp_path / "out.tsv"
    transform.run_export(input_basic(tmp_path), config_basic(tmp_path), None, out, None)
    rows = list(table_io.iter_rows(out))
    assert len(rows) == 5


def test_no_config_no_samples_converts_non_tab_input_to_tab_by_default(tmp_path):
    # No --delimiter given means the default, tab -- for a non-tab input
    # (comma here), that's a real change from the input, so it's written
    # (converted), not treated as a no-op.
    src = input_comma(tmp_path)
    out = tmp_path / "out.tsv"
    transform.run_export(src, None, None, out, None)
    assert table_io.get_input_header(out) == ["sample_id", "read_count", "status"]
    assert next(iter(table_io.iter_rows(out))) == ["SAMPLE_001", "5000", "PASS"]


def test_delimiter_override_converts_output(tmp_path):
    src = input_basic(tmp_path)
    out = tmp_path / "out.csv"
    transform.run_export(src, None, None, out, None, output_delimiter=",")
    assert hash_file(out) != hash_file(src)  # no longer byte-identical, by design
    assert table_io.get_input_header(out, delimiter=",") == [
        "sample_id",
        "read_count",
        "status",
        "notes",
    ]
    rows = list(table_io.iter_rows(out, delimiter=","))
    assert len(rows) == 5
    assert rows[0] == ["SAMPLE_001", "5000", "PASS", "ok"]


def test_delimiter_override_composes_with_config_and_samples(tmp_path):
    out = tmp_path / "out.csv"
    transform.run_export(
        input_basic(tmp_path),
        config_qc_range(tmp_path),
        samples_subset(tmp_path),
        out,
        None,
        output_delimiter=",",
    )
    rows = list(table_io.iter_rows(out, delimiter=","))
    assert [row[0] for row in rows] == ["SAMPLE_001"]


def test_undetectable_delimiter_raises(tmp_path):
    out = tmp_path / "out.tsv"
    with pytest.raises(InputTableError):
        transform.run_export(input_single_column(tmp_path), None, None, out, None)
    assert not out.exists()


def test_ragged_short_row_no_op_path_never_inspects_rows(tmp_path):
    # No --config/--samples: row structure is never inspected, so a short
    # row elsewhere in the file doesn't raise -- it's simply never read.
    src = input_ragged_short(tmp_path)
    out = tmp_path / "out.tsv"
    transform.run_export(src, None, None, out, None)
    assert not out.exists()


def test_ragged_short_row_becomes_missing_value_not_a_crash(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "columns:\n"
        "  - input_column: sample_id\n"
        "  - input_column: notes\n"
        "    qc:\n"
        '      - {operator: "=", value: ok}\n'
    )
    out = tmp_path / "out.tsv"
    transform.run_export(input_ragged_short(tmp_path), config, None, out, None)
    rows = list(table_io.iter_rows(out))
    passing_samples = {row[0] for row in rows}
    # SAMPLE_004's row is missing its trailing "notes" field entirely; that
    # must surface as an ordinary "missing value" QC failure, not a crash.
    assert "SAMPLE_004" not in passing_samples


def test_ragged_long_row_raises_before_output_created(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("columns:\n  - input_column: sample_id\n")
    out = tmp_path / "out.tsv"
    with pytest.raises(InputTableError):
        transform.run_export(input_ragged_long(tmp_path), config, None, out, None)
    assert not out.exists()


def test_no_config_no_samples_logs_nothing_to_do_not_passed_qc(tmp_path, caplog):
    # This no-op path never runs QC and never writes an output file --
    # the summary line shouldn't claim QC happened.
    out = tmp_path / "out.tsv"
    with caplog.at_level("INFO"):
        transform.run_export(input_basic(tmp_path), None, None, out, None)
    messages = [r.message for r in caplog.records]
    assert any("nothing to do" in m for m in messages)
    assert not any("passed QC" in m for m in messages)
    assert not out.exists()


def test_samples_only_no_config_logs_no_qc_not_passed_qc(tmp_path, caplog):
    # Sample-list filtering with no --config also never runs QC.
    out = tmp_path / "out.tsv"
    with caplog.at_level("INFO"):
        transform.run_export(
            input_basic(tmp_path), None, samples_subset(tmp_path), out, None
        )
    messages = [r.message for r in caplog.records]
    # samples_subset requests 2 of the 5 samples in input_basic
    assert any("2/5" in m and "no QC configured" in m for m in messages)
    assert not any("passed QC" in m for m in messages)


def test_config_given_still_logs_passed_qc(tmp_path, caplog):
    out = tmp_path / "out.tsv"
    with caplog.at_level("INFO"):
        transform.run_export(
            input_basic(tmp_path), config_qc_range(tmp_path), None, out, None
        )
    messages = [r.message for r in caplog.records]
    assert any("passed QC" in m for m in messages)
    assert not any("no QC configured" in m for m in messages)


def test_no_config_writes_no_qc_report_even_if_path_given(tmp_path):
    # Fast path (no --config, no --samples): there's no QC to report on,
    # so --qc-report shouldn't produce a file at all, not even empty.
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc_report.tsv"
    transform.run_export(input_basic(tmp_path), None, None, out, qc_report)
    assert not qc_report.exists()


def test_samples_only_no_config_writes_no_qc_report_even_if_path_given(tmp_path):
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc_report.tsv"
    transform.run_export(
        input_basic(tmp_path), None, samples_subset(tmp_path), out, qc_report
    )
    assert not qc_report.exists()


def test_config_given_still_writes_qc_report(tmp_path):
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc_report.tsv"
    transform.run_export(
        input_basic(tmp_path), config_qc_range(tmp_path), None, out, qc_report
    )
    assert qc_report.exists()


def test_config_with_no_qc_rules_still_writes_header_only_report(tmp_path):
    # config_basic renames columns but has no `qc:` rules anywhere --
    # unlike the no-config case, a config was still given, so the report
    # file should exist (header-only), not be skipped.
    out = tmp_path / "out.tsv"
    qc_report = tmp_path / "qc_report.tsv"
    transform.run_export(
        input_basic(tmp_path), config_basic(tmp_path), None, out, qc_report
    )
    assert qc_report.exists()
    assert list(table_io.iter_rows(qc_report)) == []
