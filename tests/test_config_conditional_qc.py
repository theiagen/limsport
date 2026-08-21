"""Config-shape validation for the conditional form of `qc` (the
match/rules/default mapping) -- end-to-end behavior through
pipeline.run_export lives in test_pipeline_conditional_qc.py."""

import pytest
from pydantic import ValidationError

from limsport.config import ExportConfig

_CONDITIONAL_QC_MINIMAL = {
    "match_column": "taxon",
    "cases": {"x": [{"operator": ">=", "value": 1}]},
}


def _conditional_qc_config(**qc_kwargs):
    """A minimal ExportConfig payload with one column whose qc is the
    conditional form, overridden by qc_kwargs -- shared by the
    validation tests below."""
    return {
        "columns": [
            {"input_column": "a", "qc": {**_CONDITIONAL_QC_MINIMAL, **qc_kwargs}}
        ]
    }


def test_conditional_qc_accepts_match_cases_and_optional_default():
    config = ExportConfig.model_validate(
        _conditional_qc_config(
            cases={
                "Escherichia coli": [{"operator": ">=", "value": 4600000}],
                "Klebsiella pneumoniae": [{"operator": ">=", "value": 5200000}],
            },
            default=[{"operator": ">=", "value": 100}],
        )
    )
    assert config.columns is not None
    qc = config.columns[0].qc
    assert not isinstance(qc, list)
    assert qc.match_column == "taxon"
    assert qc.cases["Escherichia coli"][0].value == 4600000
    assert qc.default is not None
    assert qc.default[0].value == 100


def test_conditional_qc_default_is_optional():
    config = ExportConfig.model_validate(_conditional_qc_config())
    assert config.columns is not None
    qc = config.columns[0].qc
    assert not isinstance(qc, list)
    assert qc.default is None


def test_conditional_qc_requires_match():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {"columns": [{"input_column": "a", "qc": {"cases": {"x": []}}}]}
        )


def test_conditional_qc_requires_rules():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {"columns": [{"input_column": "a", "qc": {"match_column": "taxon"}}]}
        )


def test_conditional_qc_rejects_empty_rules():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(_conditional_qc_config(cases={}))


def test_conditional_qc_rejects_missing_subkeys():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(_conditional_qc_config(typo_key=1))


def test_conditional_qc_rejected_on_a_file_parsing_column():
    # A column with file_parsing gets its qc from each output instead --
    # the column-level qc field (plain or conditional) isn't valid there.
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            {
                "columns": [
                    {
                        "input_column": "a",
                        "qc": _CONDITIONAL_QC_MINIMAL,
                        "file_parsing": [{"output_column": "out", "command": "cat"}],
                    }
                ]
            }
        )


def test_file_parsing_output_accepts_conditional_qc():
    config = ExportConfig.model_validate(
        {
            "columns": [
                {
                    "input_column": "a",
                    "file_parsing": [
                        {
                            "output_column": "out",
                            "command": "cat",
                            "qc": _CONDITIONAL_QC_MINIMAL,
                        }
                    ],
                }
            ]
        }
    )
    assert config.columns is not None
    file_parsing = config.columns[0].file_parsing
    assert file_parsing is not None
    output_qc = file_parsing[0].qc
    assert not isinstance(output_qc, list)
    assert output_qc.match_column == "taxon"
