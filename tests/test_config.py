import pytest
from factories import config_basic, config_qc_range, config_unknown_column
from pydantic import ValidationError

from limsport.config import ExportConfig, QCCondition, load_config
from limsport.exceptions import ConfigError


def _first_qc(config: ExportConfig) -> QCCondition:
    """The first QC condition on the config's first column, narrowed past
    the optional `columns` and the ConditionalQC form of `qc`."""
    assert config.columns is not None
    qc = config.columns[0].qc
    assert isinstance(qc, list)
    return qc[0]


def test_load_valid_config(tmp_path):
    config = load_config(config_qc_range(tmp_path))
    assert config.columns is not None
    assert [c.input_column for c in config.columns] == [
        "sample_id",
        "read_count",
        "status",
    ]
    read_count = config.columns[1]
    assert isinstance(read_count.qc, list)
    assert read_count.qc[0].operator.value == ">="
    assert read_count.qc[0].value == 1000
    assert read_count.output_column_name == "read_count"


def test_load_config_with_output_column(tmp_path):
    config = load_config(config_basic(tmp_path))
    assert config.columns is not None
    output_columns = {c.input_column: c.output_column_name for c in config.columns}
    assert output_columns["read_count"] == "total_reads"
    assert output_columns["notes"] == "notes"


def test_rejects_empty_columns(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("columns: []\n")
    with pytest.raises(ConfigError):
        load_config(config_path)


def test_rejects_explicit_empty_columns_even_with_set_qc_present(tmp_path):
    # unlike omitting `columns` entirely, an explicit `columns: []` always
    # looks like a mistake -- it's rejected regardless of set_qc.
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "columns: []\n"
        "set_qc:\n"
        '  - rule_name: "x"\n'
        "    match_samples:\n"
        '      sample_pattern: "NTC"\n'
        "    checks:\n"
        "      - input_column: reads\n"
        "        qc:\n"
        '          - {operator: "<=", value: 1000}\n'
    )
    with pytest.raises(ConfigError, match="omit it entirely"):
        load_config(config_path)


def test_rejects_a_completely_blank_config(tmp_path):
    # neither columns nor set_qc configured -- the config does nothing.
    config_path = tmp_path / "config.yaml"
    config_path.write_text("set_qc: []\n")
    with pytest.raises(ConfigError, match="at least one of"):
        load_config(config_path)


def test_omitted_columns_is_allowed_when_set_qc_is_configured(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "set_qc:\n"
        '  - rule_name: "x"\n'
        "    match_samples:\n"
        '      sample_pattern: "NTC"\n'
        "    checks:\n"
        "      - input_column: reads\n"
        "        qc:\n"
        '          - {operator: "<=", value: 1000}\n'
    )
    config = load_config(config_path)
    assert config.columns is None
    assert len(config.set_qc) == 1


def test_rejects_duplicate_column_names():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {"columns": [{"input_column": "a"}, {"input_column": "a"}]}
        )


def test_rejects_ordering_operator_on_string_value():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {
                "columns": [
                    {"input_column": "a", "qc": [{"operator": ">", "value": "PASS"}]}
                ]
            }
        )


def test_approx_operator_accepts_tolerance_percent():
    config = ExportConfig.model_validate(
        {
            "columns": [
                {
                    "input_column": "length",
                    "qc": [
                        {"operator": "~=", "value": 1000000, "tolerance_percent": 5}
                    ],
                }
            ]
        }
    )
    condition = _first_qc(config)
    assert condition.operator.value == "~="
    assert condition.tolerance_percent == 5


def test_approx_operator_requires_tolerance_percent():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {
                "columns": [
                    {"input_column": "a", "qc": [{"operator": "~=", "value": 1000000}]}
                ]
            }
        )


def test_approx_operator_rejects_non_positive_tolerance_percent():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {
                "columns": [
                    {
                        "input_column": "a",
                        "qc": [
                            {"operator": "~=", "value": 1000000, "tolerance_percent": 0}
                        ],
                    }
                ]
            }
        )


def test_tolerance_percent_rejected_on_non_approx_operator():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {
                "columns": [
                    {
                        "input_column": "a",
                        "qc": [
                            {"operator": ">=", "value": 1000, "tolerance_percent": 5}
                        ],
                    }
                ]
            }
        )


def test_approx_operator_requires_numeric_value():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {
                "columns": [
                    {
                        "input_column": "a",
                        "qc": [
                            {"operator": "~=", "value": "PASS", "tolerance_percent": 5}
                        ],
                    }
                ]
            }
        )


def test_rejects_bool_value_on_equality_operator():
    # value=True/False shouldn't get silently coerced to 1.0/0.0 --
    # someone writing `value: true` means to match "true", not 1.
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {
                "columns": [
                    {"input_column": "a", "qc": [{"operator": "=", "value": True}]}
                ]
            }
        )


def test_rejects_bool_value_on_ordering_operator():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {
                "columns": [
                    {"input_column": "a", "qc": [{"operator": ">", "value": False}]}
                ]
            }
        )


def test_load_config_rejects_empty_file(tmp_path):
    empty = tmp_path / "empty.yaml"
    empty.write_text("")
    with pytest.raises(ConfigError):
        load_config(empty)


@pytest.mark.parametrize(
    "qc_entry",
    [
        '{operator: "=", value: true}',  # boolean value
        '{operator: ">", value: "PASS"}',  # string value on an ordering operator
    ],
)
def test_load_config_reports_value_type_errors_as_config_error(tmp_path, qc_entry):
    # These two checks live in QCCondition's model_validator. They must raise
    # ValueError (not TypeError) -- pydantic only wraps ValueError into
    # ValidationError, so a TypeError would escape load_config's handler and
    # reach the user as a raw traceback instead of a clean ConfigError.
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"columns:\n  - input_column: a\n    qc:\n      - {qc_entry}\n"
    )
    with pytest.raises(ConfigError):
        load_config(config_path)


def test_load_config_rejects_wrong_top_level_shape(tmp_path):
    # Valid YAML, but a list instead of a mapping -- a different failure
    # mode than bad syntax. Should still come back as ConfigError, not an
    # uncaught pydantic error.
    not_a_mapping = tmp_path / "list.yaml"
    not_a_mapping.write_text("- a\n- b\n")
    with pytest.raises(ConfigError):
        load_config(not_a_mapping)


def test_rejects_unknown_top_level_key():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {"columns": [{"input_column": "a"}], "not_a_real_key": True}
        )


def test_malformed_yaml_raises_config_error(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("columns: [this is not: valid: yaml")
    with pytest.raises(ConfigError):
        load_config(bad)


def test_contains_operator_accepts_string_value():
    config = ExportConfig.model_validate(
        {
            "columns": [
                {
                    "input_column": "organism",
                    "qc": [{"operator": "contains", "value": "Escherichia"}],
                }
            ]
        }
    )
    condition = _first_qc(config)
    assert condition.operator.value == "contains"
    assert condition.value == "Escherichia"
    assert condition.case_insensitive is False


def test_does_not_contain_operator_accepts_string_value():
    config = ExportConfig.model_validate(
        {
            "columns": [
                {
                    "input_column": "organism",
                    "qc": [{"operator": "does_not_contain", "value": "contaminant"}],
                }
            ]
        }
    )
    condition = _first_qc(config)
    assert condition.operator.value == "does_not_contain"


def test_is_empty_operator_accepts_no_value():
    config = ExportConfig.model_validate(
        {"columns": [{"input_column": "organism", "qc": [{"operator": "is_empty"}]}]}
    )
    condition = _first_qc(config)
    assert condition.operator.value == "is_empty"
    assert condition.value is None


def test_is_not_empty_operator_accepts_no_value():
    config = ExportConfig.model_validate(
        {
            "columns": [
                {"input_column": "organism", "qc": [{"operator": "is_not_empty"}]}
            ]
        }
    )
    assert _first_qc(config).operator.value == "is_not_empty"


def test_is_empty_operator_rejects_a_value():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {
                "columns": [
                    {
                        "input_column": "organism",
                        "qc": [{"operator": "is_empty", "value": "x"}],
                    }
                ]
            }
        )


def test_is_not_empty_operator_rejects_a_value():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {
                "columns": [
                    {
                        "input_column": "organism",
                        "qc": [{"operator": "is_not_empty", "value": "x"}],
                    }
                ]
            }
        )


def test_is_empty_operator_rejects_case_insensitive():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {
                "columns": [
                    {
                        "input_column": "organism",
                        "qc": [{"operator": "is_empty", "case_insensitive": True}],
                    }
                ]
            }
        )


def test_is_empty_operator_rejects_tolerance_percent():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {
                "columns": [
                    {
                        "input_column": "organism",
                        "qc": [{"operator": "is_empty", "tolerance_percent": 5}],
                    }
                ]
            }
        )


def test_ordinary_operator_requires_a_value():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {"columns": [{"input_column": "a", "qc": [{"operator": ">="}]}]}
        )


def test_contains_operator_rejects_numeric_value():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {
                "columns": [
                    {
                        "input_column": "organism",
                        "qc": [{"operator": "contains", "value": 5}],
                    }
                ]
            }
        )


def test_does_not_contain_operator_rejects_numeric_value():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {
                "columns": [
                    {
                        "input_column": "organism",
                        "qc": [{"operator": "does_not_contain", "value": 5}],
                    }
                ]
            }
        )


def test_case_insensitive_true_accepted_on_string_operators():
    config = ExportConfig.model_validate(
        {
            "columns": [
                {
                    "input_column": "organism",
                    "qc": [
                        {
                            "operator": "contains",
                            "value": "Escherichia",
                            "case_insensitive": True,
                        }
                    ],
                }
            ]
        }
    )
    assert _first_qc(config).case_insensitive is True


def test_contains_operator_rejects_empty_string_value():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {
                "columns": [
                    {
                        "input_column": "organism",
                        "qc": [{"operator": "contains", "value": ""}],
                    }
                ]
            }
        )


def test_does_not_contain_operator_rejects_empty_string_value():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {
                "columns": [
                    {
                        "input_column": "organism",
                        "qc": [{"operator": "does_not_contain", "value": ""}],
                    }
                ]
            }
        )


def test_case_insensitive_true_rejected_on_numeric_value():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {
                "columns": [
                    {
                        "input_column": "a",
                        "qc": [
                            {"operator": ">=", "value": 1000, "case_insensitive": True}
                        ],
                    }
                ]
            }
        )


def test_unknown_column_config_raises_on_load(tmp_path):
    # Loading only validates the config's own shape; missing-in-input-header
    # checking happens in pipeline.py, not here.
    config = load_config(config_unknown_column(tmp_path))
    assert config.columns is not None
    assert [c.input_column for c in config.columns] == ["sample_id", "does_not_exist"]
