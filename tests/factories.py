"""Shared test-data builders used across multiple test modules. Each
builder takes a tmp_path and writes one small input/config/samples file
into it, returning the Path -- plain functions rather than fixtures, so a
test can build exactly the files it needs, in whatever combination,
without pytest's fixture-injection machinery in the way.

A builder used by only one test module lives inline in that module
instead of here.
"""

import hashlib


def hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_basic(tmp_path):
    path = tmp_path / "input_basic.tsv"
    path.write_text(
        "sample_id\tread_count\tstatus\tnotes\n"
        "SAMPLE_001\t5000\tPASS\tok\n"
        "SAMPLE_002\t500\tPASS\tlow reads\n"
        "SAMPLE_003\t2000000\tPASS\ttoo many reads\n"
        "SAMPLE_004\t8000\tFAIL\tbad status\n"
        "SAMPLE_005\tNA\tPASS\t\n"
    )
    return path


def input_single_column(tmp_path):
    path = tmp_path / "input_single_column.tsv"
    path.write_text("sample_id\nSAMPLE_001\nSAMPLE_002\n")
    return path


def input_with_dupes(tmp_path):
    path = tmp_path / "input_with_dupes.tsv"
    path.write_text(
        "sample_id\tread_count\tread_count\tstatus\n"
        "SAMPLE_001\t5000\t5000\tPASS\n"
        "SAMPLE_002\t500\t500\tPASS\n"
    )
    return path


def input_comma(tmp_path):
    path = tmp_path / "input_comma.csv"
    path.write_text(
        "sample_id,read_count,status\n"
        "SAMPLE_001,5000,PASS\n"
        "SAMPLE_002,500,PASS\n"
    )
    return path


def input_ragged_short(tmp_path):
    # SAMPLE_004's row is missing its trailing "notes" field.
    path = tmp_path / "input_ragged_short.tsv"
    path.write_text(
        "sample_id\tread_count\tstatus\tnotes\n"
        "SAMPLE_001\t5000\tPASS\tok\n"
        "SAMPLE_002\t500\tPASS\tlow reads\n"
        "SAMPLE_003\t2000000\tPASS\ttoo many reads\n"
        "SAMPLE_004\t8000\tPASS\n"
    )
    return path


def input_ragged_long(tmp_path):
    # SAMPLE_004's row has one extra field beyond the header's width.
    path = tmp_path / "input_ragged_long.tsv"
    path.write_text(
        "sample_id\tread_count\tstatus\tnotes\n"
        "SAMPLE_001\t5000\tPASS\tok\n"
        "SAMPLE_002\t500\tPASS\tlow reads\n"
        "SAMPLE_003\t2000000\tPASS\ttoo many reads\n"
        "SAMPLE_004\t8000\tPASS\tbad\textra\n"
    )
    return path


def config_basic(tmp_path):
    path = tmp_path / "config_basic.yaml"
    path.write_text(
        "columns:\n"
        "  - name: sample_id\n"
        "  - name: read_count\n"
        "    rename: total_reads\n"
        "  - name: status\n"
        "    rename: Status\n"
        "  - name: notes\n"
    )
    return path


def config_unknown_column(tmp_path):
    path = tmp_path / "config_unknown_column.yaml"
    path.write_text("columns:\n  - name: sample_id\n  - name: does_not_exist\n")
    return path


def config_qc_range(tmp_path):
    path = tmp_path / "config_qc_range.yaml"
    path.write_text(
        "columns:\n"
        "  - name: sample_id\n"
        "  - name: read_count\n"
        "    qc:\n"
        '      - {operator: ">=", value: 1000}\n'
        '      - {operator: "<=", value: 1000000}\n'
        "  - name: status\n"
        "    qc:\n"
        '      - {operator: "=", value: PASS}\n'
    )
    return path


def config_qc_approx(tmp_path):
    path = tmp_path / "config_qc_approx.yaml"
    path.write_text(
        "columns:\n"
        "  - name: sample_id\n"
        "  - name: read_count\n"
        "    qc:\n"
        '      - {operator: "~=", value: 5000, tolerance_percent: 10}\n'
    )
    return path


def config_dupe_reference(tmp_path):
    path = tmp_path / "config_dupe_reference.yaml"
    path.write_text("columns:\n  - name: sample_id\n  - name: read_count\n")
    return path


def samples_subset(tmp_path):
    path = tmp_path / "samples_subset.txt"
    path.write_text("SAMPLE_001\nSAMPLE_003\n")
    return path


def samples_with_unknown(tmp_path):
    path = tmp_path / "samples_with_unknown.txt"
    path.write_text("SAMPLE_001\nSAMPLE_999\n")
    return path
