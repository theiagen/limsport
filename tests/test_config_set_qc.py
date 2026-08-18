"""Config-shape validation for `set_qc` (run-level QC rules) -- end-to-end
behavior through transform.run_export lives in test_transform_set_qc.py."""

import pytest

from limsport.config import ExportConfig, SetQCMatch

_MINIMAL_COLUMNS = [{"name": "sample_id"}]


def _set_qc_config(*rules):
    return {"columns": _MINIMAL_COLUMNS, "set_qc": list(rules)}


def _check(**overrides):
    check = {"column": "reads", "qc": [{"operator": "<=", "value": 1000}]}
    check.update(overrides)
    return check


def _rule(**overrides):
    rule = {
        "name": "NTC read count",
        "match": {"sample_pattern": "NTC"},
        "columns": [_check()],
    }
    rule.update(overrides)
    return rule


def test_set_qc_defaults_to_empty_list():
    config = ExportConfig.model_validate({"columns": _MINIMAL_COLUMNS})
    assert config.set_qc == []


def test_set_qc_accepts_sample_pattern_matcher():
    config = ExportConfig.model_validate(_set_qc_config(_rule()))
    rule = config.set_qc[0]
    assert rule.name == "NTC read count"
    assert rule.columns[0].column == "reads"
    assert rule.match.sample_pattern == "NTC"
    assert rule.columns[0].qc[0].value == 1000


def test_set_qc_accepts_sample_regex_matcher():
    config = ExportConfig.model_validate(_set_qc_config(_rule(match={"sample_regex": "^NTC-?\\d*$"})))
    assert config.set_qc[0].match.sample_regex == "^NTC-?\\d*$"


def test_set_qc_accepts_samples_matcher():
    config = ExportConfig.model_validate(_set_qc_config(_rule(match={"samples": ["NTC1", "NTC2"]})))
    assert config.set_qc[0].match.samples == ["NTC1", "NTC2"]


def test_set_qc_rule_accepts_multiple_column_checks_under_one_match():
    config = ExportConfig.model_validate(
        _set_qc_config(
            _rule(
                columns=[
                    _check(column="reads", qc=[{"operator": "<=", "value": 1000}]),
                    _check(column="contam_pct", qc=[{"operator": "<=", "value": 0}]),
                ]
            )
        )
    )
    checks = config.set_qc[0].columns
    assert [c.column for c in checks] == ["reads", "contam_pct"]
    assert checks[0].qc[0].value == 1000
    assert checks[1].qc[0].value == 0


def test_set_qc_match_rejects_zero_matchers():
    with pytest.raises(Exception):
        ExportConfig.model_validate(_set_qc_config(_rule(match={})))


def test_set_qc_match_rejects_multiple_matchers():
    with pytest.raises(Exception):
        ExportConfig.model_validate(
            _set_qc_config(_rule(match={"sample_pattern": "NTC", "samples": ["NTC1"]}))
        )


def test_set_qc_match_rejects_empty_samples_list():
    with pytest.raises(Exception):
        ExportConfig.model_validate(_set_qc_config(_rule(match={"samples": []})))


def test_set_qc_match_rejects_invalid_regex():
    with pytest.raises(Exception):
        ExportConfig.model_validate(_set_qc_config(_rule(match={"sample_regex": "(unclosed"})))


def test_set_qc_match_rejects_unknown_subkeys():
    with pytest.raises(Exception):
        ExportConfig.model_validate(_set_qc_config(_rule(match={"sample_pattern": "NTC", "typo_key": 1})))


def test_set_qc_rule_requires_name():
    with pytest.raises(Exception):
        ExportConfig.model_validate(_set_qc_config(_rule(name="")))


def test_set_qc_rule_requires_at_least_one_column_check():
    with pytest.raises(Exception):
        ExportConfig.model_validate(_set_qc_config(_rule(columns=[])))


def test_set_qc_check_requires_column():
    with pytest.raises(Exception):
        ExportConfig.model_validate(_set_qc_config(_rule(columns=[_check(column="")])))


def test_set_qc_check_requires_at_least_one_qc_condition():
    with pytest.raises(Exception):
        ExportConfig.model_validate(_set_qc_config(_rule(columns=[_check(qc=[])])))


def test_set_qc_check_rejects_unknown_subkeys():
    with pytest.raises(Exception):
        ExportConfig.model_validate(_set_qc_config(_rule(columns=[_check(typo_key=1)])))


def test_set_qc_rule_rejects_duplicate_columns_within_one_rule():
    with pytest.raises(Exception):
        ExportConfig.model_validate(
            _set_qc_config(
                _rule(
                    columns=[
                        _check(column="reads", qc=[{"operator": "<=", "value": 1000}]),
                        _check(column="reads", qc=[{"operator": ">=", "value": 0}]),
                    ]
                )
            )
        )


def test_set_qc_rule_rejects_unknown_subkeys():
    with pytest.raises(Exception):
        ExportConfig.model_validate(_set_qc_config(_rule(typo_key=1)))


def test_set_qc_rejects_duplicate_rule_names():
    with pytest.raises(Exception):
        ExportConfig.model_validate(
            _set_qc_config(_rule(name="dup"), _rule(name="dup", match={"samples": ["X"]}))
        )


def test_set_qc_check_qc_does_not_accept_conditional_form():
    # a check's qc is always the plain-list form -- QCByRule
    # (match/rules/default) isn't valid here, since "which conditions apply"
    # is already answered by which sample matched.
    with pytest.raises(Exception):
        ExportConfig.model_validate(
            _set_qc_config(
                _rule(
                    columns=[
                        _check(qc={"match": "taxon", "rules": {"x": [{"operator": ">=", "value": 1}]}})
                    ]
                )
            )
        )


class TestSetQCMatchMatches:
    """Unit tests for SetQCMatch.matches(), independent of transform.py."""

    def test_sample_pattern_is_a_substring_match(self):
        match = SetQCMatch(sample_pattern="NTC")
        assert match.matches("NTC1") is True
        assert match.matches("SAMPLE_NTC_2") is True
        assert match.matches("SAMPLE_A") is False

    def test_sample_regex_uses_search_not_fullmatch(self):
        match = SetQCMatch(sample_regex="^NTC-?\\d*$")
        assert match.matches("NTC1") is True
        assert match.matches("NTC") is True
        assert match.matches("SAMPLE_NTC_2") is False  # anchored, so embedded doesn't match

    def test_samples_is_an_exact_list_match(self):
        match = SetQCMatch(samples=["NTC1", "NTC2"])
        assert match.matches("NTC1") is True
        assert match.matches("NTC3") is False
