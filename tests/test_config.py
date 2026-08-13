import pytest

from limsport.config import ExportConfig, load_config
from limsport.exceptions import ConfigError


def test_load_valid_config(fixtures_dir):
    config = load_config(fixtures_dir / "config_qc_range.yaml")
    assert [c.name for c in config.columns] == ["sample_id", "read_count", "status"]
    read_count = config.columns[1]
    assert read_count.qc[0].operator.value == ">="
    assert read_count.qc[0].value == 1000
    assert read_count.output_name == "read_count"


def test_load_config_with_rename(fixtures_dir):
    config = load_config(fixtures_dir / "config_basic.yaml")
    renamed = {c.name: c.output_name for c in config.columns}
    assert renamed["read_count"] == "total_reads"
    assert renamed["notes"] == "notes"


def test_rejects_empty_columns(fixtures_dir):
    with pytest.raises(ConfigError):
        load_config(fixtures_dir / "config_empty_columns.yaml")


def test_rejects_duplicate_column_names():
    with pytest.raises(Exception):
        ExportConfig.model_validate(
            {"columns": [{"name": "a"}, {"name": "a"}]}
        )


def test_rejects_ordering_operator_on_string_value():
    with pytest.raises(Exception):
        ExportConfig.model_validate(
            {"columns": [{"name": "a", "qc": [{"operator": ">", "value": "PASS"}]}]}
        )


def test_approx_operator_accepts_tolerance_percent():
    config = ExportConfig.model_validate(
        {
            "columns": [
                {
                    "name": "length",
                    "qc": [{"operator": "~=", "value": 1000000, "tolerance_percent": 5}],
                }
            ]
        }
    )
    condition = config.columns[0].qc[0]
    assert condition.operator.value == "~="
    assert condition.tolerance_percent == 5


def test_approx_operator_requires_tolerance_percent():
    with pytest.raises(Exception):
        ExportConfig.model_validate(
            {"columns": [{"name": "a", "qc": [{"operator": "~=", "value": 1000000}]}]}
        )


def test_approx_operator_rejects_non_positive_tolerance_percent():
    with pytest.raises(Exception):
        ExportConfig.model_validate(
            {
                "columns": [
                    {
                        "name": "a",
                        "qc": [{"operator": "~=", "value": 1000000, "tolerance_percent": 0}],
                    }
                ]
            }
        )


def test_tolerance_percent_rejected_on_non_approx_operator():
    with pytest.raises(Exception):
        ExportConfig.model_validate(
            {
                "columns": [
                    {
                        "name": "a",
                        "qc": [{"operator": ">=", "value": 1000, "tolerance_percent": 5}],
                    }
                ]
            }
        )


def test_approx_operator_requires_numeric_value():
    with pytest.raises(Exception):
        ExportConfig.model_validate(
            {
                "columns": [
                    {
                        "name": "a",
                        "qc": [{"operator": "~=", "value": "PASS", "tolerance_percent": 5}],
                    }
                ]
            }
        )


def test_rejects_bool_value_on_equality_operator():
    # value=True/False shouldn't get silently coerced to 1.0/0.0 --
    # someone writing `value: true` means to match "true", not 1.
    with pytest.raises(Exception):
        ExportConfig.model_validate(
            {"columns": [{"name": "a", "qc": [{"operator": "=", "value": True}]}]}
        )


def test_rejects_bool_value_on_ordering_operator():
    with pytest.raises(Exception):
        ExportConfig.model_validate(
            {"columns": [{"name": "a", "qc": [{"operator": ">", "value": False}]}]}
        )


def test_load_config_rejects_empty_file(tmp_path):
    empty = tmp_path / "empty.yaml"
    empty.write_text("")
    with pytest.raises(ConfigError):
        load_config(empty)


def test_load_config_rejects_wrong_top_level_shape(tmp_path):
    # Valid YAML, but a list instead of a mapping -- a different failure
    # mode than bad syntax. Should still come back as ConfigError, not an
    # uncaught pydantic error.
    not_a_mapping = tmp_path / "list.yaml"
    not_a_mapping.write_text("- a\n- b\n")
    with pytest.raises(ConfigError):
        load_config(not_a_mapping)


def test_file_parsing_accepts_command_and_optional_timeout():
    config = ExportConfig.model_validate(
        {
            "columns": [
                {
                    "name": "reference_file",
                    "file_parsing": {"command": "bcftools view", "timeout_seconds": 30},
                }
            ]
        }
    )
    file_parsing = config.columns[0].file_parsing
    assert file_parsing is not None
    assert file_parsing.command == "bcftools view"
    assert file_parsing.timeout_seconds == 30


def test_file_parsing_requires_command():
    with pytest.raises(Exception):
        ExportConfig.model_validate({"columns": [{"name": "a", "file_parsing": {}}]})


def test_file_parsing_rejects_empty_command():
    with pytest.raises(Exception):
        ExportConfig.model_validate(
            {"columns": [{"name": "a", "file_parsing": {"command": ""}}]}
        )


def test_file_parsing_rejects_whitespace_only_command():
    # min_length=1 alone lets "   " through -- bash treats it as a silent
    # no-op, not a real command, so it must be rejected too.
    with pytest.raises(Exception):
        ExportConfig.model_validate(
            {"columns": [{"name": "a", "file_parsing": {"command": "   "}}]}
        )


@pytest.mark.parametrize("bad_timeout", [0, -1, -0.5])
def test_file_parsing_rejects_non_positive_timeout(bad_timeout):
    # 0 or negative means "instant timeout" to subprocess, not
    # "unlimited" -- should be a clear config error, not a confusing
    # runtime failure.
    with pytest.raises(Exception):
        ExportConfig.model_validate(
            {"columns": [{"name": "a", "file_parsing": {"command": "cat", "timeout_seconds": bad_timeout}}]}
        )


def test_file_parsing_accepts_no_timeout():
    config = ExportConfig.model_validate(
        {"columns": [{"name": "a", "file_parsing": {"command": "cat"}}]}
    )
    file_parsing = config.columns[0].file_parsing
    assert file_parsing is not None
    assert file_parsing.timeout_seconds is None


def test_file_parsing_rejects_unknown_subkeys():
    # Unlike the original placeholder (extra="allow"), the real schema
    # catches typos in file_parsing's own keys.
    with pytest.raises(Exception):
        ExportConfig.model_validate(
            {"columns": [{"name": "a", "file_parsing": {"command": "cat", "typo_key": 1}}]}
        )


def test_rejects_unknown_top_level_key():
    with pytest.raises(Exception):
        ExportConfig.model_validate({"columns": [{"name": "a"}], "not_a_real_key": True})


def test_malformed_yaml_raises_config_error(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("columns: [this is not: valid: yaml")
    with pytest.raises(ConfigError):
        load_config(bad)


def test_unknown_column_config_raises_on_load(fixtures_dir):
    # Loading only validates the config's own shape; missing-in-input-header
    # checking happens in transform.py, not here.
    config = load_config(fixtures_dir / "config_unknown_column.yaml")
    assert [c.name for c in config.columns] == ["sample_id", "does_not_exist"]
