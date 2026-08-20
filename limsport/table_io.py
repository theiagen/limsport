"""
TSV reading/writing, including delimiter auto-detection

External methods:
    - detect_delimiter()
    - get_input_header()
    - iter_rows()
    - write_tsv()
"""

import csv
from collections.abc import Iterable, Iterator
from pathlib import Path

from .exceptions import InputTableError

_CANDIDATE_DELIMITERS = "\t,;|"


def detect_delimiter(path: Path) -> str:
    """
    Detects the delimiter from the header: tab, comma, semicolon, or pipe.

    Args:
        path: the input table to check the first line of.

    Returns:
        The single delimiter character.

    Raises:
        InputTableError: if the file is empty, or none can be identified confidently.
    """
    with path.open(newline="", encoding="utf-8") as f:
        header_line = f.readline()
    if not header_line:
        raise InputTableError(f"{path}: file is empty, cannot detect a delimiter")
    try:
        dialect = csv.Sniffer().sniff(header_line, delimiters=_CANDIDATE_DELIMITERS)
    except csv.Error as e:
        raise InputTableError(
            f"{path}: could not auto-detect a delimiter ({e}); the file may have only one column, or use an unsupported delimiter"
        ) from e
    return dialect.delimiter


def get_input_header(path: Path, delimiter: str | None = None) -> list[str]:
    """
    Returns the file's header row.

    Args:
        path: the input table to read.
        delimiter: the delimiter to split on, or None to auto-detect it.

    Returns:
        The header's column names, in file order.

    Raises:
        InputTableError: if `delimiter` is None and one cannot be detected.
    """
    delimiter = delimiter or detect_delimiter(path)
    with path.open(newline="", encoding="utf-8") as f:
        return next(csv.reader(f, delimiter=delimiter))


def iter_rows(path: Path, delimiter: str | None = None) -> Iterator[list[str]]:
    """
    Yields each data row as a list of raw string cells.

    Args:
        path: the input table to read; its header is skipped.
        delimiter: the delimiter to split on, or None to auto-detect it.

    Yields:
        One data row's raw string cells, padded with empty strings when the row
        has less fields than the header.

    Raises:
        InputTableError: if a row has more fields than the header, or if
          `delimiter` is None and one cannot be detected.
    """
    delimiter = delimiter or detect_delimiter(
        path
    )  # detect_delimiter here only runs in the pytests

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter=delimiter)
        width = len(next(reader))  # skip header
        for row in reader:
            if len(row) < width:
                # pad row if it's not the right length (potentially stripped whitespace?)
                row = row + [""] * (width - len(row))
            elif len(row) > width:
                raise InputTableError(
                    f"{path}: row has {len(row)} columns, expected {width} (based on the header): {row}"
                )
            yield row


def write_tsv(
    path: Path, header: list[str], rows: Iterable[list[str]], delimiter: str = "\t"
) -> None:
    """
    Writes TSV to output

    Args:
        path: the file to write, overwriting anything already there.
        header: the column names to write as the first row.
        rows: the data rows to write, in order.
        delimiter: the delimiter to join cells with; defaults to a tab.
    """
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(
            f, delimiter=delimiter, lineterminator="\n", quoting=csv.QUOTE_MINIMAL
        )
        writer.writerow(header)
        writer.writerows(rows)
