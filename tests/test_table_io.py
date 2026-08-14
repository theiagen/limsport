import pytest

from limsport import table_io
from limsport.exceptions import InputTableError
from factories import (
    hash_file,
    input_basic,
    input_comma,
    input_ragged_long,
    input_ragged_short,
    input_single_column,
    input_with_dupes,
)


def test_detect_delimiter_tab(tmp_path):
    assert table_io.detect_delimiter(input_basic(tmp_path)) == "\t"


def test_detect_delimiter_comma(tmp_path):
    assert table_io.detect_delimiter(input_comma(tmp_path)) == ","


def test_detect_delimiter_raises_on_single_column(tmp_path):
    with pytest.raises(InputTableError):
        table_io.detect_delimiter(input_single_column(tmp_path))


def test_detect_delimiter_raises_on_empty_file(tmp_path):
    empty = tmp_path / "empty.tsv"
    empty.write_text("")
    with pytest.raises(InputTableError):
        table_io.detect_delimiter(empty)


def test_detect_delimiter_ignores_a_ragged_row_elsewhere_in_the_file(tmp_path):
    # Sniffing from the header line only means a malformed data row further
    # down the file can't break detection for an otherwise normal file.
    assert table_io.detect_delimiter(input_ragged_short(tmp_path)) == "\t"
    assert table_io.detect_delimiter(input_ragged_long(tmp_path)) == "\t"


def test_iter_rows_pads_a_short_row_with_empty_strings(tmp_path):
    rows = list(table_io.iter_rows(input_ragged_short(tmp_path)))
    # SAMPLE_004's row is missing its trailing "notes" field in the fixture.
    short_row = next(row for row in rows if row[0] == "SAMPLE_004")
    assert short_row == ["SAMPLE_004", "8000", "PASS", ""]


def test_iter_rows_raises_on_a_row_longer_than_the_header(tmp_path):
    with pytest.raises(InputTableError):
        list(table_io.iter_rows(input_ragged_long(tmp_path)))


def test_count_rows_does_not_validate_row_width(tmp_path):
    # count_rows is used by the byte-identical fast path, which never
    # inspects row structure -- it must not raise on a ragged file.
    assert table_io.count_rows(input_ragged_short(tmp_path)) == 4
    assert table_io.count_rows(input_ragged_long(tmp_path)) == 4


def test_read_header_auto_detects_comma_delimiter(tmp_path):
    path = input_comma(tmp_path)
    assert table_io.read_header(path) == ["sample_id", "read_count", "status"]
    assert list(table_io.iter_rows(path))[0] == ["SAMPLE_001", "5000", "PASS"]


def test_read_header_and_iter_rows_accept_explicit_delimiter(tmp_path):
    path = input_comma(tmp_path)
    assert table_io.read_header(path, delimiter=",") == ["sample_id", "read_count", "status"]
    rows = list(table_io.iter_rows(path, delimiter=","))
    assert rows[0] == ["SAMPLE_001", "5000", "PASS"]


def test_read_header(tmp_path):
    assert table_io.read_header(input_basic(tmp_path)) == [
        "sample_id",
        "read_count",
        "status",
        "notes",
    ]


def test_iter_rows_skips_header(tmp_path):
    rows = list(table_io.iter_rows(input_basic(tmp_path)))
    assert len(rows) == 5
    assert rows[0][0] == "SAMPLE_001"


def test_write_read_round_trip_with_quotes(tmp_path):
    path = tmp_path / "out.tsv"
    header = ["a", "b"]
    rows = [['has "quotes"', "plain"], ["another", "row"]]
    table_io.write_tsv(path, header, rows)
    assert table_io.read_header(path) == header
    assert list(table_io.iter_rows(path)) == rows


def test_copy_file_verbatim_is_byte_identical(tmp_path):
    src = input_basic(tmp_path)
    dst = tmp_path / "copy.tsv"
    table_io.copy_file_verbatim(src, dst)
    assert hash_file(dst) == hash_file(src)


def test_copy_file_verbatim_preserves_duplicate_headers(tmp_path):
    src = input_with_dupes(tmp_path)
    dst = tmp_path / "copy.tsv"
    table_io.copy_file_verbatim(src, dst)
    assert hash_file(dst) == hash_file(src)
