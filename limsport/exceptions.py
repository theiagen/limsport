"""
The LIMSport unique classes.

Included classes:
    - LIMSportError
    - ConfigError
    - InputTableError
    - FileParsingError
    - ToolNotFoundError
"""


class LIMSportError(Exception):
    """
    Base class for all errors that should be reported to the user as a clean message,
    not crash with a raw traceback. cli.py catches this class.
    """


class ConfigError(LIMSportError):
    """
    The YAML config file is missing, malformed, or fails validation.
    """


class InputTableError(LIMSportError):
    """
    The input TSV is inconsistent with the config or sample list (e.g. a config column
    that doesn't exist, or exists more than once, in the header).
    """


class FileParsingError(LIMSportError):
    """
    A file_parsing command failed, produced a disallowed newline, or a cloud file
    couldn't be localized.

    This is about one row's data, so ingest.py catches it and fails that row's QC
    rather than the whole run. Anything that would fail every row identically must
    NOT subclass this -- see ToolNotFoundError.
    """


class ToolNotFoundError(LIMSportError):
    """
    A command line tool file_parsing needs (e.g. 'gcloud') isn't installed.

    Deliberately not a FileParsingError: no row can succeed without the tool, so
    this aborts the run instead of producing one identical QC failure per row.
    """
