import pytest

from limsport.config import ExportConfig, load_config
from limsport.exceptions import ConfigError
from factories import config_basic, config_qc_range, config_unknown_column


def test_load_valid_config(tmp_path):
    config = load_config(config_qc_range(tmp_path))
    assert [c.name for c in config.columns] == ["sample_id", "read_count", "status"]
    read_count = config.columns[1]
    assert read_count.qc[0].operator.value == ">="
    assert read_count.qc[0].value == 1000
    assert read_count.output_name == "read_count"


def test_load_config_with_rename(tmp_path):
    config = load_config(config_basic(tmp_path))
    renamed = {c.name: c.output_name for c in config.columns}
    assert renamed["read_count"] == "total_reads"
    assert renamed["notes"] == "notes"


def test_rejects_empty_columns(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("columns: []\n")
    with pytest.raises(ConfigError):
        load_config(config_path)


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


def test_rejects_unknown_top_level_key():
    with pytest.raises(Exception):
        ExportConfig.model_validate({"columns": [{"name": "a"}], "not_a_real_key": True})


def test_malformed_yaml_raises_config_error(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("columns: [this is not: valid: yaml")
    with pytest.raises(ConfigError):
        load_config(bad)


def test_unknown_column_config_raises_on_load(tmp_path):
    # Loading only validates the config's own shape; missing-in-input-header
    # checking happens in transform.py, not here.
    config = load_config(config_unknown_column(tmp_path))
    assert [c.name for c in config.columns] == ["sample_id", "does_not_exist"]
