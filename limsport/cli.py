"""
argparse entry point for the `limsport` command

External methods:
    - existing_file()
    - non_negative_int()
    - build_parser()
    - main()
"""

import argparse
import logging
import sys
from pathlib import Path

from . import pipeline
from .exceptions import LIMSportError


def existing_file(value: str) -> Path:
    """
    Confirms that an input file must already exist

    Args:
        value: the path string given on the command line.

    Returns:
        The value as a Path.

    Raises:
        argparse.ArgumentTypeError: if the path does not exist or is not a file.
    """
    path = Path(value)
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"file not found: {value}")
    return path


def non_negative_int(value: str) -> int:
    """
    Confirms a count option is zero or more.

    Args:
        value: the number given on the command line.

    Returns:
        The value as an int.

    Raises:
        argparse.ArgumentTypeError: if it isn't a whole number of zero or more.
    """
    try:
        number = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"not a whole number: {value}") from e
    if number < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or greater: {value}")
    return number


def build_parser() -> argparse.ArgumentParser:
    """
    Builds the argparse parser for the LIMSport CLI.

    Returns:
        The parser with every LIMSport option registered.
    """
    parser = argparse.ArgumentParser(
        prog="limsport",
        description="Transform a TSV table's column names and perform QC on rows to generate a LIMS-importable file.",
    )
    parser.add_argument(
        "--input", "-i", required=True, type=existing_file, help="input TSV table"
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("limsport.tsv"),
        help="output TSV path (default: limsport.tsv).",
    )

    # optional inputs
    parser.add_argument(
        "--config",
        "-c",
        type=existing_file,
        help="optional YAML config for column mapping and QC",
    )
    parser.add_argument(
        "--samples",
        "-s",
        type=existing_file,
        help="optional file listing sample names to include",
    )
    parser.add_argument(
        "--qc-report",
        "-r",
        type=Path,
        default=Path("qc_report.tsv"),
        help="QC failure report TSV path (default: qc_report.tsv)",
    )
    parser.add_argument(
        "--delimiter",
        "-d",
        type=str,
        default="\t",
        help="output delimiter (default: '\\t')",
    )
    parser.add_argument(
        "--max-file-parsing-failures",
        type=non_negative_int,
        default=None,
        help="abort once more than N rows fail file_parsing (default: no limit; a "
        "file that won't parse fails that row's QC and the run continues). Use 0 to "
        "abort on the first failure.",
    )
    parser.add_argument(
        "--allow-file-parsing",
        action="store_true",
        help="allow executing file_parsing commands from --config (required if the config uses them)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """
    Sets up logging, parses the arguments, and runs the export.

    Args:
        argv: the argument list to parse, or None to read them from sys.argv.

    Returns:
        The process exit code -- 0 on success, 1 if the export raised a
        LIMSportError or an OSError.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )
    args = build_parser().parse_args(argv)

    try:
        pipeline.run_export(
            args.input,
            args.config,
            args.samples,
            args.output,
            args.qc_report,
            args.delimiter,
            args.allow_file_parsing,
            args.max_file_parsing_failures,
        )
    except (LIMSportError, OSError) as e:
        # LIMSportError covers config/input-table domain errors (see
        # exceptions.py) while OSError covers write-side failures
        print(str(e), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
