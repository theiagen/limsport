"""Config-shape validation for qc_by -- end-to-end behavior through
transform.run_export lives in test_transform_qc_by.py."""

import pytest

from limsport.config import ExportConfig

_QC_BY_MINIMAL = {"match": "taxon", "rules": {"x": [{"operator": ">=", "value": 1}]}}


def _qc_by_config(**qc_by_kwargs):
    """A minimal ExportConfig payload with one qc_by column, whose own
    fields (match, rules, default) are overridden by qc_by_kwargs --
    shared by the qc_by validation tests below."""
    return {"columns": [{"name": "a", "qc_by": {**_QC_BY_MINIMAL, **qc_by_kwargs}}]}


def _column_with_qc_by(**column_kwargs):
    """A column carrying the same minimal qc_by plus whatever else
    column_kwargs adds -- shared by the qc_by mutual-exclusion tests
    below, which each pair qc_by with a conflicting column-level field."""
    return {"columns": [{"name": "a", "qc_by": _QC_BY_MINIMAL, **column_kwargs}]}


def test_qc_by_accepts_match_rules_and_optional_default():
    config = ExportConfig.model_validate(
        _qc_by_config(
            rules={
                "Escherichia coli": [{"operator": ">=", "value": 4600000}],
                "Klebsiella pneumoniae": [{"operator": ">=", "value": 5200000}],
            },
            default=[{"operator": ">=", "value": 100}],
        )
    )
    qc_by = config.columns[0].qc_by
    assert qc_by is not None
    assert qc_by.match == "taxon"
    assert qc_by.rules["Escherichia coli"][0].value == 4600000
    assert qc_by.default[0].value == 100


def test_qc_by_default_is_optional():
    config = ExportConfig.model_validate(_qc_by_config())
    assert config.columns[0].qc_by.default is None


def test_qc_by_requires_match():
    with pytest.raises(Exception):
        ExportConfig.model_validate({"columns": [{"name": "a", "qc_by": {"rules": {"x": []}}}]})


def test_qc_by_requires_rules():
    with pytest.raises(Exception):
        ExportConfig.model_validate({"columns": [{"name": "a", "qc_by": {"match": "taxon"}}]})


def test_qc_by_rejects_empty_rules():
    with pytest.raises(Exception):
        ExportConfig.model_validate(_qc_by_config(rules={}))


def test_qc_by_rejects_unknown_subkeys():
    with pytest.raises(Exception):
        ExportConfig.model_validate(_qc_by_config(typo_key=1))


def test_qc_by_and_column_level_qc_are_mutually_exclusive():
    with pytest.raises(Exception):
        ExportConfig.model_validate(_column_with_qc_by(qc=[{"operator": ">=", "value": 1}]))


def test_qc_by_rejected_on_a_file_parsing_column():
    with pytest.raises(Exception):
        ExportConfig.model_validate(
            _column_with_qc_by(file_parsing=[{"name": "out", "command": "cat"}])
        )
