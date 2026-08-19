"""Config-shape validation for file_parsing -- end-to-end behavior through
transform.run_export lives in test_transform_file_parsing.py."""

import pytest
from pydantic import ValidationError

from limsport.config import ExportConfig


def test_file_parsing_accepts_single_output_with_command_and_optional_timeout():
    config = ExportConfig.model_validate(
        {
            "columns": [
                {
                    "name": "reference_file",
                    "file_parsing": [
                        {
                            "name": "reference_file",
                            "command": "bcftools view",
                            "timeout_seconds": 30,
                        }
                    ],
                }
            ]
        }
    )
    assert config.columns is not None
    file_parsing = config.columns[0].file_parsing
    assert file_parsing is not None
    assert len(file_parsing) == 1
    assert file_parsing[0].command == "bcftools view"
    assert file_parsing[0].timeout_seconds == 30


def test_file_parsing_accepts_multiple_outputs_each_with_their_own_command():
    config = ExportConfig.model_validate(
        {
            "columns": [
                {
                    "name": "coverage_tsv",
                    "file_parsing": [
                        {"name": "mean_depth", "command": "awk '{print $7}'"},
                        {
                            "name": "coverage_pct",
                            "command": "awk '{print $6}'",
                            "timeout_seconds": 10,
                        },
                    ],
                }
            ]
        }
    )
    assert config.columns is not None
    file_parsing = config.columns[0].file_parsing
    assert file_parsing is not None
    assert [o.name for o in file_parsing] == ["mean_depth", "coverage_pct"]
    assert file_parsing[1].timeout_seconds == 10
    assert config.columns[0].generated_output_names == ["mean_depth", "coverage_pct"]


def test_file_parsing_rejects_empty_list():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate({"columns": [{"name": "a", "file_parsing": []}]})


def test_file_parsing_rejects_duplicate_generated_output_names_within_a_column():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {
                "columns": [
                    {
                        "name": "a",
                        "file_parsing": [
                            {"name": "dup", "command": "cat"},
                            {"name": "dup", "command": "echo hi"},
                        ],
                    }
                ]
            }
        )


def test_file_parsing_rejects_output_name_collision_across_columns():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {
                "columns": [
                    {
                        "name": "a",
                        "file_parsing": [{"name": "shared", "command": "cat"}],
                    },
                    {
                        "name": "b",
                        "file_parsing": [{"name": "shared", "command": "echo hi"}],
                    },
                ]
            }
        )


def test_file_parsing_rejects_rename_on_a_file_parsing_column():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {
                "columns": [
                    {
                        "name": "a",
                        "rename": "b",
                        "file_parsing": [{"name": "out", "command": "cat"}],
                    }
                ]
            }
        )


def test_file_parsing_rejects_column_level_qc_on_a_file_parsing_column():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {
                "columns": [
                    {
                        "name": "a",
                        "qc": [{"operator": ">=", "value": 1}],
                        "file_parsing": [{"name": "out", "command": "cat"}],
                    }
                ]
            }
        )


def _single_output_config(**output_kwargs):
    """A minimal ExportConfig payload with one file_parsing output, whose
    own fields (command, timeout_seconds, ...) are overridden by
    output_kwargs -- shared by the single-output validation tests below,
    which otherwise differ only in that one output's fields."""
    return {
        "columns": [{"name": "a", "file_parsing": [{"name": "out", **output_kwargs}]}]
    }


def test_file_parsing_requires_command():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(_single_output_config())


def test_file_parsing_rejects_empty_command():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(_single_output_config(command=""))


def test_file_parsing_rejects_whitespace_only_command():
    # min_length=1 alone lets "   " through -- bash treats it as a silent
    # no-op, not a real command, so it must be rejected too.
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(_single_output_config(command="   "))


@pytest.mark.parametrize("bad_timeout", [0, -1, -0.5])
def test_file_parsing_rejects_non_positive_timeout(bad_timeout):
    # 0 or negative means "instant timeout" to subprocess, not
    # "unlimited" -- should be a clear config error, not a confusing
    # runtime failure.
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            _single_output_config(command="cat", timeout_seconds=bad_timeout)
        )


def test_file_parsing_accepts_no_timeout():
    config = ExportConfig.model_validate(_single_output_config(command="cat"))
    assert config.columns is not None
    file_parsing = config.columns[0].file_parsing
    assert file_parsing is not None
    assert file_parsing[0].timeout_seconds is None


def test_file_parsing_rejects_unknown_subkeys():
    # Unlike the original placeholder (extra="allow"), the real schema
    # catches typos in file_parsing's own keys.
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(_single_output_config(command="cat", typo_key=1))
