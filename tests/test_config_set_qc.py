"""Config-shape validation for `set_qc` (run-level QC rules) -- end-to-end
behavior through pipeline.run_export lives in test_pipeline_set_qc.py."""

import pytest
from pydantic import ValidationError

from limsport.config import ExportConfig, SetQCMatch

_MINIMAL_COLUMNS = [{"input_column": "sample_id"}]


def _set_qc_config(*rules):
    return {"sample_qc": _MINIMAL_COLUMNS, "set_qc": list(rules)}


def _check(**overrides):
    check = {"input_column": "reads", "qc": [{"operator": "<=", "value": 1000}]}
    check.update(overrides)
    return check


def _rule(**overrides):
    rule = {
        "rule_name": "NTC read count",
        "match_samples": {"sample_pattern": "NTC"},
        "checks": [_check()],
    }
    rule.update(overrides)
    return rule


def test_set_qc_defaults_to_empty_list():
    config = ExportConfig.model_validate({"sample_qc": _MINIMAL_COLUMNS})
    assert config.set_qc == []


def test_set_qc_accepts_sample_pattern_matcher():
    config = ExportConfig.model_validate(_set_qc_config(_rule()))
    rule = config.set_qc[0]
    assert rule.rule_name == "NTC read count"
    assert rule.checks[0].input_column == "reads"
    assert rule.match_samples.sample_pattern == "NTC"
    assert rule.checks[0].qc[0].value == 1000


def test_set_qc_accepts_sample_regex_matcher():
    config = ExportConfig.model_validate(
        _set_qc_config(_rule(match_samples={"sample_regex": "^NTC-?\\d*$"}))
    )
    assert config.set_qc[0].match_samples.sample_regex == "^NTC-?\\d*$"


def test_set_qc_accepts_samples_matcher():
    config = ExportConfig.model_validate(
        _set_qc_config(_rule(match_samples={"samples": ["NTC1", "NTC2"]}))
    )
    assert config.set_qc[0].match_samples.samples == ["NTC1", "NTC2"]


def test_set_qc_rule_accepts_multiple_input_columns_under_one_match():
    config = ExportConfig.model_validate(
        _set_qc_config(
            _rule(
                checks=[
                    _check(
                        input_column="reads", qc=[{"operator": "<=", "value": 1000}]
                    ),
                    _check(
                        input_column="contam_pct", qc=[{"operator": "<=", "value": 0}]
                    ),
                ]
            )
        )
    )
    checks = config.set_qc[0].checks
    assert [c.input_column for c in checks] == ["reads", "contam_pct"]
    assert checks[0].qc[0].value == 1000
    assert checks[1].qc[0].value == 0


def test_set_qc_match_rejects_zero_matchers():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(_set_qc_config(_rule(match_samples={})))


def test_set_qc_match_rejects_multiple_matchers():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            _set_qc_config(
                _rule(match_samples={"sample_pattern": "NTC", "samples": ["NTC1"]})
            )
        )


def test_set_qc_match_rejects_empty_samples_list():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            _set_qc_config(_rule(match_samples={"samples": []}))
        )


def test_set_qc_match_rejects_invalid_regex():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            _set_qc_config(_rule(match_samples={"sample_regex": "(unclosed"}))
        )


def test_set_qc_match_rejects_missing_subkeys():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            _set_qc_config(
                _rule(match_samples={"sample_pattern": "NTC", "typo_key": 1})
            )
        )


def test_set_qc_rule_requires_name():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(_set_qc_config(_rule(rule_name="")))


def test_set_qc_rule_requires_at_least_one_check():
    with pytest.raises(ValidationError, match="include_sample_qc"):
        ExportConfig.model_validate(_set_qc_config(_rule(checks=[])))


_COLUMN_WITH_QC = {"input_column": "reads", "qc": [{"operator": ">=", "value": 1}]}


def test_set_qc_rule_with_include_sample_qc_needs_no_checks():
    config = ExportConfig.model_validate(
        {
            "sample_qc": [_COLUMN_WITH_QC],
            "set_qc": [_rule(checks=[], include_sample_qc=True)],
        }
    )
    assert config.set_qc[0].include_sample_qc
    assert config.set_qc[0].checks == []


@pytest.mark.parametrize(
    "sample_qc",
    [
        None,
        [{"input_column": "sample_id"}],
        [{"input_column": "taxon", "output": True, "qc": []}],
    ],
)
def test_include_sample_qc_requires_some_sample_level_qc(sample_qc):
    payload = {"set_qc": [_rule(include_sample_qc=True)]}
    if sample_qc is not None:
        payload["sample_qc"] = sample_qc
    with pytest.raises(
        ValidationError, match="no column in sample_qc has qc to include"
    ):
        ExportConfig.model_validate(payload)


def test_include_sample_qc_counts_file_parsing_qc():
    config = ExportConfig.model_validate(
        {
            "sample_qc": [
                {
                    "input_column": "report",
                    "file_parsing": [
                        {
                            "output_column": "n50",
                            "command": 'cat "$FILE"',
                            "qc": [{"operator": ">=", "value": 1}],
                        }
                    ],
                }
            ],
            "set_qc": [_rule(include_sample_qc=True)],
        }
    )
    assert config.set_qc[0].include_sample_qc


def test_set_qc_check_requires_input_column():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            _set_qc_config(_rule(checks=[_check(input_column="")]))
        )


def test_set_qc_check_requires_at_least_one_qc_condition():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(_set_qc_config(_rule(checks=[_check(qc=[])])))


def test_set_qc_check_rejects_missing_subkeys():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(_set_qc_config(_rule(checks=[_check(typo_key=1)])))


def test_set_qc_rule_rejects_duplicate_input_columns_within_one_rule():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            _set_qc_config(
                _rule(
                    checks=[
                        _check(
                            input_column="reads", qc=[{"operator": "<=", "value": 1000}]
                        ),
                        _check(
                            input_column="reads", qc=[{"operator": ">=", "value": 0}]
                        ),
                    ]
                )
            )
        )


def test_set_qc_rule_rejects_missing_subkeys():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(_set_qc_config(_rule(typo_key=1)))


def test_set_qc_rejects_duplicate_rule_names():
    with pytest.raises(ValidationError):
        ExportConfig.model_validate(
            _set_qc_config(
                _rule(rule_name="dup"),
                _rule(rule_name="dup", match_samples={"samples": ["X"]}),
            )
        )


def test_set_qc_check_qc_accepts_conditional_form():
    config = ExportConfig.model_validate(
        _set_qc_config(
            _rule(
                checks=[
                    _check(
                        qc={
                            "match_column": "taxon",
                            "cases": {"x": [{"operator": ">=", "value": 1}]},
                        }
                    )
                ]
            )
        )
    )
    assert config.set_qc[0].checks[0].qc.match_column == "taxon"


def test_set_qc_check_qc_rejects_empty_list():
    with pytest.raises(ValidationError, match="must not be empty"):
        ExportConfig.model_validate(_set_qc_config(_rule(checks=[_check(qc=[])])))


class TestSetQCMatchMatches:
    """Unit tests for SetQCmatch.applies_to(), independent of pipeline.py."""

    def test_sample_pattern_is_a_substring_match(self):
        match = SetQCMatch(sample_pattern="NTC")
        assert match.applies_to("NTC1") is True
        assert match.applies_to("SAMPLE_NTC_2") is True
        assert match.applies_to("SAMPLE_A") is False

    def test_sample_regex_uses_search_not_fullmatch(self):
        match = SetQCMatch(sample_regex="^NTC-?\\d*$")
        assert match.applies_to("NTC1") is True
        assert match.applies_to("NTC") is True
        assert (
            match.applies_to("SAMPLE_NTC_2") is False
        )  # anchored, so embedded doesn't match

    def test_samples_is_an_exact_list_match(self):
        match = SetQCMatch(samples=["NTC1", "NTC2"])
        assert match.applies_to("NTC1") is True
        assert match.applies_to("NTC3") is False
