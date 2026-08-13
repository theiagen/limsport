"""Runs a column's file_parsing instruction: downloads a gs:// file if
needed, runs the configured command against it via bash, and returns its
single-line result.

Gated behind --allow-file-parsing (see cli.py), since this runs commands
from the config file against a path that came from the TSV data.
"""

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import FileParsingInstruction
from .exceptions import FileParsingError

logger = logging.getLogger("limsport")

_GCS_PREFIX = "gs://"
_GCS_DOWNLOAD_CMD = ["gcloud", "storage", "cp"]


def _safe_basename(raw_path: str) -> str:
    """Path(raw_path).name, but never ".", "..", or empty.

    raw_path comes from TSV data, which is less trusted than the config.
    A crafted value like "gs://bucket/.." makes Path(...).name return
    "..", and since local_path is tmp_dir / this name, that would resolve
    to tmp_dir's parent -- escaping the temp sandbox entirely. Falling
    back to a fixed name for anything unsafe keeps the join inside
    tmp_dir no matter what raw_path is.
    """
    name = Path(raw_path).name
    if not name or name in (".", ".."):
        return "downloaded"
    return name


def _cleanup(tmp_dir: Path) -> None:
    try:
        shutil.rmtree(tmp_dir)
    except OSError as e:
        # A cleanup failure shouldn't hard fail, but it shouldn't be silent either
        logger.warning("failed to remove temporary download directory %s: %s", tmp_dir, e)


def _localize(raw_path: str) -> tuple[str, Path | None]:
    """gs:// paths get downloaded to a fresh temp dir; returns (local_path,
    temp_dir to clean up). Anything else comes back unchanged as
    (raw_path, None), on the assumption it's already local."""
    if not raw_path.startswith(_GCS_PREFIX):
        return raw_path, None

    if shutil.which("gcloud") is None:
        raise FileParsingError(f"{raw_path}: 'gcloud' is required to localize this path but was not found on PATH")

    tmp_dir = Path(tempfile.mkdtemp(prefix="limsport-"))
    local_path = tmp_dir / _safe_basename(raw_path)
    result = subprocess.run(
        [*_GCS_DOWNLOAD_CMD, raw_path, str(local_path)], capture_output=True, text=True
    )
    if result.returncode != 0:
        _cleanup(tmp_dir)
        raise FileParsingError(
            f"{raw_path}: failed to localize with {' '.join(_GCS_DOWNLOAD_CMD)} "
            f"(exit {result.returncode}): {result.stderr.strip()}"
        )
    return str(local_path), tmp_dir


def run(instruction: FileParsingInstruction, raw_value: str) -> str:
    """Download raw_value first if it's a cloud path, run
    instruction.command against it with $LIMSPORT_FILE set, clean up
    afterward, and return the single-line result.

    Raises FileParsingError for a failing command, a missing cloud CLI
    tool, a timeout, or a result that contains a newline.
    """
    local_path, tmp_dir = _localize(raw_value)
    try:
        try:
            result = subprocess.run(
                ["bash", "-c", instruction.command],
                env={**os.environ, "LIMSPORT_FILE": local_path},
                capture_output=True,
                text=True,
                timeout=instruction.timeout_seconds,
            )
        except subprocess.TimeoutExpired as e:
            raise FileParsingError(f"file_parsing command timed out for {raw_value!r}") from e

        if result.returncode != 0:
            raise FileParsingError(
                f"file_parsing command failed (exit {result.returncode}) for {raw_value!r}: "
                f"{result.stderr.strip()}"
            )

        # strip trailing newlines, fail on embedded ones
        output = result.stdout.rstrip("\n")
        if "\n" in output or "\r" in output:
            raise FileParsingError(
                f"file_parsing command for {raw_value!r} produced a value containing "
                f"a newline, which cannot be written to a TSV cell: {output!r}"
            )
        return output
    finally:
        if tmp_dir is not None:
            _cleanup(tmp_dir)
