"""argparse entry point for the `limsport` command"""

import argparse
import logging
import sys
from pathlib import Path

from . import transform
from .exceptions import LIMSportError


def existing_file(value: str) -> Path:
    """confirm that an input file must already exist"""
    path = Path(value)
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"file not found: {value}")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="limsport",
        description="Transform a TSV table's column names and perform QC on rows to generate a LIMS-importable file.",
    )
    parser.add_argument("--input", "-i",
                        required=True, type=existing_file, help="input TSV table")
    parser.add_argument("--output", "-o",
                        type=Path, default=Path("limsport.tsv"), help="output TSV path (default: limsport.tsv).")

    # optional inputs
    parser.add_argument("--config", "-c",
                        type=existing_file, help="optional YAML config for column mapping and QC")
    parser.add_argument("--samples", "-s",
                        type=existing_file, help="optional file listing sample names to include")
    parser.add_argument("--qc-report", "-r",
                        type=Path, help="optional path to write a QC failure report TSV")
    parser.add_argument("--delimiter", "-d",
                        type=str, default="\t", help="output delimiter (default: '\t')")
    parser.add_argument("--allow-file-parsing",
                        action="store_true", help="allow executing file_parsing commands from --config (required if the config uses them)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stderr, force=True
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
