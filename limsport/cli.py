"""
argparse entry point for the `limsport` command

External methods:
    - existing_file()
    - build_parser()
    - main()
"""

import argparse
import logging
import sys
from pathlib import Path

from . import transform
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
        help="output delimiter (default: '\t')",
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
        transform.run_export(
            args.input,
            args.config,
            args.samples,
            args.output,
            args.qc_report,
            args.delimiter,
            args.allow_file_parsing,
        )
    except (LIMSportError, OSError) as e:
        # LIMSportError covers config/input-table domain errors (see
        # exceptions.py) while OSError covers write-side failures
        print(str(e), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
