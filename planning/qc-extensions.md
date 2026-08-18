# Planning: substring QC + set-level (run-level) QC

Status: **requirements gathering, no code written yet.** Written 2026-08-17 so we can
resume without re-deriving the architecture review below.

## The two requests

1. Substring QC operator: a string column passes if it *contains* a given substring,
   not just if it equals it exactly. Example: value `Escherichia coli` should pass a
   check for substring `Escherichia`.
2. Set-level (run-level) QC: a single sample's value can fail the *entire run*, not
   just itself. Example: if any NTC has `> 1000` reads, the whole run fails. Related:
   confirm positive controls are being classified as expected. Needs config support.

## Current architecture (relevant facts)

- `src/limsport/config.py` — Pydantic models, all `ConfigDict(extra="forbid")` (closed
  schema; unknown keys are rejected outright). `QCOperator` enum values are all
  symbols: `> >= = <= < ~=`. `QCCondition(operator, value: int|float|str|bool,
  tolerance_percent)` has a validator (`_validate_operator_constraints` or similar)
  that currently rejects non-numeric values for any operator except `=`. `ExportConfig`
  only has a `columns:` list today — no top-level key for anything cross-row.
- `src/limsport/qc.py` — `evaluate_condition()` branches on
  `isinstance(condition.value, str)` and treats any string value as an equality check,
  regardless of operator. This is the actual bug-in-waiting for substring support: it
  branches on *type*, not *operator*, so a new operator would silently collapse to `==`
  unless this is fixed.
- `src/limsport/transform.py::run_export()` — reads the whole input table and **already
  buffers every row's fate in memory** before writing (`output_rows` accumulates, the
  TSV is written once at the end). This matters: a two-pass design (gather run-level
  facts, then gate the export) is cheap here, not a big rewrite, because nothing is
  streamed to disk incrementally today.
  - QC only ever sees columns listed under `columns:` in the config (via
    `_build_name_index` / `match_index` / `_collect_match_columns`, the same plumbing
    conditional QC (`QCByRule.match`) already uses to look up a value that may not be
    an *output* column). A run-level check (e.g. an NTC read-count column) needs the
    same "readable but not necessarily exported" treatment if it isn't otherwise kept.
  - **Failing rows are dropped from output today** (`if not outcome.passed: continue`).
    There is no existing concept of "the whole export failed" — only "N/M samples
    passed." This is the crux of open question 1 below.
- `src/limsport/report.py` — per-*failure* TSV rows (sample, column, operator,
  expected, actual, reason) plus log lines. No run-level verdict concept exists.
- `src/limsport/cli.py::main()` — returns exit code 1 only for exceptions
  (`LIMSportError`/`OSError`). A config that "runs clean" but has 0/10 samples pass QC
  currently still exits 0. No exit-code signal exists today for "the run's QC failed
  as a whole."
- Tests mirror `src/` 1:1 (e.g. `tests/test_config_conditional_qc.py`,
  `tests/test_transform_conditional_qc.py`). `examples/` are self-contained
  directories with a golden `output.tsv`/`qc_report.tsv`, used both as docs and as
  round-trip tests.
- **Side-repo drift risk**: `docs/config-builder/` is a second, hand-maintained
  implementation of this same config schema (browser-side JS, validated against the
  real Python `load_config()` in its own test suite). Any change to `QCOperator` or
  `QCCondition` needs a matching change in `docs/config-builder/schema.js`
  (`OPERATORS`, the numeric-value gate in `buildCondition`, and
  `yamlEqualityValue`'s quoting logic, which is currently gated on
  `operator === "="` specifically) or the builder will generate configs the real
  validator rejects (or vice versa). Add a case to
  `docs/config-builder/tests/schema.test.mjs` for whatever gets decided here.

## Feature 1 — substring QC: touch list (once operator semantics are decided)

1. `config.py::QCOperator` — add new member.
2. `config.py::QCCondition` validator — allow (or require) a string value for the new
   operator; keep rejecting `tolerance_percent` for it same as other non-numeric ops.
3. `qc.py::evaluate_condition` — branch on **operator**, not `isinstance(value, str)`.
4. `docs/config-builder/schema.js` — `OPERATORS`, `buildCondition`'s value-type gate,
   `yamlEqualityValue`'s quoting rule.
5. New tests (`tests/test_config.py`, `tests/test_qc.py`) + a case in
   `docs/config-builder/tests/schema.test.mjs`.
6. Maybe a new example under `examples/` if this needs end-to-end documentation.

## Feature 2 — set-level QC: open design, no touch list yet

Deliberately not sketching a `run_checks:`/config shape yet — question 1 and question
5 below will reshape it. Once answered, this section gets a real design.

## Answers

**Substring QC:**
- New operators: `contains` and a "does not contain" variant (name TBD, e.g.
  `does_not_contain`), both taking a string value only.
- Default is case-sensitive, matching existing `=` behavior. Add an opt-in
  case-insensitivity flag that applies to *both* `=` and `contains`/`does_not_contain`
  (name/shape TBD — likely a new `QCCondition` field, e.g. `case_sensitive: bool =
  True`, rejected as a config error on numeric operators).

**Set-level QC:**
1. On set-level failure: no special output-suppression behavior decided yet beyond
   what's already true today (failing rows are dropped) — what's new is the *report*:
   add a row indicating the sample failed because of a set-level rule, with
   `operator`/`expected` left blank and `reason` describing which set-level rule
   fired. (Whether the run *also* gets a distinct non-zero exit code / whole-file
   failure signal is still open — see remaining questions below.)
2. Sample identity is **already well-established**: `transform.py` reads
   `sample = row[0]` — the sample name is always the input table's first column. So
   both matching strategies below key off that same existing value, no new
   column-selection concept needed. Two candidate config shapes to choose between
   (see "Config examples" below): (a) a naming pattern against the sample ID, (b) an
   explicit list of sample names in the config.
3. A required set-level rule with **zero matching samples in the run fails the run**,
   with a message indicating a set-level rule was configured but had no sample to
   apply it to.
4. Confirmed: "positive control classified appropriately" and "NTC exceeds threshold"
   are the *same* mechanism — a per-matched-sample assertion that fails the run when
   not met. No separate "positive control" concept needed in the schema.
5. Multiple NTCs/positive controls can coexist in one run. **All matched samples must
   pass their rule** — user confirmed there's no meaningful difference between
   "every one passes" and "none exceeds threshold" framings, so no aggregate
   (sum/count) functions are needed, just per-sample evaluation of the same rule.

## Config examples for set-level matching (pick one, or support both)

Both use a new top-level `set_qc:` key (name not yet finalized) alongside `columns:`.
Each entry identifies a sample or samples (by pattern or by explicit list), a column
to read (does **not** need to be in the `columns:` output allow-list — same
"readable but not necessarily exported" plumbing conditional QC's `match` already
uses), and one or more QC conditions (reusing `QCCondition`, including the new
`contains`/`does_not_contain` operators) that every matched sample must satisfy.

**(a) Naming pattern on sample ID** — matches any sample whose name contains (or
matches) the given pattern:

```yaml
set_qc:
  - name: "NTC read count"
    match:
      sample_pattern: "NTC"      # matched against row[0], substring by default
    column: reads
    qc:
      - operator: "<="
        value: 1000
  - name: "Positive control identity"
    match:
      sample_pattern: "PC"
    column: predicted_organism
    qc:
      - operator: "contains"
        value: "Escherichia coli"
```

**(b) Explicit list of sample names in the config:**

```yaml
set_qc:
  - name: "NTC read count"
    match:
      samples: ["NTC1", "NTC2", "NTC-blank"]
    column: reads
    qc:
      - operator: "<="
        value: 1000
  - name: "Positive control identity"
    match:
      samples: ["PC1"]
    column: predicted_organism
    qc:
      - operator: "contains"
        value: "Escherichia coli"
```

Both could be supported at once (`match:` accepts either `sample_pattern` or
`samples`, like `QCByRule.match` already offers alternatives), if that's useful
rather than picking one.

## Final decisions

1. `sample_pattern` reuses the same substring (`contains`) semantics as the QC
   operator. **Also** add a separate regex option, `sample_regex`, as an alternative
   matcher within `match:` (not a replacement for `sample_pattern` — both exist).
2. No new exit-code / whole-run-failure signal for now. The QC report row (sample
   failed because a set-level rule fired, `operator`/`expected` blank, `reason`
   describing the rule) is enough.
3. Top-level key is `set_qc`. Operators are `contains` / `does_not_contain`.
4. Support **both** matching strategies at once, right now: a `set_qc` entry's
   `match:` block may use `sample_pattern` (substring), `sample_regex` (regex), or
   `samples` (explicit list) — mutually exclusive per entry, same "pick one
   alternative" shape `QCByRule.match` already uses elsewhere in the schema.

Updated config example (all three matcher kinds shown):

```yaml
set_qc:
  - name: "NTC read count"
    match:
      sample_pattern: "NTC"          # substring match against row[0]
    column: reads
    qc:
      - operator: "<="
        value: 1000
  - name: "NTC read count (regex form)"
    match:
      sample_regex: "^NTC-?\\d*$"    # full regex match against row[0]
    column: reads
    qc:
      - operator: "<="
        value: 1000
  - name: "Positive control identity"
    match:
      samples: ["PC1"]               # explicit list
    column: predicted_organism
    qc:
      - operator: "contains"
        value: "Escherichia coli"
```

Requirements gathering is now considered **complete**. Next session should move to
implementation planning proper: exact Pydantic model shapes for `SetQCRule`/its
`match` union, the `case_sensitive` field shape for `QCCondition`, where in
`transform.py` the set-level pass runs (before or after per-row QC / output writing),
and the full touch list for `docs/config-builder/` mirroring these schema changes.

## Design session 2 (2026-08-18) — resolving the scope ambiguity + concrete design

**Corrections to session 1's plan, found by re-reading `config.py`/`transform.py`
directly instead of from memory:**

- `QCByRule.match` is just a **column name string** (`match: str`) — the row's
  match value in that column selects which `rules[...]` list applies. It is *not*
  a "pick one alternative" union. There is no existing precedent in this codebase
  for a mutually-exclusive-fields matcher; `SetQCMatch` (below) is new, modeled
  after `QCCondition`'s own `tolerance_percent` validator pattern
  (`model_validator(mode="after")` counting which optional fields are set), not
  after `QCByRule`.
- Confirmed `run_export()` already buffers every row's fate before writing
  (`output_rows` list, one `table_io.write_tsv()` call at the end) — the two-pass
  restructuring below is cheap, not a rewrite.

**The scope question, now resolved:** when a set_qc rule fails, **the whole run
fails** — every sample is dropped from output (`output.tsv` ends up header-only)
and every sample gets a QC-report row referencing the failure, not just the
offending control. This was previously left ambiguous (session 1 hedged, then
the plan drifted into describing per-row-only behavior); explicitly confirmed via
AskUserQuestion this session. This means `transform.py` needs a **real two-pass
split**: evaluate every row into a buffered outcome first, decide every set_qc
rule's fate, *then* decide final per-row output/report based on whether any
set_qc rule failed.

### Config schema

```python
class SetQCMatch(BaseModel):
    """Exactly one of these three identifies which sample(s) a set_qc rule applies to."""
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    sample_pattern: str | None = None   # substring match against row[0], case-sensitive
    sample_regex: str | None = None     # re.search against row[0], case-sensitive
    samples: list[str] | None = None    # explicit list of exact sample names

    @model_validator(mode="after")
    def _exactly_one_matcher(self) -> "SetQCMatch":
        # same shape as QCCondition._validate_operator_constraints: count which
        # optional fields are set, reject 0 or >1
        ...
        # sample_regex is compiled here (re.compile) so a bad pattern is a
        # config-load error, not a mid-run crash
        # samples, if given, must be non-empty

class SetQCRule(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)     # required, used in QC-report reason text
    match: SetQCMatch
    column: str = Field(min_length=1)   # input column to read; need not be in `columns:`
    qc: list[QCCondition] = Field(min_length=1)   # plain list only, no QCByRule form

# ExportConfig gains:
    set_qc: list[SetQCRule] = Field(default_factory=list)
```

`column` is validated to exist in the input header via the *same*
`_validate_header_reference` helper `QCByRule.match` already uses (extend
`_validate_columns_exist` and `_collect_match_columns`'s `match_index`-building
in `transform.py` to also cover `set_qc[].column`, rather than a parallel path).

### Matching semantics (defaults chosen, flagging for a quick confirm)

- `sample_pattern`: substring match (`pattern in sample`), same as the new
  `contains` QC operator — reuses the exact same logic.
- `sample_regex`: `re.search(pattern, sample)`, **not** `fullmatch`. Chosen
  because the example the user already wrote themselves
  (`sample_regex: "^NTC-?\\d*$"`) anchors manually with `^`/`$` — i.e. they're
  already writing regexes assuming "search," not expecting free anchoring.
- Sample-name matching (all three forms) is **always case-sensitive** — no
  `case_sensitive` knob for the matcher itself (that flag is a `QCCondition`
  concern, for the *value* comparison, not sample-name matching). A regex user
  who wants case-insensitivity can use `(?i)` inline.
- `set_qc` matching happens on **candidate rows** (i.e. *after* `--samples`
  allow-list filtering), same population as per-row QC and output. An NTC
  excluded via `--samples` is treated as not part of the run being validated.

### Zero-match handling

A `set_qc` rule that matches **zero** candidate rows raises `InputTableError`
immediately after the main loop, aborting the whole export before any output is
written (existing `cli.py` exception handling already turns this into a
non-zero exit + stderr message — no new exit-code plumbing needed). This is
stricter than "whole run fails via QC report": there's no sample to attach a
`QCFailure` to (its `sample` field is required), so this case is a hard error,
not a QC failure.

### Two-pass `run_export()` restructuring

Key implementation insight: reuse `qc.evaluate_row()` itself to evaluate a
set_qc rule, instead of hand-rolling condition evaluation. For a matched row:

```python
rule_outcome = qc.evaluate_row(
    [qc.ResolvedField(rule.column, rule.column, raw_value, rule.qc, None)], sample
)
```

If `not rule_outcome.passed`, `rule_outcome.failures` is already a list of
fully-formed `QCFailure` objects (correct `operator`/`expected`/`actual`/
`reason`) — no new failure-construction code needed for the "offending sample"
case.

Sketch:

```python
# Pass 1: buffer every row's per-row QC outcome; also feed set_qc matching
buffered = []  # (sample, fields_or_row, per_row_outcome_or_None)
set_qc_matched: dict[str, list[str]] = {r.name: [] for r in config.set_qc}
set_qc_failures: dict[str, list[QCFailure]] = {r.name: [] for r in config.set_qc}

for row in table_io.iter_rows(...):
    ... existing sample/--samples filtering ...
    if config is not None:
        ... existing field resolution + qc.evaluate_row(fields, sample) -> outcome ...
        for rule in config.set_qc:
            if _sample_matches(rule.match, sample):
                set_qc_matched[rule.name].append(sample)
                raw_value = row[set_qc_col_index[rule.column]]
                rule_outcome = qc.evaluate_row([qc.ResolvedField(rule.column, rule.column, raw_value, rule.qc, None)], sample)
                if not rule_outcome.passed:
                    set_qc_failures[rule.name].extend(rule_outcome.failures)
        buffered.append((sample, fields, outcome))
    else:
        buffered.append((sample, row, None))

# zero-match check -- hard error, aborts before any output
for rule in config.set_qc:
    if not set_qc_matched[rule.name]:
        raise InputTableError(f"set_qc rule {rule.name!r} matched no samples in this run")

run_failed_rules = [r for r in config.set_qc if set_qc_failures[r.name]]

output_rows, all_failures, passed_rows = [], [], 0
if run_failed_rules:
    # whole run fails: no output rows at all
    offending_samples: set[str] = set()
    for rule in run_failed_rules:
        all_failures.extend(set_qc_failures[rule.name])
        offending_samples.update(f.sample for f in set_qc_failures[rule.name])
    failing_rule_names = [r.name for r in run_failed_rules]
    for sample, fields, outcome in buffered:
        if sample in offending_samples:
            continue  # already has a detailed failure row above
        all_failures.append(QCFailure(
            sample=sample, column="", output_column="", operator=None, expected=None,
            actual=None, reason=f"run failed QC due to set_qc rule(s): {failing_rule_names}",
        ))
    # output_rows stays empty
else:
    for sample, fields, outcome in buffered:
        ... existing per-row QC pass/fail + output_rows.append(...) logic, unchanged ...
```

This is a genuine restructuring of `run_export()`'s single loop into
buffer-then-decide, but every per-row QC rule stays byte-identical in behavior
when no `set_qc` is configured (`config.set_qc == []`) — the new code paths are
strict no-ops in that case, so the three existing `examples/` goldens are
expected to remain byte-identical.

**Confirmed:** when multiple set_qc rules fail at once, a collateral
(non-offending) sample gets **one combined report row** naming all failing
rules, not one row per rule. Matches the sketch above as written.

### `docs/config-builder/` touch list (session 1 undersold this — it's comparable
in size to the Python side, not a mirror of an enum tweak)

- `schema.js`: `OPERATORS` (add `contains`/`does_not_contain`), a new
  `newSetQCRule`/`newSetQCMatch` factory, `buildConfig` support for serializing
  `set_qc:`, exactly-one-matcher validation (mirrors the Python model_validator,
  independently reimplemented since there's no shared code between the JS tool
  and the Python package), key ordering in `serializeYAML`.
- `app.js`: new UI section entirely (a "Set-level QC" card type, separate from
  the per-column cards) — matcher-type selector (pattern/regex/samples),
  condition list reusing existing QC-condition editor UI, target-column text
  input.
- **Do not validate `sample_regex` client-side against JS `RegExp`** — JS and
  Python regex dialects diverge; a browser-side syntax check could reject a
  config that's valid to the real Python validator. Let the real
  `load_config()` test harness be the only regex-syntax gate, same as the tool's
  existing philosophy for every other field.
- `tests/schema.test.mjs`: new cases for `contains`/`does_not_contain` and for
  `set_qc` round-tripping through the real Python validator.

### Sequencing

Land `contains`/`does_not_contain` (feature 1) first, as its own self-contained
change with its own tests — feature 2's positive-control check depends on it
for a real end-to-end example, and it's a much smaller, independently reviewable
diff. Then build `set_qc` on top.

### Status

Design is complete. All open questions are resolved.

**Feature 1 (`contains`/`does_not_contain` + `case_sensitive`) is implemented**
(2026-08-18), not yet committed:

- `src/limsport/config.py`: `QCOperator.CONTAINS`/`DOES_NOT_CONTAIN` added;
  `QCCondition` gained `case_sensitive: bool = True`; validator updated to
  require a string value for the two new operators and to reject
  `case_sensitive=False` on a non-string value.
- `src/limsport/qc.py`: `evaluate_condition` now branches on operator first
  (`CONTAINS`/`does_not_contain` handled explicitly via a new `_evaluate_contains`
  helper), then falls through to the existing string-EQ / numeric paths;
  `case_sensitive` folds both sides via `.casefold()` before comparing, for
  both `=` and the two new operators.
- Tests added to `tests/test_config.py` and `tests/test_qc.py`; full suite
  (`pytest tests/`) passes at 195/195.
- `docs/config-builder/`: `schema.js` (`OPERATORS`, new `STRING_OPERATORS`
  export, `buildCondition`'s value-type gate, `case_sensitive` emission,
  `renderConditionInline`'s quoting rule extended to the new operators),
  `app.js` (operator dropdown auto-picks up the new options; added a
  case-sensitivity checkbox shown only for string operators), `style.css`
  (small addition for the new checkbox's layout). JS test suite
  (`node --test tests/`) passes at 17/17, including new cases round-tripping
  `contains`/`does_not_contain` and `case_sensitive` through the real
  `load_config()`.
- `README.md`'s QC operators table updated to list `contains`/`does_not_contain`
  and document `case_sensitive` (was previously undersold — the code was
  changed in three places but the user-facing operator spec wasn't).
  `examples/README.md`/`examples/basic/config.yaml` intentionally left
  untouched for now — those describe what the existing fixtures demonstrate,
  not a claim about the full operator set; a new example is a separate,
  optional follow-up, not required for feature 1.
- Hardening found on advisor review: `{operator: contains, value: ""}` used to
  pass `load_config()` and then always match — a silently-inert QC rule. Now
  rejected at config-validation time (same precedent as
  `FileParsingOutput._command_not_blank`'s blank-string rejection). Also
  pinned via test: a blank cell fails `does_not_contain` the same way it fails
  every other operator (the "missing value" guard runs before any
  operator-specific logic), even though a blank cell technically "does not
  contain" anything — documented as a deliberate consistency choice, not an
  oversight.
- Fixed (per your request) together, not left as a quirk: `app.js`'s operator
  `change` handler now clears `cond.tolerancePercent` whenever the operator
  moves away from `~=`, and clears `cond.caseSensitive` back to the default
  (`true`) whenever it moves away from a string operator (`=`/`contains`/
  `does_not_contain`) — resetting both the in-memory state and the (now-hidden)
  input's displayed value. Previously either leftover value could trip
  `buildConfig`'s validation with no visible control left to fix it; this
  fixes the root cause (stale state) rather than papering over the symptom.
- Full test suites pass: 198/198 Python (`pytest tests/`), 17/17 JS
  (`node --test docs/config-builder/tests/`).
- Nothing committed to git yet — pending your review.

Next step: `set_qc` (feature 2), per the design above.

### Design session 3 — `set_qc` schema started, then a naming change to feature 1

Started walking through `set_qc` implementation step by step (per your request to
go slowly). Step 1 (config schema) is done:

- `src/limsport/config.py`: added `SetQCMatch` (exactly-one-of `sample_pattern` /
  `sample_regex` / `samples`, with a `.matches(sample)` method and `sample_regex`
  compiled eagerly so a bad pattern is a config-load error) and `SetQCRule`
  (`name`, `match: SetQCMatch`, `column`, `qc: list[QCCondition]`).
  `ExportConfig` gained `set_qc: list[SetQCRule] = Field(default_factory=list)`,
  plus a duplicate-rule-name check mirroring the existing `_find_duplicates`
  pattern used for column names.
- Not yet done: the `transform.py` two-pass restructuring (steps 2-3), tests
  (step 5), or the `docs/config-builder/` mirror (step 6). No tests added yet for
  the new models, so don't treat this step as verified — it hasn't been run
  against `pytest` yet.

Paused there because you asked for an unrelated but immediate change to
**feature 1**: rename `case_sensitive: bool = True` to **`case_insensitive: bool
= False`** (inverted default and polarity), and change the config builder's
checkbox from "case-sensitive" to an "ignore case" checkbox. Done across the
whole stack:

- `src/limsport/config.py`: `QCCondition.case_sensitive` → `case_insensitive`
  (default `False`); validator now rejects `case_insensitive=True` on a
  non-string value (inverted from the old "reject `case_sensitive=False`"
  check).
- `src/limsport/qc.py`: `_fold()` and both call sites (`_evaluate_contains`, the
  string-EQ branch of `evaluate_condition`) inverted to `case_insensitive`
  semantics (folds when `True`, previously folded when *not* `case_sensitive`).
- `tests/test_config.py`, `tests/test_qc.py`: renamed/inverted accordingly.
- `docs/config-builder/schema.js`: `newCondition().caseInsensitive` (default
  `false`), `buildCondition`'s validation and emission (`case_insensitive: true`
  only emitted when set, same "omit at default" convention as before),
  `renderConditionInline`'s check inverted.
- `docs/config-builder/app.js`: checkbox variable/class renamed
  (`caseInsensitiveInput`/`.cond-case-insensitive`), **label text changed to
  "ignore case"**, checked state now represents `caseInsensitive` directly
  (checked = ignore case = `true`), and the operator-switch reset logic (added
  last session for the `tolerancePercent` quirk) updated to reset
  `caseInsensitive` back to `false` instead of `true`.
- `docs/config-builder/style.css`: `.cond-case-sensitive` → `.cond-case-insensitive`.
- `docs/config-builder/tests/schema.test.mjs`: renamed/inverted accordingly.
- Full suites re-verified after the rename: 198/198 Python, 17/17 JS.

**Also separately noticed, not yet acted on:** at some point between sessions,
`README.md` was modified externally and no longer contains this session's
earlier documentation of `contains`/`does_not_contain`/`case_insensitive` (the
operators table is back to only listing `>`, `>=`, `<=`, `<`, `=`, `~=`, and
there's no `case_sensitive`/`case_insensitive` prose or example). This looks
like an accidental revert (e.g. an editor with a stale buffer saving over the
file) rather than an intentional edit, since it doesn't match anything you
asked for this session. **Flagged to you, not fixed** — per the "don't silently
undo external changes" rule, but also didn't seem right to silently leave
stale, so: confirm whether you want the operator-table documentation
reinstated (it would need updating anyway for `does_not_contain` and
`case_insensitive`, the current names, not the `not_contains`/`case_sensitive`
names it had when first written).

Next step, once you're ready to resume the `set_qc` walkthrough: step 2
(`transform.py` sample-matching helper), then step 3 (the two-pass
restructuring).

### Design session 7 — `transform.py` two-pass restructuring (steps 2+3)

Step 2 (sample matching) turned out to already be covered by
`SetQCMatch.matches()` from step 1 — no separate transform.py helper needed;
`run_export` just calls `rule.match.matches(sample)` directly.

Step 3, the real work, is done:

- `_validate_set_qc_columns()` added (mirrors `_validate_columns_exist`):
  checks each `set_qc[].column` exists unambiguously in the input header,
  using the same `_validate_header_reference` helper `QCByRule.match`
  already uses.
- `match_index` (the "readable but not necessarily exported" column lookup)
  now also includes every `set_qc[].column`, via
  `_collect_match_columns(config.columns) | {rule.column for rule in config.set_qc}`
  — `_collect_match_columns` itself is untouched, kept single-purpose.
- `run_export`'s single loop is now buffer-then-decide: every row's resolved
  fields go into `buffered` (a `(sample, fields)` list) without yet deciding
  pass/fail. Within the same loop, each `set_qc` rule is checked against
  each row: on a match, `qc.evaluate_row()` is reused against a synthetic
  `ResolvedField` for that rule's column/value/conditions — this was the
  key reuse insight from the design phase, since a failing `rule_outcome`
  already carries fully-formed `QCFailure` objects (right operator/expected/
  actual/reason) with zero new failure-construction code.
- After the loop: any `set_qc` rule matching zero samples raises
  `InputTableError` (hard error, aborts before any output is written).
  Otherwise, if any rule's matched samples produced failures, the *whole
  run* fails: `output_rows` stays empty, the offending sample(s) get their
  full-detail failure row(s), and every other candidate sample gets one
  combined collateral row naming all failing rules (per your "one combined
  row" answer from session 1). Otherwise (the common case — no `set_qc`
  configured, or all rules pass), per-row QC evaluation proceeds exactly as
  before, just now as a second pass over `buffered` instead of inline in the
  first loop.
- **Verified zero regression**: full pytest suite (198/198) and all three
  `examples/*/run_examples.sh` end-to-end scripts pass unchanged — `set_qc`
  defaults to `[]`, so `run_failed_rules` is always empty for any config that
  doesn't use it, meaning every existing config takes the exact same code
  path (just reached one loop iteration later) as before this change.
- **Verified the new behavior directly** (manual end-to-end run, not yet a
  committed automated test): built a 3-sample input with an NTC and a
  `set_qc` rule capping its read count.
  - *Pass case*: all 3 samples pass, empty QC report, as expected.
  - *Fail case*: NTC's cap exceeded → `0/3 samples passed QC`, output.tsv is
    header-only, QC report has NTC1's full detail (`<=`, `100`, `500`,
    reason) plus one blank/combined collateral row per other sample naming
    the failing rule.
  - *Zero-match case*: a rule matching a nonexistent "POS" pattern → hard
    error, exit code 1, message naming the rule.
  - All three matched the design exactly.

Not yet done: automated `pytest` tests for any of this (step 5) — the manual
run above is real end-to-end verification but isn't committed as a
regression test yet. Also not yet done: the `docs/config-builder/` mirror
(step 6).

### Design session 8 — step 5: automated tests

Two new files, following the project's existing split convention (config-shape
validation vs. end-to-end transform behavior, same as
`test_config_conditional_qc.py` / `test_transform_conditional_qc.py`):

- `tests/test_config_set_qc.py` (18 tests): `SetQCMatch`/`SetQCRule`/
  `ExportConfig.set_qc` shape validation — each matcher kind accepted, zero
  or multiple matchers rejected, empty `samples` list rejected, invalid
  `sample_regex` rejected, required fields (`name`, `column`, non-empty
  `qc`) enforced, unknown subkeys rejected (`extra="forbid"`), duplicate
  rule names rejected, `set_qc` defaults to `[]`, and `SetQCRule.qc` doesn't
  accept the `QCByRule` conditional form. Also a `TestSetQCMatchMatches`
  class unit-testing `.matches()` directly (substring, anchored regex via
  `re.search`, exact list) independent of `transform.py`.
- `tests/test_transform_set_qc.py` (11 tests): end-to-end through
  `transform.run_export` — pass case (every sample kept, empty report),
  fail case (output fully zeroed, offending sample gets full detail,
  collateral samples get blank rows naming the rule), zero-match hard
  error before any output is written, `column` not in the input header
  hard error, `column` need-not-be-in-the-output-allow-list (mirrors
  `QCByRule.match`'s existing freedom), all three matcher kinds
  (`sample_pattern`/`sample_regex`/`samples`) exercised end-to-end, multiple
  simultaneously-failing rules producing one combined collateral reason
  naming both, and an explicit regression guard confirming a config with no
  `set_qc` key behaves identically to before `set_qc` existed.
- Two test-authoring bugs caught and fixed while writing these (not product
  bugs): a single-column input/output can't have its delimiter
  auto-detected by `table_io.detect_delimiter` — fixed by giving the
  "column not in header" test input a second real column, and by passing
  `delimiter="\t"` explicitly when reading back the single-column-output
  test's result.
- Full suite: **227/227 passing** (198 prior + 18 + 11 new).

Remaining: step 6, the `docs/config-builder/` mirror (`set_qc` is a new UI
section — matcher-type selector, condition list reuse, `newSetQCRule`
factory, `serializeYAML` support — comparable in size to the Python side, per
the original design note).

### Design session 9 — step 6, part 1: `schema.js` logic layer

- `newSetQCMatch()`/`newSetQCRule()` factories added, following the existing
  `kind`-discriminator convention (`newQC()`'s `"none"|"list"|"conditional"`)
  — `kind: "pattern"|"regex"|"samples"` picks which of `samplePattern`/
  `sampleRegex`/`samples` wizard state actually gets built; the other two
  are just unused state, so no "exactly one" validator is needed client-side
  (unlike the Python `SetQCMatch` model, which has to validate arbitrary
  hand-written YAML). `samples` is a single comma/newline-separated text
  field in the UI, split/trimmed/filtered into a list on build.
- `buildConfig(columns, setQCRules = [])` gained a second, optional
  parameter (default `[]` keeps every existing call site/test working
  unchanged) — builds `plain.set_qc` only when at least one rule is
  configured (omitted entirely otherwise, so a config with no `set_qc`
  doesn't grow a `set_qc: []` line). Validates: rule name/column/≥1
  condition required, matcher-specific requirements (non-blank pattern/
  regex, non-empty samples list), and duplicate rule names — deliberately
  **not** validating `sample_regex` syntax client-side (JS/Python regex
  dialects diverge; confirmed the real `load_config()` still catches a bad
  pattern in its own test).
- `serializeYAML()` extended to render a `set_qc:` section after `columns:`
  when present. Rule `name`, and every match-matcher string
  (`sample_pattern`/`sample_regex`/each `samples` entry), are always quoted
  via the existing `yamlKey()` helper — consistent with your "intended-string
  values should always be quoted" direction from earlier this session,
  applied proactively here rather than needing a follow-up fix.
  `column:` uses `yamlScalar` (a column-name reference, same treatment as
  `QCByRule.match`). A rule's `qc:` list reuses `renderConditionList`
  unchanged, so `contains`/`does_not_contain`/`case_insensitive` all work
  inside `set_qc` for free.
- Verified end-to-end against the real `load_config()` with a hand-built
  two-rule scenario (a `samples`-matcher rule and a `sample_regex`-matcher
  rule with a `contains` condition) before writing formal tests — both
  parsed and round-tripped correctly.
- 11 new tests added to `docs/config-builder/tests/schema.test.mjs`:
  omitted-when-empty, each matcher kind round-tripping through the real
  validator, `contains` inside `set_qc`, quoting behavior, each required-field
  validation, duplicate-name rejection, and confirming an invalid
  `sample_regex` is still caught (by the real validator, since the client
  doesn't check regex syntax). **Full JS suite: 30/30 passing** (19 prior +
  11 new).

Not yet done: the UI wiring in `app.js` (a new "Set-level QC" section:
matcher-type selector, condition-list reuse, add/remove rule controls),
`index.html`/`docs/config.md` markup for the new section, and matching
`style.css`. Paused here to check in before starting that part.

### Design session 10 — step 6, part 2: UI wiring (this completes step 6)

- `app.js`: `state.setQCRules = []` added alongside `state.columns`. New
  `buildSetQCMatchEditor(match)` (a `<select>` for `pattern`/`regex`/
  `samples`, with the corresponding one of three text inputs shown/hidden
  via the same `.hidden` toggle pattern used throughout) and
  `buildSetQCRuleCard(rule)` (rule name, the match editor, target column,
  and a `buildConditionsEditor(rule.conditions)` reused as-is — `contains`/
  `does_not_contain`/`case_insensitive`/`forceString` all work inside a
  set_qc rule for free, no new condition-editing code needed). The rule
  card deliberately **reuses** `.column-header`/`.column-header-title`/
  `.column-summary`/`.column-header-actions`/`.column-body` CSS classes
  from the column-card UI rather than duplicating near-identical rules —
  only the outer card class (`set-qc-rule-card`) is new, since it needs its
  own grouped-list overlap treatment independent of `.column-card`.
  `refreshPreview()` and both `download`/`copy` handlers updated to call
  `buildConfig(state.columns, state.setQCRules)`.
- `index.html` and `docs/config.md` both gained the same new markup (kept
  in sync, as always): a `.set-qc-section` div inside `.form-pane`, after
  the columns list and `+ Add column` button — a heading, a one-paragraph
  explanation of the "whole run fails" behavior, `#set-qc-rules` (the rule
  cards mount here), and `#add-set-qc-btn`.
- `style.css`: `.set-qc-section` (its own `margin-top` + internal `gap`,
  since it's a `.form-pane` child and `.form-pane` itself is `gap: 0` for
  the column-card overlap trick), `.set-qc-match` (simple flex column for
  the matcher selector + inputs), and `.set-qc-rule-card` with the same
  `-1px`-overlap + squared-corner grouped-list treatment as `.column-card`
  (own comment cross-referencing the original for the "why").
- **Verification**: `node --check` on `app.js`/`schema.js`, a brace/paren
  balance check on all three changed files, and serving the directory
  statically to confirm the new markup (`set-qc-section`, `set-qc-rules`,
  `add-set-qc-btn`) renders in the actual HTML output and every asset
  (`app.js`/`schema.js`/`style.css`) still loads with 200. **The Chrome
  browser-automation tool wasn't available this session** (extension not
  connected), so this could not be interactively click-tested in a real
  browser — only statically verified. `schema.js`'s logic (which this UI
  calls into) remains fully covered by the 30 automated tests from the
  previous section; `app.js` itself has no automated test coverage, same
  as every other UI feature in this tool (no exception introduced here).
  **Recommend you click through it once for real** (add a rule, switch
  matcher kinds, add conditions, collapse/remove) before treating this as
  done — that's the one gap in this session's verification.
- Full suites: **227/227 Python, 30/30 JS** — unaffected, since this
  section only touched UI-layer files with no test coverage of their own.

This completes step 6 and, with it, the full `set_qc` implementation
(schema, transform.py two-pass logic, tests, and the config-builder UI).
Nothing has been committed to git yet.

### Design session 11 — `SetQCRule` restructured: multiple columns per rule

Requested change: a rule should be able to check **several columns** under
one `match`, instead of needing a separate rule (repeating the same
`match`) per column. New shape:

```yaml
set_qc:
  - name: "NTC checks"
    match:
      samples: ["NTC1"]
    columns:
      - column: reads
        qc:
          - {operator: "<=", value: 1000}
      - column: contam_pct
        qc:
          - {operator: "<=", value: 5}
```

- `src/limsport/config.py`: new `SetQCCheck` model (`column`, `qc`).
  `SetQCRule.column`/`.qc` replaced with `columns: list[SetQCCheck]`
  (`min_length=1`), plus a validator rejecting duplicate `column` values
  within one rule's `columns` list (mirrors the existing `_find_duplicates`
  pattern used for column/rule names elsewhere).
- `src/limsport/transform.py`: `_validate_set_qc_columns` and the
  `match_index`-building now iterate `rule.columns` (each check's
  `.column`) instead of a single `rule.column`. The per-row evaluation
  builds **one `ResolvedField` per check** and calls `qc.evaluate_row()`
  **once per rule** across all of them — this is the same reuse insight as
  before, just extended: `evaluate_row` already aggregates failures across
  multiple fields, so "does this rule pass" naturally became "do all of
  this rule's column checks pass," with no new aggregation logic needed.
  A rule fails (and so the whole run fails) if **any one** of its checks
  fails for a matched sample — same AND semantics as multiple conditions on
  one column already had.
- Both Python test files rewritten for the new shape (`tests/
  test_config_set_qc.py`: 21 tests, `tests/test_transform_set_qc.py`: 14
  tests) — including new tests specifically for the multi-column case
  (accepts multiple checks, rejects duplicate columns within one rule, a
  rule with one passing + one failing check still fails the whole rule).
  **Full Python suite: 233/233.**
- `docs/config-builder/schema.js`: `newSetQCRule().columns` is now a list
  of `newSetQCCheck()` (`{column, conditions}`) instead of a flat
  `column`/`conditions`. `buildSetQCRule` builds each check via a new
  `buildSetQCCheck`, validates ≥1 check and rejects duplicate columns
  within the rule. `renderSetQCRule` renders a `columns:` list, each
  `- column: ... / qc: ...` via a new `renderSetQCCheck`.
- `docs/config-builder/app.js`: the rule card's single column/condition
  editor replaced with `buildSetQCChecksEditor(rule)` — a nested,
  addable/removable list of checks. **Deliberately reused the existing
  conditional-qc rule-list CSS/markup wholesale** (`.rules`/`.rule-block`/
  `.rule-header`/`.rule-key` — the same grouped-list-overlap classes the
  `match`/`rules`/`default` editor already uses for its own per-value rule
  list), since the shape ("a labeled key input + remove button, then a
  condition list, in an overlapping-border list") is identical. **No new
  CSS was needed for this part.**
- Verified manually end-to-end against the real `load_config()` (a
  two-check rule, matching the user's exact example shape) before updating
  the JS test suite; `docs/config-builder/tests/schema.test.mjs` rewritten
  for the new shape, plus new tests for the multi-column case, duplicate-
  column rejection, and an empty-`columns`-list rejection. **Full JS suite:
  33/33.**
- Not yet done: `README.md` doesn't document `set_qc` at all yet (it was
  never added — only feature 1's `contains`/`case_insensitive` made it into
  the README this session). Flagging as an optional follow-up, not done
  as part of this restructuring since it wasn't asked for.

Full suites after this change: **233/233 Python, 33/33 JS.** Nothing
committed to git yet.

### Design session 12 — `columns:` becomes omittable + `is_empty`/`is_not_empty`

Two independent changes, both discussed via arguments/wording options first,
then implemented once you picked.

**`columns:` can now be omitted when `set_qc` is configured:**

- Decision (yours, narrower than my default suggestion): `columns:` can be
  **omitted entirely** (meaning "pass every input column through unchanged,
  same as no config at all, but `set_qc` still runs") as long as `set_qc`
  has at least one rule. An **explicit** `columns: []` is rejected
  unconditionally, regardless of `set_qc` — it looks like a mistake, not a
  deliberate choice, so it doesn't get the same pass. A config with neither
  `columns` nor `set_qc` is rejected ("config must configure at least one
  of 'columns' or 'set_qc'").
- `src/limsport/config.py`: `ExportConfig.columns` changed from
  `list[ColumnConfig]` (required) to `list[ColumnConfig] | None = None`.
  The old `@field_validator("columns")` replaced with a
  `@model_validator(mode="after")` (needs both fields to distinguish
  "omitted" from "explicit empty" and to check `set_qc`).
- `src/limsport/transform.py::run_export()`: when `config.columns is None`,
  `output_header = header` and `resolved_columns = []` (mirrors the
  no-config `else` branch), and the buffered per-row value is the **raw
  row**, not resolved fields — the earlier per-row buffering logic had to
  branch on `config.columns is not None` specifically (not just `config is
  not None`) in three places: building `resolved_columns`/`output_header`,
  choosing what to buffer per row, and the final decision phase's "run
  per-row QC or just pass the row through" branch. `set_qc` evaluation
  itself is unaffected — it never depended on `columns:` existing.
- Verified end-to-end manually (a `set_qc`-only config passing every input
  column through, confirmed byte-identical to input) before adding tests.
- Tests added: `tests/test_config.py` (4 new: rejects explicit `columns:
  []` even with `set_qc` present, rejects a fully blank config, allows
  omitted `columns` when `set_qc` exists), `tests/test_transform_set_qc.py`
  (2 new: pass-through end-to-end, and confirming a `set_qc` failure still
  zeroes the whole run even with `columns` omitted).

**New `is_empty`/`is_not_empty` operators:**

- Wording chosen: `is_empty` / `is_not_empty` (your pick from the options
  presented — SQL/pandas-familiar).
- Structural point (not just naming): `qc.py::evaluate_condition` has an
  unconditional "blank cell = automatic failure" guard that runs *before*
  any operator-specific logic, for every existing operator. Since these two
  operators' entire purpose is testing blankness, they had to be checked
  **before** that guard, not after — this is a real carve-out, not a
  cosmetic addition.
- `src/limsport/config.py`: `QCCondition.value` widened from required to
  `int | float | str | bool | None = None`. The validator gained an early
  branch for `IS_EMPTY`/`IS_NOT_EMPTY`: rejects a `value`, rejects
  `case_insensitive`, rejects `tolerance_percent`, then returns immediately
  — every other operator now explicitly checks `value is not None` (this
  used to be enforced implicitly by the field being required; widening it
  to `Optional` meant that guarantee had to become explicit).
- `src/limsport/qc.py::evaluate_condition`: `is_empty`/`is_not_empty`
  checked immediately after computing `raw`, before the `if not raw: return
  False, "missing value"` guard every other operator still hits.
- Verified manually first (`evaluate_condition` on blank/whitespace/content
  cells for both operators) before writing tests.
- Tests added: `tests/test_config.py` (7: accepts no value, rejects a
  value, rejects `case_insensitive`, rejects `tolerance_percent`, plus a
  regression test that an ordinary operator like `>=` still requires a
  value now that the field is Optional), `tests/test_qc.py` (7: pass/fail
  on blank/whitespace/None/content for both operators), and
  `tests/test_transform_set_qc.py` (2: the motivating end-to-end case — an
  NTC's `detected_organism` column passes via `is_empty` when blank, and
  fails the whole run via the same rule when contaminated).
- `docs/config-builder/schema.js`: added to `OPERATORS`; new
  `NO_VALUE_OPERATORS` export. `buildCondition` returns `{operator}` only
  (no value) for these two, ignoring any leftover value/tolerance/
  case-insensitive wizard state. `renderConditionInline` omits the
  `value:` key entirely for these operators (confirmed manually against
  the real `load_config()`, then covered by 2 new JS tests).
- `docs/config-builder/app.js`: the value input itself is now hidden (not
  just tolerance/case-insensitive/force-string) when the operator is
  `is_empty`/`is_not_empty`, with the same "clear stale state on switch"
  treatment as the other hidden-control cases.
- Full suites after both changes: **254/254 Python, 35/35 JS.**

Nothing committed to git yet.

### Design session 4 — README restored, config-builder quoting made unconditional

- `README.md`'s QC operators table and example restored with the *current*
  names (`does_not_contain`, `case_insensitive`) — confirmed this was an
  accidental removal on your end, not intentional.
- `docs/config-builder/schema.js`: `yamlEqualityValue()` (which renders a
  string-operator condition's `value:`) now **always** quotes the value,
  regardless of content. Previously it only quoted when the value had a
  non-alphanumeric character or looked like an ambiguous YAML scalar (e.g.
  `{operator: contains, value: Salmonella}` rendered with a bare, unquoted
  `Salmonella`). The now-unused `ALPHANUMERIC_ONLY_RE` regex was removed.
  Scope: this only touches the QC condition `value:` field for string
  operators (`=`/`contains`/`does_not_contain`) — column names, renames,
  rule keys (already always quoted via `yamlKey`), and other scalar fields
  are unchanged.
- Updated the one test that asserted the old "quote only when necessary"
  behavior (`docs/config-builder/tests/schema.test.mjs`) to assert
  always-quoted instead.
- Verified against the real `load_config()`: quoting is a pure formatting
  choice, doesn't change what's parsed, so this doesn't affect any config
  semantics — just makes the generated YAML visually unambiguous that a
  `value:` is a string, not a bare keyword.
- Full suites re-verified: 198/198 Python, 17/17 JS.

### Design session 5 — `=` gets an explicit "treat as string" toggle

Closes a real gap: a numeric-looking `=` value (e.g. `"1000"`) always got
auto-converted to a bare number with no way to force a quoted string match,
and "ignore case" was shown for `=` even when the value would end up numeric
(where checking it just produced a validation error).

- `docs/config-builder/schema.js`: `newCondition()` gained `forceString:
  false`. `buildCondition`'s `=` branch: `value = cond.forceString ||
  !isNumeric ? raw : Number(raw)` — forcing the raw string through even when
  it looks numeric. No schema.py change needed on the Python side: this is
  purely a builder-side UX decision about *how the builder decides* whether
  a `=` value is numeric or a string: the resulting YAML is either a bare
  number or a quoted string either way, exactly what `load_config()` already
  accepts for `value: int | float | str | bool`.
- `docs/config-builder/app.js`: new "treat as string" checkbox
  (`forceStringInput`/`.cond-force-string`), shown only when operator is `=`.
  "Ignore case" visibility now goes through `caseInsensitiveApplies()`: true
  unconditionally for `contains`/`does_not_contain` (already always string),
  but for `=` only when "treat as string" is checked. Switching away from
  `=`, or unchecking "treat as string", resets both `forceString` and (if no
  longer applicable) `caseInsensitive` — same stale-hidden-state fix pattern
  as the `tolerancePercent`/`caseInsensitive` reset added earlier.
- `docs/config-builder/style.css`: `.cond-force-string` shares the
  `.cond-case-insensitive` checkbox-label styling.
- Tests added confirming: `=` with a numeric-looking value still defaults to
  a number (and still rejects `case_insensitive` in that state); `=` with
  `forceString` forces a string (quoted in the YAML, confirmed a JSON string
  — not a number — after round-tripping through the real `load_config()`)
  and allows `case_insensitive` in that state.
- Full suites re-verified: 198/198 Python, 19/19 JS (2 new).

### Design session 6 — "treat as string" auto-hides once the value is unambiguous

Refinement to session 5: once a `=` value has any non-numeric character
(e.g. "PASS"), it's already unambiguously a string — no need to ask. "Treat
as string" now only shows for a `=` value that's numeric-looking (or blank);
"ignore case" shows directly once the value is an explicit string, without
needing the checkbox at all.

- `docs/config-builder/schema.js`: exported `NUMERIC_RE` (was file-private)
  so `app.js` can reuse the exact same numeric-looking test, rather than
  duplicating the regex.
- `docs/config-builder/app.js`: added `equalityValueIsExplicitString()`
  (true when the current `=` value is non-blank and non-numeric-looking).
  `forceStringApplies()`/`caseInsensitiveApplies()` both consult it now.
  `valueInput`'s `input` listener re-checks `caseInsensitiveApplies()` on
  every keystroke (not just on operator change), resetting `caseInsensitive`
  if a value edit makes it no longer applicable — same stale-hidden-control
  fix pattern as before, just now triggered by value edits too, not only
  operator switches.
  - No `schema.js` change needed beyond the export: `buildCondition`'s `=`
    branch (`cond.forceString || !isNumeric ? raw : Number(raw)`) already
    treated a non-numeric value as a string regardless of `forceString`, so
    a stale `forceString` value left over after the checkbox auto-hides is
    inert, not invalid -- unlike `tolerancePercent`, it never causes a
    validation error, so it doesn't need resetting when hidden this way.
- No new automated tests: this is `app.js` DOM/visibility behavior, and
  `app.js` has no test coverage in this project (only `schema.js`'s pure
  logic is tested via `node --test`) -- consistent with the existing
  convention, not a gap introduced here.
- Verified: `node --check app.js` (syntax) and the full JS suite (still
  19/19, unaffected since it only exercises `schema.js`).

## Resuming tomorrow

Run this in the repo directory (`/home/sage_wright/github/limsport`) to continue this
exact Claude Code session:

```
claude --continue
```

(`-c` also works.) If that doesn't pick up this session for any reason — e.g. a
different session was started in this directory in the meantime — use `claude
--resume` (`-r`) instead, which shows a picker of recent sessions to choose from. This
file is the durable fallback either way: all requirements-gathering answers and the
finalized config shape above are captured, so implementation planning can start fresh
from this doc even without the chat history.

### Design session 13 — config-builder: `columns: []` never rendered, warning until columns or set_qc exists

Mirrors the backend's session-12 change (`columns:` omittable) in the JS
tool's own UI/validation, which hadn't been updated yet:

- `docs/config-builder/schema.js`: `buildConfig`'s "add at least one
  column" error now only fires when **both** `columns` and `setQCRules`
  are empty (message updated to say so). `serializeYAML` no longer renders
  `columns: []` at all when there are zero columns — the key is omitted
  entirely, matching what the real `load_config()` actually accepts
  (explicit `columns: []` is still rejected server-side; only omitting the
  key is allowed).
- `docs/config-builder/app.js`: the download/copy-button `ok` gate updated
  from `state.columns.length > 0` to `state.columns.length > 0 ||
  state.setQCRules.length > 0`, so a valid set_qc-only config (zero
  columns) can actually be downloaded/copied instead of being permanently
  blocked by a stale column-count check.
- Verified manually first: zero columns + zero set_qc → warning shown,
  preview renders as empty string (no stray `columns: []`); zero columns +
  one set_qc rule → no errors, no top-level `columns:` line, `set_qc:`
  present, accepted by the real `load_config()`.
- 4 new tests added. One self-caught bug during test-writing: an early
  version of the "no top-level columns:" assertion used `/columns:/`,
  which also matches a `set_qc` rule's own per-rule `columns:` key (the
  multi-column-per-rule list from session 11) -- fixed to `/^columns:/m`
  so it only checks the top-level key.
- Full suites: **254/254 Python (unaffected), 38/38 JS.**

Nothing committed to git yet.

### Design session 14 — new `examples/set_qc/` scenario, docs for `set_qc`/new operators

A fourth example scenario, following the exact conventions of `basic/`/
`file_parsing/`/`theiaprok_illumina_pe/` (self-contained input/config/output
fixtures + its own `run_examples.sh`). Also backfills the `set_qc`/
`is_empty`/`is_not_empty` documentation in the main `README.md`, which
sessions 11-13 had explicitly deferred.

**Scenario design** (`examples/set_qc/`): 5 samples — three ordinary
samples, a negative control (`NTC1`), a positive control (`PC1`).
`columns:` gives `status` a `case_insensitive: true` `=` check directly;
`read_count` and `detected_organism` are deliberately left with **no**
per-column `qc:`, since a single fixed rule can't express "low for the NTC,
high for real samples" or "blank for the NTC, organism-specific for
everyone else" — those live entirely in three `set_qc` rules instead, one
per match kind:
1. `sample_pattern: "NTC"` → checks **two** columns (`detected_organism`
   via `is_empty`, `read_count` via `<=`) — the multi-column-per-rule case.
2. `sample_regex: "^PC-?\d*$"` → checks `detected_organism` (`contains`,
   case-insensitive) and `notes` (`is_not_empty`) — another two-column rule.
3. `samples: [...]` (explicit list) → checks `detected_organism` via
   `does_not_contain` (case-insensitive), for the three ordinary samples.

**Files**, mirroring `file_parsing/`'s split between "main golden run" and
"scenarios needing their own dataset because they abort the whole run":
- `input.tsv` + `config.yaml` → `output.tsv`/`qc_report.tsv`: the all-pass
  golden scenario (report is header-only).
- `input_ntc_contaminated.tsv` (same config, `NTC1`'s `read_count` raised
  to 5000) → `output_ntc_contaminated.tsv`/`qc_report_ntc_contaminated.tsv`:
  a **second committed golden pair**, deliberately — a `set_qc` failure
  isn't a hard error (exit 0, output *is* written, just header-only), so
  unlike the true hard-error scenarios below, this outcome is a real,
  diffable artifact worth committing, not just a stderr line. Confirmed
  byte-identical across repeated runs before committing.
- `config_zero_match.yaml`, `config_empty_columns.yaml`: true hard errors
  (non-zero exit, no output at all) — a rule matching zero samples, and an
  explicit `columns: []` even with `set_qc` present. Run to `/tmp` only, per
  the existing convention for hard-error scenarios.
- `config_columns_omitted.yaml`: a bonus (not an error) illustrating the
  omitted-`columns:` pass-through behavior, also run to `/tmp` only (not a
  committed golden pair), matching how `basic/` handles its own non-golden
  illustrative commands (nothing-to-do path, delimiter conversion).
- `run_examples.sh`: same `run_ok`/`run_fail` harness as the other three
  scenarios. All 5 commands verified to actually pass, and the two golden
  pairs confirmed byte-stable across a second run before finalizing.

**Docs added to `README.md`** (previously deferred): the `columns:` intro
paragraph now states it's omittable; the QC operators table gained
`is_empty`/`is_not_empty` rows; a new `### set_qc: run-level (whole-run)
QC` section (placed between "Conditional qc" and "file_parsing") covers
the three match kinds, multi-column rules, the whole-run-fail behavior,
and the zero-match hard error, linking to `examples/set_qc/` for the
worked example.

**`examples/README.md`**: table updated ("Three" → "Four" scenarios, new
row), and a full `## set_qc/` section added in the same position/style as
the other three, cross-linking back to the new README.md sections via
anchor links (matching the existing `../README.md#file_parsing-...`
convention already used by the `file_parsing/` section).

No code changes this session — example fixtures and documentation only.
Full suites unaffected: **254/254 Python, 38/38 JS.** Nothing committed to
git yet (`git status`: `README.md` and `examples/README.md` modified,
`examples/set_qc/` new, `planning/` new).

### Design session 15 — consolidated `basic/`, `file_parsing/`, `set_qc/`,
### and `theiaprok_illumina_pe/` into one `examples/full/`

User asked to merge all four example directories into one, built around
`theiaprok_illumina_pe.tsv` (condensed), allowing synthetic NTC/PC-like
rows, while preserving every distinct condition the four separate
directories demonstrated.

**Design**: trimmed the real 491-column/70-sample table down to 15 columns
and 11 samples (9 real + 2 synthetic: `NTC1`, `PC1`). Reused the 9 real
samples already curated in the old `theiaprok_illumina_pe/samples.txt`
(their per-sample failure stories were already known and documented).
Two real cells deliberately edited (disclosed in `config.yaml`'s header
comment and in `examples/README.md`): `461023`'s `est_coverage_clean` →
`"NA"` (non-numeric-cell demo), `SAMN24249320`'s `combined_mean_q_clean` →
whitespace-only (distinct from `03-98DDCS`'s genuinely-blank cell).
`number_contigs`'s qc became a plain two-condition AND range (`>=10,
<=300`) to keep `basic/`'s "plain AND range" demonstration without
disturbing any real sample's existing pass/fail outcome (verified against
real data first: no curated sample sits between 10 and its old floor).

Added 5 new synthetic columns (`sequencing_platform`, `qc_status`,
`screening_notes`, `notes`, `raw_read_count`) rather than reusing
`gambit_predicted_taxon` for the string-operator/set_qc demos, since that
column is already the conditional-qc match key — an advisor review caught
that collision before any fixture was written (a blank match value on a
synthetic row would fail conditional qc for the wrong reason, and
`does_not_contain`/`contains` against a genuinely blank cell hits the
generic "missing value" guard before the operator-specific logic runs, so
every real sample needed non-blank placeholder text in `screening_notes`
even though only 3 of them are actually matched by that rule).

`file_parsing`'s three-different-formats demonstration (JSON, invented
report, real gs://) didn't need to collapse onto one table: kept the real
`quast_report` (gs://, awk) on the main table, and moved the old
`file_parsing/`'s SAMPLE_A-D fixtures + config almost verbatim into a
second small adjunct scenario in the same directory
(`input_multi_format.tsv` + `config_multi_format.yaml`), rather than
forcing JSON/invented-report parsing onto real theiaprok rows where it
wouldn't make biological sense. NTC1/PC1 got local (not `gs://`) crafted
`quast_report`-format files, matching the real format confirmed by
downloading one real sample's actual report via `gcloud storage cp`
first.

Ragged-row and forbidden-bucket error fixtures stayed wholly separate tiny
files (same convention as before) rather than being mixed into the main
table.

**Verification**: `gcloud` was available and authenticated in this
environment, so the real `gs://` file_parsing + conditional qc + every
plain operator + set_qc were all run for real (not just unit-tested)
against actual Google Cloud Storage before being committed as golden
`output.tsv`/`qc_report.tsv`. Every hard-error scenario (9 total) and the
whole-run-fail/columns-omitted bonus scenarios were run individually to
confirm exit codes and messages, then the full consolidated
`run_examples.sh` (17 scenarios) was run twice with `md5sum -c` to confirm
golden output files are byte-stable.

**A dependency the user hadn't had in view**: `docs/config-builder/
examples.js` + `tests/schema.test.mjs` round-trip the config-builder
against `examples/{basic,file_parsing,theiaprok_illumina_pe}/config.yaml`
directly through the real `load_config()` — deleting those directories
would have silently broken 3 JS tests. Rewrote `examples.js`'s three
builder functions into `fullExampleColumns()` + `fullExampleSetQCRules()`
(matching the new `examples/full/config.yaml` exactly, including the
set_qc rules) and `multiFormatExampleColumns()` (matching
`config_multi_format.yaml`), and updated the two round-trip tests
accordingly. Both still pass, proving the config-builder can reproduce
the new merged config byte-for-byte.

Old `examples/basic/`, `examples/file_parsing/`, `examples/
theiaprok_illumina_pe/` were `git rm -r`'d (tracked, clean) only after the
full replacement was built and green; `examples/set_qc/` (never
committed) was plain `rm -rf`'d. `examples/README.md` fully rewritten
around the single `full/` scenario; root `README.md`'s one
`examples/file_parsing/` cross-reference updated to point at `examples/
full/`.

Final state: **254/254 Python, 37/37 JS** (JS count dropped by one net —
three per-directory round-trip tests collapsed into two against the
merged config). Nothing committed to git yet — `examples/full/` new,
three old example directories staged as deleted, `examples/README.md`/
`README.md`/`docs/config-builder/examples.js`/`docs/config-builder/
tests/schema.test.mjs` modified.

**Post-advisor fixes**: an advisor pass caught a real bug before calling
this done — `run_examples.sh`'s delimiter-conversion demo used
`config.yaml` (real `gs://` file_parsing) but sat *outside* the
`HAVE_GCLOUD` guard, so the script would hard-fail (non-zero exit) for
anyone without `gcloud` on `PATH`, unlike every other gcloud-dependent
command which is properly skipped. Moved it inside the guard with a
matching `skip()`. Also caught that the `config_columns_omitted.yaml`
bonus run only proved the config *runs*, not that `set_qc` still gates
with `columns:` omitted (it passed trivially since nothing in
`theiaprok_illumina_pe.tsv` fails when run through that config) — added a
second bonus run against `input_ntc_contaminated.tsv` through the same
config, confirming header-only output. Also fixed an arithmetic error in
`examples/README.md` ("18 columns in the output" — actual header has 17;
15 kept columns − 1 (`quast_report` itself) + 3 (its file_parsing
outputs) = 17) and a miscount in this log ("4 new synthetic columns" when
5 were actually listed). Verified the `HAVE_GCLOUD=0` path for real with
`env PATH=<venv-bin>:/usr/bin:/bin bash examples/full/run_examples.sh`
(gcloud's snap bin excluded, limsport's venv bin kept): 14 ok, 0
unexpected, 4 skipped, exit 0. Re-ran the full script once more with
`gcloud` present afterward and re-confirmed all 6 golden output files are
still byte-identical via `md5sum -c`.

**Also noted, not part of this task**: `README.md` was substantially
rewritten externally (by the user, outside this conversation) partway
through this session, condensing it into a terser style and dropping all
`examples/*` path cross-references — including the one this session had
just updated to point at `examples/full/`. Flagged two apparent issues in
that rewritten version for the user to confirm rather than silently
fixing: (1) the `file_parsing` section's prose says the file path reaches
commands via `"$FILE"`, but the actual env var (per `file_parsing.py` and
every example config) is `$LIMSPORT_FILE`; (2) the To-Do list still shows
`[ ] set level QC - if "NTC" fails fail the entire run` as unchecked,
even though `set_qc` has been fully implemented and shipped for several
sessions now.

### Design session 16 — renamed `$LIMSPORT_FILE` to `$FILE`; reconciled an
### external reorg of `examples/`

User asked to rename the `file_parsing` env var from `$LIMSPORT_FILE` to
`$FILE` (resolving inconsistency (1) noted just above — the rewritten
README's prose already said `$FILE`, code said `$LIMSPORT_FILE`). Flagged
one real tradeoff before doing it: `env = {**os.environ, "FILE":
local_path}` in `file_parsing.py` always wins in the subprocess regardless
of name (dict literal, our key last), so there's no functional collision
risk — the only cost is that `FILE` is a much more generic/less
greppable name than `LIMSPORT_FILE`. User confirmed, proceeded. Renamed
across `src/limsport/file_parsing.py` (the env var itself + its
docstring), `README.md`, 5 test files, `docs/config-builder/{app.js,
examples.js}`, `docs/config-builder/tests/schema.test.mjs`, and every
example config. Left the mention inside this planning log's own dated
entries alone (historical record of what was true at the time).

Mid-rename, the user reorganized `examples/full/` externally (outside
this conversation) into `examples/configs/`, `examples/inputs/`,
`examples/outputs/`, `examples/files/` (split by file type) with
`run_examples.sh` moved to the top-level `examples/` directory, and told
me directly: "i did a reorganization of the examples/ folder, please
update paths accordingly." This broke 2 JS round-trip tests
(`examples/full/config.yaml` no longer existed) and several internal path
references (NTC1/PC1's `quast_report` cell values and the multi-format
adjunct's per-sample file paths in `inputs/theiaprok_illumina_pe.tsv` /
`inputs/input_ntc_contaminated.tsv` / `inputs/input_multi_format.tsv`
still said `examples/full/...`).

Fixed: the 3 TSVs' internal `examples/full/` → `examples/files/`
references; rewrote `examples/run_examples.sh` with three directory
variables (`CONFIGS`/`INPUTS`/`OUTPUTS`) instead of one flat `$DIR`, and
corrected its `REPO_ROOT` calculation (one `..` instead of two, since the
script itself moved up a directory level); updated every command path in
`examples/README.md` via targeted per-filename `sed` substitutions (each
filename maps to exactly one subdirectory, so this was unambiguous);
updated `docs/config-builder/tests/schema.test.mjs`'s two `EXAMPLES`
path-joins and `examples.js`'s docstrings. Rewrote `examples/README.md`'s
title/intro framing (it still said "one self-contained directory, `full/`")
to describe the subdirectory split instead.

**Verification**: full `run_examples.sh` re-run from the new layout — 18
ok, 0 unexpected, 0 skipped, golden `outputs/*.tsv` byte-identical via
`md5sum -c`. Re-verified the `HAVE_GCLOUD=0` skip path still works after
moving the script (14 ok, 4 skipped, exit 0). Full suites: **254/254
Python, 37/37 JS**. Final repo-wide sweep confirmed zero remaining
`LIMSPORT_FILE` or `examples/full` references outside this log's own
historical entries.

### Design session 17 — `--qc-report` always written when `--config` is used

User asked: make `--qc-report` always write a report (resolves the
long-standing `README.md` TODO: "make qc-report default to qc_report.tsv
but the command line option will rename it"). Kept the design minimal:
only `cli.py`'s argparse gained `default=Path("qc_report.tsv")` on
`--qc-report`; `transform.run_export`'s own `qc_report_path: Path | None`
signature and its `if qc_report_path is not None: write` logic were left
completely untouched. This means the "always written" policy is a
CLI-level default, not a transform-level behavior change — the ~57
existing test call sites that call `transform.run_export(...)` directly
and pass `None` (deliberately skipping the report for tests that don't
care about it) needed zero changes, since they bypass argparse's defaults
entirely. Only one test encoded the OLD CLI behavior and needed rewriting:
`test_omitting_qc_report_writes_no_report_file` →
`test_omitting_qc_report_defaults_to_qc_report_tsv_in_the_current_directory`,
mirroring the existing `test_output_defaults_to_limsport_tsv_in_the_current_directory`
pattern (`monkeypatch.chdir(tmp_path)`).

The "no config → no report, even if `--qc-report` is passed" behavior is
unchanged and still correct/tested (no QC ever ran, so there's nothing to
report) — this only changes the case where `--config` *is* used.

**Caught before it caused damage**: `examples/run_examples.sh` had two
`run_ok` commands (delimiter conversion, both columns-omitted bonus runs)
that pass `--config` but never explicitly passed `--qc-report` — under
the new default, these would silently write a bare `qc_report.tsv` into
whatever the script's cwd is, which is the repo root (`cd "$REPO_ROOT"` at
the top of the script). Confirmed this by running the script once before
fixing it: a stray `qc_report.tsv` appeared at the repo root. Fixed by
adding explicit `--qc-report "$TMP/<distinct-name>.tsv"` to all three
commands, then verified a clean re-run leaves the repo root untouched
(`git status` clean of anything but tracked changes).

Also updated: `README.md` (intro line, CLI reference table, "## The QC
report" section, removed the now-done TODO line), `src/limsport/cli.py`'s
help text.

**Verification**: full `run_examples.sh` re-run (18 ok, 0 unexpected, 0
skipped), golden `outputs/*.tsv` byte-identical via `md5sum -c`, repo root
confirmed clean of stray files, full suites **254/254 Python, 37/37 JS**.

Noted but not touched: `src/limsport/config.py` changed externally again
this session (comment/docstring trimming only, no logic change) —
verified via `git diff` before moving on, harmless.

Nothing committed to git yet.
