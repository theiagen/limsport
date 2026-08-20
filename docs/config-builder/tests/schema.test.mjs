// Node's built-in test runner -- no npm dependencies needed. Run with:
//   node --test config-builder/tests/
//
// The strongest possible check here isn't comparing against a second,
// hand-written copy of the schema -- it's feeding the generator's YAML
// through the real `limsport.config.load_config()` (the actual Pydantic
// model this whole tool exists to satisfy) and comparing its model_dump()
// against the same call made on the checked-in example fixtures.

import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  newColumn,
  newCondition,
  newFileParsingOutput,
  newSetQCRule,
  newSetQCCheck,
  buildConfig,
  serializeYAML,
} from "../schema.js";
import {
  condition,
  fullExampleColumns,
  fullExampleSetQCRules,
  multiFormatExampleColumns,
} from "../examples.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..", "..");
const EXAMPLES = path.join(REPO_ROOT, "examples");
const HELPER = path.join(HERE, "load_and_dump.py");

function loadAndDump(yamlPath) {
  const result = spawnSync("python3", [HELPER, yamlPath], {
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: REPO_ROOT },
  });
  return result;
}

function assertValidAndMatches(plainConfig, examplePath, testName) {
  const yaml = serializeYAML(plainConfig);
  const dir = mkdtempSync(path.join(tmpdir(), "limsport-config-builder-"));
  const generatedPath = path.join(dir, "generated.yaml");
  writeFileSync(generatedPath, yaml);

  const generated = loadAndDump(generatedPath);
  assert.equal(
    generated.status,
    0,
    `${testName}: generated YAML should be accepted by load_config()\n${generated.stderr}\n--- generated YAML ---\n${yaml}`
  );

  const reference = loadAndDump(examplePath);
  assert.equal(reference.status, 0, `${testName}: reference fixture should itself be valid\n${reference.stderr}`);

  assert.deepEqual(
    JSON.parse(generated.stdout),
    JSON.parse(reference.stdout),
    `${testName}: generated config should be semantically identical to ${examplePath}\n--- generated YAML ---\n${yaml}`
  );
}

test("full example: every operator, renames, conditional qc with no default, real file_parsing, and set_qc", () => {
  const { plain, errors } = buildConfig(fullExampleColumns(), fullExampleSetQCRules());
  assert.deepEqual(errors, []);
  assertValidAndMatches(plain, path.join(EXAMPLES, "configs", "config.yaml"), "full");
});

test("full example multi-format adjunct: single-output, multi-output, and a third command style", () => {
  const { plain, errors } = buildConfig(multiFormatExampleColumns());
  assert.deepEqual(errors, []);
  assertValidAndMatches(plain, path.join(EXAMPLES, "configs", "config_multi_format.yaml"), "configs/config_multi_format");
});

// ---------------------------------------------------------------------------
// Validation: client-side errors should line up with real rejections
// ---------------------------------------------------------------------------

test("validation: duplicate column names are rejected", () => {
  const a = newColumn();
  a.inputColumn = "sample_id";
  const b = newColumn();
  b.inputColumn = "sample_id";
  const { errors } = buildConfig([a, b]);
  assert.ok(errors.some((e) => e.includes("Duplicate column name")));
});

test("validation: duplicate output names (rename vs. plain name) are rejected", () => {
  const a = newColumn();
  a.inputColumn = "sample_id";
  const b = newColumn();
  b.inputColumn = "other_id";
  b.outputColumn = "sample_id";
  const { errors } = buildConfig([a, b]);
  assert.ok(errors.some((e) => e.includes("Duplicate output column name")));
});

test("validation: duplicate file_parsing output names across two different columns are rejected, naming both sources", () => {
  const a = newColumn();
  a.inputColumn = "file_a";
  a.isFileParsing = true;
  const outA = newFileParsingOutput();
  outA.outputColumn = "value";
  outA.command = 'cat "$FILE"';
  a.fileParsing = [outA];

  const b = newColumn();
  b.inputColumn = "file_b";
  b.isFileParsing = true;
  const outB = newFileParsingOutput();
  outB.outputColumn = "value";
  outB.command = 'cat "$FILE"';
  b.fileParsing = [outB];

  const { errors } = buildConfig([a, b]);
  const dupError = errors.find((e) => e.includes('Duplicate output column name "value"'));
  assert.ok(dupError, `expected a duplicate-output error, got: ${JSON.stringify(errors)}`);
  assert.match(dupError, /file_a > output "value"/);
  assert.match(dupError, /file_b > output "value"/);
});

test("validation: non-EQ operator with a non-numeric value is rejected", () => {
  const col = newColumn();
  col.inputColumn = "status";
  col.qc = { kind: "list", conditions: [condition(">=", "PASS")] };
  const { errors } = buildConfig([col]);
  assert.ok(errors.some((e) => e.includes("requires a numeric value")));
});

test("validation: '~=' without tolerance_percent is rejected", () => {
  const col = newColumn();
  col.inputColumn = "length";
  const cond = newCondition();
  cond.operator = "~=";
  cond.value = "1000000";
  col.qc = { kind: "list", conditions: [cond] };
  const { errors } = buildConfig([col]);
  assert.ok(errors.some((e) => e.includes('tolerance_percent is required for operator "~="')));
});

test("validation: tolerance_percent on a non-'~=' operator is rejected", () => {
  const col = newColumn();
  col.inputColumn = "length";
  col.qc = { kind: "list", conditions: [condition(">=", 1000, 5)] };
  const { errors } = buildConfig([col]);
  assert.ok(errors.some((e) => e.includes("tolerance_percent is only valid with operator")));
});

test("validation: a column that is both renamed and file_parsing-enabled cannot happen via the wizard's own state shape (mutual exclusivity is structural)", () => {
  // The wizard toggles `isFileParsing` and only renders/collects its
  // rename+qc state OR file_parsing, never both -- buildConfig() reflects
  // that by simply never emitting `output_column`/`qc` alongside
  // `file_parsing` regardless of leftover state, matching config.py's
  // model_validator.
  const col = newColumn();
  col.inputColumn = "metadata_json";
  col.outputColumn = "should_be_ignored";
  col.isFileParsing = true;
  const output = newFileParsingOutput();
  output.outputColumn = "value";
  output.command = "cat \"$FILE\"";
  col.fileParsing = [output];
  const { plain, errors } = buildConfig([col]);
  assert.deepEqual(errors, []);
  assert.equal(plain.columns[0].output_column, undefined);
  assert.equal(plain.columns[0].qc, undefined);
});

test("validation: an empty column name is rejected", () => {
  const col = newColumn();
  const { errors } = buildConfig([col]);
  assert.ok(errors.some((e) => e.includes("name is required")));
});

test("validation: a genuinely invalid config (duplicate names) is also rejected by the real load_config()", () => {
  const a = newColumn();
  a.inputColumn = "sample_id";
  const b = newColumn();
  b.inputColumn = "sample_id";
  // Bypass client-side validation on purpose to prove load_config() itself
  // would catch this if the client-side check were ever removed/buggy.
  const { plain } = buildConfig([a, b]);
  const yaml = serializeYAML(plain);
  const dir = mkdtempSync(path.join(tmpdir(), "limsport-config-builder-"));
  const p = path.join(dir, "bad.yaml");
  writeFileSync(p, yaml);
  const result = loadAndDump(p);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /Duplicate column name/);
});

test("validation: 'contains'/'does_not_contain' with a non-numeric value round - trip through the real load_config()", () => {
  const col = newColumn();
  col.inputColumn = "organism";
  col.qc = {
    kind: "list",
    conditions: [condition("contains", "Escherichia"), condition("does_not_contain", "contaminant")],
  };
  const { plain, errors } = buildConfig([col]);
  assert.deepEqual(errors, []);
  const yaml = serializeYAML(plain);

  const dir = mkdtempSync(path.join(tmpdir(), "limsport-config-builder-"));
  const p = path.join(dir, "contains.yaml");
  writeFileSync(p, yaml);
  const result = loadAndDump(p);
  assert.equal(result.status, 0, `${result.stderr}\n--- generated YAML ---\n${yaml}`);
  const dumped = JSON.parse(result.stdout);
  assert.deepEqual(
    dumped.columns[0].qc.map((c) => [c.operator, c.value]),
    [
      ["contains", "Escherichia"],
      ["does_not_contain", "contaminant"],
    ]
  );
});

test("validation: 'contains' does not require a numeric value (unlike other non-EQ operators)", () => {
  const col = newColumn();
  col.inputColumn = "organism";
  col.qc = { kind: "list", conditions: [condition("contains", "Escherichia")] };
  const { errors } = buildConfig([col]);
  assert.deepEqual(errors, []);
});

test("validation: 'is_empty'/'is_not_empty' take no value, and round-trip through the real load_config()", () => {
  const col = newColumn();
  col.inputColumn = "detected_organism";
  const isEmpty = newCondition();
  isEmpty.operator = "is_empty";
  const col2 = newColumn();
  col2.inputColumn = "notes";
  const isNotEmpty = newCondition();
  isNotEmpty.operator = "is_not_empty";
  col.qc = { kind: "list", conditions: [isEmpty] };
  col2.qc = { kind: "list", conditions: [isNotEmpty] };

  const { plain, errors } = buildConfig([col, col2]);
  assert.deepEqual(errors, []);
  const yaml = serializeYAML(plain);
  assert.match(yaml, /\{operator: is_empty\}/);
  assert.match(yaml, /\{operator: is_not_empty\}/);
  assert.doesNotMatch(yaml, /is_empty.*value/);

  const dir = mkdtempSync(path.join(tmpdir(), "limsport-config-builder-"));
  const p = path.join(dir, "is-empty.yaml");
  writeFileSync(p, yaml);
  const result = loadAndDump(p);
  assert.equal(result.status, 0, `${result.stderr}\n--- generated YAML ---\n${yaml}`);
  const dumped = JSON.parse(result.stdout);
  assert.equal(dumped.columns[0].qc[0].operator, "is_empty");
  assert.equal(dumped.columns[0].qc[0].value, null);
  assert.equal(dumped.columns[1].qc[0].operator, "is_not_empty");
});

test("validation: a leftover value on 'is_empty' is dropped entirely, not rejected or passed through", () => {
  const col = newColumn();
  col.inputColumn = "detected_organism";
  const cond = newCondition();
  cond.operator = "is_empty";
  cond.value = "Escherichia"; // leftover state from a previous operator
  col.qc = { kind: "list", conditions: [cond] };

  const { plain, errors } = buildConfig([col]);
  assert.deepEqual(errors, []);
  // buildCondition() drops any value for a NO_VALUE_OPERATORS operator
  // entirely -- confirm the generated YAML has no value at all, so there's
  // nothing left for load_config() to reject in the first place.
  const yaml = serializeYAML(plain);
  assert.doesNotMatch(yaml, /Escherichia/);

  const dir = mkdtempSync(path.join(tmpdir(), "limsport-config-builder-"));
  const p = path.join(dir, "is-empty-with-value.yaml");
  writeFileSync(p, yaml);
  const result = loadAndDump(p);
  assert.equal(result.status, 0, result.stderr);
});

test("validation: case_insensitive: true round-trips through the real load_config(), and is omitted by default", () => {
  const col = newColumn();
  col.inputColumn = "organism";
  const caseInsensitive = newCondition();
  caseInsensitive.operator = "contains";
  caseInsensitive.value = "Escherichia";
  caseInsensitive.caseInsensitive = true;
  col.qc = { kind: "list", conditions: [caseInsensitive] };

  const { plain, errors } = buildConfig([col]);
  assert.deepEqual(errors, []);
  const yaml = serializeYAML(plain);
  assert.match(yaml, /case_insensitive: true/);

  const dir = mkdtempSync(path.join(tmpdir(), "limsport-config-builder-"));
  const p = path.join(dir, "case-insensitive.yaml");
  writeFileSync(p, yaml);
  const result = loadAndDump(p);
  assert.equal(result.status, 0, `${result.stderr}\n--- generated YAML ---\n${yaml}`);
  const dumped = JSON.parse(result.stdout);
  assert.equal(dumped.columns[0].qc[0].case_insensitive, true);

  // default (case_insensitive left false) shouldn't appear in the emitted YAML at all
  const defaultCol = newColumn();
  defaultCol.inputColumn = "status";
  defaultCol.qc = { kind: "list", conditions: [condition("=", "PASS")] };
  const { plain: defaultPlain, errors: defaultErrors } = buildConfig([defaultCol]);
  assert.deepEqual(defaultErrors, []);
  assert.doesNotMatch(serializeYAML(defaultPlain), /case_insensitive/);
});

test("validation: case_insensitive: true on a numeric-valued condition is rejected", () => {
  const col = newColumn();
  col.inputColumn = "read_count";
  const cond = newCondition();
  cond.operator = ">=";
  cond.value = "1000";
  cond.caseInsensitive = true;
  col.qc = { kind: "list", conditions: [cond] };
  const { errors } = buildConfig([col]);
  assert.ok(errors.some((e) => e.includes("case_insensitive: true is only valid when value is a string")));
});

test("validation: '=' with a numeric-looking value defaults to a number, even with case_insensitive requested", () => {
  const col = newColumn();
  col.inputColumn = "status";
  const cond = newCondition();
  cond.operator = "=";
  cond.value = "1000";
  cond.caseInsensitive = true;
  col.qc = { kind: "list", conditions: [cond] };
  const { errors } = buildConfig([col]);
  assert.ok(errors.some((e) => e.includes("case_insensitive: true is only valid when value is a string")));
});

test("validation: '=' with forceString treats a numeric-looking value as a string, and allows case_insensitive", () => {
  const col = newColumn();
  col.inputColumn = "status";
  const cond = newCondition();
  cond.operator = "=";
  cond.value = "1000";
  cond.forceString = true;
  cond.caseInsensitive = true;
  col.qc = { kind: "list", conditions: [cond] };

  const { plain, errors } = buildConfig([col]);
  assert.deepEqual(errors, []);
  const yaml = serializeYAML(plain);
  assert.match(yaml, /value: "1000"/);
  assert.match(yaml, /case_insensitive: true/);

  const dir = mkdtempSync(path.join(tmpdir(), "limsport-config-builder-"));
  const p = path.join(dir, "force-string.yaml");
  writeFileSync(p, yaml);
  const result = loadAndDump(p);
  assert.equal(result.status, 0, `${result.stderr}\n--- generated YAML ---\n${yaml}`);
  const dumped = JSON.parse(result.stdout);
  assert.equal(dumped.columns[0].qc[0].value, "1000");
  assert.equal(typeof dumped.columns[0].qc[0].value, "string");
  assert.equal(dumped.columns[0].qc[0].case_insensitive, true);
});

test("serialization: string-operator values are always quoted, even when plain alphanumeric, and load_config() preserves them exactly", () => {
  const col = newColumn();
  col.inputColumn = "status";
  col.qc = {
    kind: "list",
    conditions: [condition("=", "PASS"), condition("=", "value1"), condition("=", "high-risk"), condition("=", "N/A")],
  };

  const { plain, errors } = buildConfig([col]);
  assert.deepEqual(errors, []);
  const yaml = serializeYAML(plain);

  // Every string-operator value is quoted, plain alphanumeric or not --
  // makes it visually unambiguous that this is a string comparison, not a
  // bare keyword.
  assert.match(yaml, /value: "PASS"/);
  assert.match(yaml, /value: "value1"/);
  assert.match(yaml, /value: "high-risk"/);
  assert.match(yaml, /value: "N\/A"/);

  const dir = mkdtempSync(path.join(tmpdir(), "limsport-config-builder-"));
  const p = path.join(dir, "eq-quoting.yaml");
  writeFileSync(p, yaml);
  const result = loadAndDump(p);
  assert.equal(result.status, 0, result.stderr);
  const dumped = JSON.parse(result.stdout);
  const values = dumped.columns[0].qc.map((c) => c.value);
  assert.deepEqual(values, ["PASS", "value1", "high-risk", "N/A"]);
});

// ---------------------------------------------------------------------------
// set_qc
// ---------------------------------------------------------------------------

function assertValidSetQC(columns, setQCRules, testName) {
  const { plain, errors } = buildConfig(columns, setQCRules);
  assert.deepEqual(errors, [], `${testName}: ${JSON.stringify(errors)}`);
  const yaml = serializeYAML(plain);

  const dir = mkdtempSync(path.join(tmpdir(), "limsport-config-builder-"));
  const p = path.join(dir, "set-qc.yaml");
  writeFileSync(p, yaml);
  const result = loadAndDump(p);
  assert.equal(result.status, 0, `${testName}: ${result.stderr}\n--- generated YAML ---\n${yaml}`);
  return { plain, yaml, dumped: JSON.parse(result.stdout) };
}

test("set_qc: omitted entirely from the YAML when no rules are configured", () => {
  const col = newColumn();
  col.inputColumn = "sample_id";
  const { plain, errors } = buildConfig([col], []);
  assert.deepEqual(errors, []);
  assert.equal(plain.set_qc, undefined);
  assert.doesNotMatch(serializeYAML(plain), /set_qc/);
});

test("validation: neither columns nor set_qc rules configured is rejected, with no columns: line in the preview", () => {
  const { plain, errors } = buildConfig([], []);
  assert.ok(errors.some((e) => e.includes("Add at least one column or set-level QC rule")));
  assert.equal(serializeYAML(plain).trim(), "");
});

test("columns: [] is never rendered -- omitted when there are no columns, even with set_qc configured", () => {
  const rule = newSetQCRule();
  rule.ruleName = "NTC read count";
  rule.matchSamples.kind = "pattern";
  rule.matchSamples.samplePattern = "NTC";
  setCheck(rule, { column: "reads", operator: "<=", value: "1000" });

  const { plain, errors } = buildConfig([], [rule]);
  assert.deepEqual(errors, []);
  const yaml = serializeYAML(plain);
  // /^columns:/m, not /columns:/ -- only the top-level config `columns:` key
  // should be absent.
  assert.doesNotMatch(yaml, /^columns:/m);
  assert.match(yaml, /^set_qc:/m);

  const dir = mkdtempSync(path.join(tmpdir(), "limsport-config-builder-"));
  const p = path.join(dir, "columns-omitted.yaml");
  writeFileSync(p, yaml);
  const result = loadAndDump(p);
  assert.equal(result.status, 0, `${result.stderr}\n--- generated YAML ---\n${yaml}`);
  const dumped = JSON.parse(result.stdout);
  assert.equal(dumped.columns, null);
});

test("validation: adding a set_qc rule with zero columns clears the \"add at least one\" warning", () => {
  const rule = newSetQCRule();
  rule.ruleName = "NTC read count";
  rule.matchSamples.kind = "pattern";
  rule.matchSamples.samplePattern = "NTC";
  setCheck(rule, { column: "reads", operator: "<=", value: "1000" });
  const { errors } = buildConfig([], [rule]);
  assert.ok(!errors.some((e) => e.includes("Add at least one column or set-level QC rule")));
});

// Builds a set_qc rule's single {input_column, qc} check, matching the shape
// newSetQCRule() produces by default (one entry in `checks`).
function setCheck(rule, { column, operator, value }) {
  rule.checks[0].inputColumn = column;
  const cond = newCondition();
  cond.operator = operator;
  cond.value = value;
  rule.checks[0].conditions = [cond];
}

test("set_qc: sample_pattern matcher round-trips through the real load_config()", () => {
  const col = newColumn();
  col.inputColumn = "sample_id";
  const col2 = newColumn();
  col2.inputColumn = "reads";

  const rule = newSetQCRule();
  rule.ruleName = "NTC read count";
  rule.matchSamples.kind = "pattern";
  rule.matchSamples.samplePattern = "NTC";
  setCheck(rule, { column: "reads", operator: "<=", value: "1000" });

  const { dumped } = assertValidSetQC([col, col2], [rule], "sample_pattern");
  assert.equal(dumped.set_qc[0].rule_name, "NTC read count");
  assert.equal(dumped.set_qc[0].checks[0].input_column, "reads");
  assert.deepEqual(dumped.set_qc[0].match_samples, { sample_pattern: "NTC", sample_regex: null, samples: null });
  assert.equal(dumped.set_qc[0].checks[0].qc[0].value, 1000);
});

test("set_qc: sample_regex matcher round-trips through the real load_config()", () => {
  const col = newColumn();
  col.inputColumn = "sample_id";
  const col2 = newColumn();
  col2.inputColumn = "reads";

  const rule = newSetQCRule();
  rule.ruleName = "NTC read count";
  rule.matchSamples.kind = "regex";
  rule.matchSamples.sampleRegex = "^NTC-?\\d*$";
  setCheck(rule, { column: "reads", operator: "<=", value: "1000" });

  const { dumped } = assertValidSetQC([col, col2], [rule], "sample_regex");
  assert.equal(dumped.set_qc[0].match_samples.sample_regex, "^NTC-?\\d*$");
});

test("set_qc: samples matcher accepts a comma-separated list and round-trips through the real load_config()", () => {
  const col = newColumn();
  col.inputColumn = "sample_id";
  const col2 = newColumn();
  col2.inputColumn = "reads";

  const rule = newSetQCRule();
  rule.ruleName = "NTC read count";
  rule.matchSamples.kind = "samples";
  rule.matchSamples.samples = "NTC1, NTC2,  NTC3 ";
  setCheck(rule, { column: "reads", operator: "<=", value: "1000" });

  const { dumped } = assertValidSetQC([col, col2], [rule], "samples");
  assert.deepEqual(dumped.set_qc[0].match_samples.samples, ["NTC1", "NTC2", "NTC3"]);
});

test("set_qc: a check's qc can use contains/does_not_contain, same as column qc", () => {
  const col = newColumn();
  col.inputColumn = "sample_id";
  const col2 = newColumn();
  col2.inputColumn = "organism";

  const rule = newSetQCRule();
  rule.ruleName = "positive control organism";
  rule.matchSamples.kind = "samples";
  rule.matchSamples.samples = "PC1";
  setCheck(rule, { column: "organism", operator: "contains", value: "Escherichia" });

  const { dumped } = assertValidSetQC([col, col2], [rule], "contains in set_qc");
  assert.equal(dumped.set_qc[0].checks[0].qc[0].operator, "contains");
  assert.equal(dumped.set_qc[0].checks[0].qc[0].value, "Escherichia");
});

test("set_qc: a rule can check multiple columns under one match, all read from the same matched sample(s)", () => {
  const col = newColumn();
  col.inputColumn = "sample_id";
  const col2 = newColumn();
  col2.inputColumn = "reads";
  const col3 = newColumn();
  col3.inputColumn = "contam_pct";

  const rule = newSetQCRule();
  rule.ruleName = "NTC checks";
  rule.matchSamples.kind = "pattern";
  rule.matchSamples.samplePattern = "NTC";
  setCheck(rule, { column: "reads", operator: "<=", value: "1000" });
  const check2 = newSetQCCheck();
  check2.inputColumn = "contam_pct";
  const cond2 = newCondition();
  cond2.operator = "<=";
  cond2.value = "5";
  check2.conditions = [cond2];
  rule.checks.push(check2);

  const { dumped } = assertValidSetQC([col, col2, col3], [rule], "multi-column rule");
  assert.deepEqual(
    dumped.set_qc[0].checks.map((c) => c.input_column),
    ["reads", "contam_pct"]
  );
  assert.equal(dumped.set_qc[0].checks[1].qc[0].value, 5);
});

test("set_qc: rule name and match strings are always quoted in the generated YAML", () => {
  const col = newColumn();
  col.inputColumn = "sample_id";
  const col2 = newColumn();
  col2.inputColumn = "reads";

  const rule = newSetQCRule();
  rule.ruleName = "NTC read count"; // contains a space
  rule.matchSamples.kind = "pattern";
  rule.matchSamples.samplePattern = "NTC"; // plain alphanumeric, still quoted
  setCheck(rule, { column: "reads", operator: "<=", value: "1000" });

  const { yaml } = assertValidSetQC([col, col2], [rule], "quoting");
  assert.match(yaml, /- rule_name: "NTC read count"/);
  assert.match(yaml, /sample_pattern: "NTC"/);
});

test("validation: set_qc rule missing a matcher is rejected", () => {
  const rule = newSetQCRule();
  rule.ruleName = "x";
  rule.matchSamples.kind = "pattern";
  rule.matchSamples.samplePattern = ""; // blank
  setCheck(rule, { column: "reads", operator: "<=", value: "1000" });
  const { errors } = buildConfig([newColumn()], [rule]);
  assert.ok(errors.some((e) => e.includes("sample_pattern is required")));
});

test("validation: set_qc rule with a blank samples list is rejected", () => {
  const rule = newSetQCRule();
  rule.ruleName = "x";
  rule.matchSamples.kind = "samples";
  rule.matchSamples.samples = "  ,  ,";
  setCheck(rule, { column: "reads", operator: "<=", value: "1000" });
  const { errors } = buildConfig([newColumn()], [rule]);
  assert.ok(errors.some((e) => e.includes("at least one sample name is required")));
});

test("validation: set_qc rule requires a name, at least one column check, and at least one condition per check", () => {
  const rule = newSetQCRule();
  rule.matchSamples.samplePattern = "NTC";
  rule.checks[0].conditions = [];
  const { errors } = buildConfig([newColumn()], [rule]);
  assert.ok(errors.some((e) => e.includes("name is required")));
  assert.ok(errors.some((e) => e.includes("column is required")));
  assert.ok(errors.some((e) => e.includes("needs at least one condition")));
});

test("validation: an empty columns list on a set_qc rule is rejected", () => {
  const rule = newSetQCRule();
  rule.ruleName = "x";
  rule.matchSamples.samplePattern = "NTC";
  rule.checks = [];
  const { errors } = buildConfig([newColumn()], [rule]);
  assert.ok(errors.some((e) => e.includes("needs at least one column to check")));
});

test("validation: duplicate columns within one set_qc rule are rejected", () => {
  const rule = newSetQCRule();
  rule.ruleName = "x";
  rule.matchSamples.samplePattern = "NTC";
  setCheck(rule, { column: "reads", operator: "<=", value: "1000" });
  const check2 = newSetQCCheck();
  check2.inputColumn = "reads";
  const cond2 = newCondition();
  cond2.operator = ">=";
  cond2.value = "0";
  check2.conditions = [cond2];
  rule.checks.push(check2);

  const { errors } = buildConfig([newColumn()], [rule]);
  assert.ok(errors.some((e) => e.includes("duplicate column(s) within this rule: reads")));
});

test("validation: duplicate set_qc rule names are rejected", () => {
  const rule1 = newSetQCRule();
  rule1.ruleName = "dup";
  rule1.matchSamples.samplePattern = "NTC";
  setCheck(rule1, { column: "reads", operator: "<=", value: "1000" });
  const rule2 = newSetQCRule();
  rule2.ruleName = "dup";
  rule2.matchSamples.samplePattern = "NTC";
  setCheck(rule2, { column: "contam_pct", operator: "<=", value: "0" });
  const { errors } = buildConfig([newColumn()], [rule1, rule2]);
  assert.ok(errors.some((e) => e.includes('Duplicate set_qc rule name(s): dup')));
});

test("validation: an invalid sample_regex is still caught by the real load_config(), even though the client doesn't check regex syntax", () => {
  const col = newColumn();
  col.inputColumn = "sample_id";
  const rule = newSetQCRule();
  rule.ruleName = "x";
  rule.matchSamples.kind = "regex";
  rule.matchSamples.sampleRegex = "(unclosed";
  setCheck(rule, { column: "sample_id", operator: ">=", value: "1" });

  const { plain, errors } = buildConfig([col], [rule]);
  assert.deepEqual(errors, []); // client-side accepts it -- no JS regex validation
  const yaml = serializeYAML(plain);

  const dir = mkdtempSync(path.join(tmpdir(), "limsport-config-builder-"));
  const p = path.join(dir, "bad-regex.yaml");
  writeFileSync(p, yaml);
  const result = loadAndDump(p);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /invalid sample_regex/);
});
