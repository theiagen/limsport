class LIMSportError(Exception):
    """Base class for all errors that should be reported to the user as a clean
    message, not crash with a raw traceback. cli.py catches this class."""


class ConfigError(LIMSportError):
    """The YAML config file is missing, malformed, or fails validation."""


class InputTableError(LIMSportError):
    """The input TSV is inconsistent with the config or sample list (e.g. a config
    column that doesn't exist, or exists more than once, in the header)."""


class FileParsingError(LIMSportError):
    """A file_parsing command failed, produced a disallowed newline, or a cloud file
    couldn't be localized."""
