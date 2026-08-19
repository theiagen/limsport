"""TSV reading/writing, including delimiter auto-detection"""

import csv
from collections.abc import Iterable, Iterator
from pathlib import Path

from .exceptions import InputTableError

_CANDIDATE_DELIMITERS = "\t,;|"


def detect_delimiter(path: Path) -> str:
    """Detect the delimiter from the header: tab, comma, semicolon, or pipe.
    Raises an error if none of those can be identified confidently.
    """
    with path.open(newline="", encoding="utf-8") as f:
        header_line = f.readline()
    if not header_line:
        raise InputTableError(f"{path}: file is empty, cannot detect a delimiter")
    try:
        dialect = csv.Sniffer().sniff(header_line, delimiters=_CANDIDATE_DELIMITERS)
    except csv.Error as e:
        raise InputTableError(
            f"{path}: could not auto-detect a delimiter ({e}); "
            "the file may have only one column, or use an unsupported delimiter"
        ) from e
    return dialect.delimiter


def get_input_header(path: Path, delimiter: str | None = None) -> list[str]:
    """Return the file's header row. delimiter is auto-detected if omitted."""
    delimiter = delimiter or detect_delimiter(path)
    with path.open(newline="", encoding="utf-8") as f:
        return next(csv.reader(f, delimiter=delimiter))


def iter_rows(path: Path, delimiter: str | None = None) -> Iterator[list[str]]:
    """Yield each data row as a list of raw string cells.

    Rows with less fields are padded while rows with more fields fail
    """
    delimiter = delimiter or detect_delimiter(
        path
    )  # detect_delimiter here only runs in the pytests

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter=delimiter)
        width = len(next(reader))  # skip header
        for row in reader:
            if len(row) < width:
                row = row + [""] * (width - len(row))
            elif len(row) > width:
                raise InputTableError(
                    f"{path}: row has {len(row)} columns, expected {width} (based on the header): {row}"
                )
            yield row


def write_tsv(
    path: Path, header: list[str], rows: Iterable[list[str]], delimiter: str = "\t"
) -> None:
    """Write TSV to output"""
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(
            f, delimiter=delimiter, lineterminator="\n", quoting=csv.QUOTE_MINIMAL
        )
        writer.writerow(header)
        writer.writerows(rows)
