"""
argparse entry point for the `limsport` command

External methods:
    - existing_file()
    - non_negative_int()
    - output_path()
    - build_parser()
    - main()
"""

import argparse
import logging
import sys
from importlib.metadata import version
from pathlib import Path

from . import pipeline
from .exceptions import LIMSportError

logger = logging.getLogger("limsport")


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


def output_path(prefix: str, delimiter: str, suffix: str = "") -> Path:
    """
    Names an output file from its prefix and the output delimiter.

    Args:
        prefix: the --output-prefix value, which may include directories.
        delimiter: the output delimiter.
        suffix: appended to the prefix before the extension.

    Returns:
        The prefix and suffix with .tsv for a tab, .csv for a comma, or .txt
        otherwise.
    """
    extension = {"\t": ".tsv", ",": ".csv"}.get(delimiter, ".txt")
    return Path(f"{prefix}{suffix}{extension}")


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
        "--output-prefix",
        "-o",
        default="limsport",
        help="prefix for the output (<prefix>_lims_report), QC failure report "
        "(<prefix>_qc_fail_report), and QC summary (<prefix>_qc_summary); the "
        "extension, .tsv, .csv, or .txt, matches --output-delimiter (default: limsport).",
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
        "--output-delimiter",
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
        "--include-qc-fails",
        action="store_true",
        help="also write samples that fail QC to the output, including every sample "
        "of a run that fails set_qc; the QC summary still marks them FAIL",
    )
    parser.add_argument(
        "--allow-file-parsing",
        action="store_true",
        help="allow executing file_parsing commands from --config (required if the config uses them)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="log each step of the run to stderr",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {version('limsport')}",
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
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )
    logger.debug("arguments: %s", vars(args))

    try:
        pipeline.run_export(
            args.input,
            args.config,
            args.samples,
            output_path(args.output_prefix, args.output_delimiter, "_lims_report"),
            output_path(args.output_prefix, args.output_delimiter, "_qc_fail_report"),
            args.output_delimiter,
            args.allow_file_parsing,
            args.max_file_parsing_failures,
            output_path(args.output_prefix, args.output_delimiter, "_qc_summary"),
            args.include_qc_fails,
        )
    except (LIMSportError, OSError) as e:
        # LIMSportError covers config/input-table domain errors (see
        # exceptions.py) while OSError covers write-side failures
        print(str(e), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
