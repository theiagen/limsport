"""Runs a column's file_parsing outputs: downloads a gs:// file if
needed, runs each configured command against it via bash, and returns
each single-line result.

Gated behind --allow-file-parsing (see cli.py), since this runs commands
from the config file against a path that came from the TSV data.
"""

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import FileParsingOutput
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


def _run_command(output: FileParsingOutput, env: dict[str, str], raw_value: str) -> str:
    """Run one output's command with the given environment (which
    already has $LIMSPORT_FILE set), and return its single-line result.

    Raises FileParsingError for a failing command, a timeout, or a
    result that contains a newline. raw_value is only used to identify
    the failing row in error messages.
    """
    try:
        result = subprocess.run(
            ["bash", "-c", output.command],
            env=env,
            capture_output=True,
            text=True,
            timeout=output.timeout_seconds,
        )
    except subprocess.TimeoutExpired as e:
        raise FileParsingError(f"file_parsing command timed out for {raw_value!r}") from e

    if result.returncode != 0:
        raise FileParsingError(
            f"file_parsing command failed (exit {result.returncode}) for {raw_value!r}: "
            f"{result.stderr.strip()}"
        )

    # strip trailing newlines, fail on embedded ones
    output_value = result.stdout.rstrip("\n")
    if "\n" in output_value or "\r" in output_value:
        raise FileParsingError(
            f"file_parsing command for {raw_value!r} produced a value containing "
            f"a newline, which cannot be written to a TSV cell: {output_value!r}"
        )
    return output_value


def run(outputs: list[FileParsingOutput], raw_value: str) -> list[str]:
    """Download raw_value first if it's a cloud path, run each output's
    command against it in order (sharing that one localized copy and
    environment), clean up afterward, and return the results in the same
    order as outputs.

    Raises FileParsingError for a failing command, a missing cloud CLI
    tool, a timeout, or a result that contains a newline. A failure on
    any one output aborts the rest.
    """
    local_path, tmp_dir = _localize(raw_value)
    try:
        env = {**os.environ, "LIMSPORT_FILE": local_path}
        return [_run_command(o, env, raw_value) for o in outputs]
    finally:
        if tmp_dir is not None:
            _cleanup(tmp_dir)
