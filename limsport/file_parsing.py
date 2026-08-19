"""Runs file_parsing: downloads a gs:// file if needed, runs each configured command
against it via bash, and returns each single-line result.

Requires the `--allow-file-parsing` input to force people to acknowledge their choices
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


def _safe_basename(original_path: str) -> str:
    """Returns Path(original_path).name, but never ".", "..", or an empty string

    original_path comes from TSV data. A crafted value like "gs://bucket/.." returns  "..",
    which causes issues. If there's anything unsafe, rename it to "downloaded"
    """
    name = Path(original_path).name
    if not name or name in (".", ".."):
        return "downloaded"
    return name


def _cleanup(temp_dir: Path) -> None:
    """Remove temporary download directory"""
    try:
        shutil.rmtree(temp_dir)
    except OSError as e:
        # cleanup failures shouldn't hard fail but shouldn't be silent either
        logger.warning(
            "failed to remove temporary download directory %s: %s", temp_dir, e
        )


def _localize(original_path: str) -> tuple[str, Path | None]:
    """gs:// paths get downloaded to a fresh temp dir; returns (local_path, temp_dir to
    clean up). Anything else comes back as (original_path, None) since it's likely local
    """
    if not original_path.startswith(_GCS_PREFIX):
        return original_path, None

    # check for gcloud on path
    if shutil.which("gcloud") is None:
        raise FileParsingError(
            f"{original_path}: 'gcloud' is required to localize this path but was not found on PATH"
        )

    # make a temporary dir
    temp_dir = Path(tempfile.mkdtemp(prefix="limsport-"))
    local_path = temp_dir / _safe_basename(original_path)
    result = subprocess.run(
        [*_GCS_DOWNLOAD_CMD, original_path, str(local_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        _cleanup(temp_dir)
        raise FileParsingError(
            f"{original_path}: failed to localize with {' '.join(_GCS_DOWNLOAD_CMD)} "
            f"(exit {result.returncode}): {result.stderr.strip()}"
        )
    return str(local_path), temp_dir


def _run_command(
    output: FileParsingOutput, env: dict[str, str], original_path: str
) -> str:
    """Run one output's command with the given environment (which has the file set as
    $FILE), and return its single-line result.

    Raises FileParsingError for a failing command, a timeout, or a result that contains
    a newline. `original_path` is only used to identify the failing row in error messages.
    """
    try:
        result = subprocess.run(
            ["bash", "-c", output.command],
            env=env,
            capture_output=True,
            text=True,
            timeout=output.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise FileParsingError(
            f"file_parsing command timed out for {original_path!r}"
        ) from e

    if result.returncode != 0:
        raise FileParsingError(
            f"file_parsing command failed (exit {result.returncode}) for {original_path!r}: {result.stderr.strip()}"
        )

    # strip trailing newlines, fail on embedded ones
    output_value = result.stdout.rstrip("\n")
    if "\n" in output_value or "\r" in output_value:
        raise FileParsingError(
            f"file_parsing command for {original_path!r} produced a value containing a newline, which cannot be written to a TSV cell: {output_value!r}"
        )
    return output_value


def run(output_columns: list[FileParsingOutput], original_path: str) -> list[str]:
    """Download original_path first if it's a cloud path, run each output's command
    against it in order (sharing that one localized copy and environment), clean up
    afterward, and return the results in the same order as outputs.

    Raises FileParsingError for a failing command, a missing cloud CLI tool, a timeout,
    or a result that contains a newline. A failure on any output aborts the process.
    """
    local_path, temp_dir = _localize(original_path)
    try:
        env = {**os.environ, "FILE": local_path}
        return [_run_command(output, env, original_path) for output in output_columns]
    finally:
        if temp_dir is not None:
            _cleanup(temp_dir)
