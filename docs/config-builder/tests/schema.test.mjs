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

import { newColumn, newCondition, newFileParsingOutput, buildConfig, serializeYAML } from "../schema.js";
import {
  condition,
  basicExampleColumns,
  fileParsingExampleColumns,
  theiaprokExampleColumns,
} from "../examples.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..", "..");
const EXAMPLES = path.join(REPO_ROOT, "examples");
const HELPER = path.join(HERE, "load_and_dump.py");

function loadAndDump(yamlPath) {
  const result = spawnSync("python3", [HELPER, yamlPath], {
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: path.join(REPO_ROOT, "src") },
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

test("basic example: every operator, rename, plain pass-through, dropped column", () => {
  const { plain, errors } = buildConfig(basicExampleColumns());
  assert.deepEqual(errors, []);
  assertValidAndMatches(plain, path.join(EXAMPLES, "basic", "config.yaml"), "basic");
});

test("file_parsing example: single-output, multi-output, and a third command style", () => {
  const { plain, errors } = buildConfig(fileParsingExampleColumns());
  assert.deepEqual(errors, []);
  assertValidAndMatches(plain, path.join(EXAMPLES, "file_parsing", "config.yaml"), "file_parsing");
});

test("theiaprok example: renames, conditional qc with no default, multi-output file_parsing with nested conditional qc", () => {
  const { plain, errors } = buildConfig(theiaprokExampleColumns());
  assert.deepEqual(errors, []);
  assertValidAndMatches(
    plain,
    path.join(EXAMPLES, "theiaprok_illumina_pe", "config.yaml"),
    "theiaprok_illumina_pe"
  );
});

// ---------------------------------------------------------------------------
// Validation: client-side errors should line up with real rejections
// ---------------------------------------------------------------------------

test("validation: duplicate column names are rejected", () => {
  const a = newColumn();
  a.name = "sample_id";
  const b = newColumn();
  b.name = "sample_id";
  const { errors } = buildConfig([a, b]);
  assert.ok(errors.some((e) => e.includes("Duplicate column name")));
});

test("validation: duplicate output names (rename vs. plain name) are rejected", () => {
  const a = newColumn();
  a.name = "sample_id";
  const b = newColumn();
  b.name = "other_id";
  b.rename = "sample_id";
  const { errors } = buildConfig([a, b]);
  assert.ok(errors.some((e) => e.includes("Duplicate output column name")));
});

test("validation: duplicate file_parsing output names across two different columns are rejected, naming both sources", () => {
  const a = newColumn();
  a.name = "file_a";
  a.isFileParsing = true;
  const outA = newFileParsingOutput();
  outA.name = "value";
  outA.command = 'cat "$LIMSPORT_FILE"';
  a.fileParsing = [outA];

  const b = newColumn();
  b.name = "file_b";
  b.isFileParsing = true;
  const outB = newFileParsingOutput();
  outB.name = "value";
  outB.command = 'cat "$LIMSPORT_FILE"';
  b.fileParsing = [outB];

  const { errors } = buildConfig([a, b]);
  const dupError = errors.find((e) => e.includes('Duplicate output column name "value"'));
  assert.ok(dupError, `expected a duplicate-output error, got: ${JSON.stringify(errors)}`);
  assert.match(dupError, /file_a > output "value"/);
  assert.match(dupError, /file_b > output "value"/);
});

test("validation: non-EQ operator with a non-numeric value is rejected", () => {
  const col = newColumn();
  col.name = "status";
  col.qc = { kind: "list", conditions: [condition(">=", "PASS")] };
  const { errors } = buildConfig([col]);
  assert.ok(errors.some((e) => e.includes("requires a numeric value")));
});

test("validation: '~=' without tolerance_percent is rejected", () => {
  const col = newColumn();
  col.name = "length";
  const cond = newCondition();
  cond.operator = "~=";
  cond.value = "1000000";
  col.qc = { kind: "list", conditions: [cond] };
  const { errors } = buildConfig([col]);
  assert.ok(errors.some((e) => e.includes('tolerance_percent is required for operator "~="')));
});

test("validation: tolerance_percent on a non-'~=' operator is rejected", () => {
  const col = newColumn();
  col.name = "length";
  col.qc = { kind: "list", conditions: [condition(">=", 1000, 5)] };
  const { errors } = buildConfig([col]);
  assert.ok(errors.some((e) => e.includes("tolerance_percent is only valid with operator")));
});

test("validation: a column that is both renamed and file_parsing-enabled cannot happen via the wizard's own state shape (mutual exclusivity is structural)", () => {
  // The wizard toggles `isFileParsing` and only renders/collects rename+qc
  // OR file_parsing, never both -- buildConfig() reflects that by simply
  // never emitting `rename`/`qc` alongside `file_parsing` regardless of
  // leftover state, matching config.py's model_validator.
  const col = newColumn();
  col.name = "metadata_json";
  col.rename = "should_be_ignored";
  col.isFileParsing = true;
  const output = newFileParsingOutput();
  output.name = "value";
  output.command = "cat \"$LIMSPORT_FILE\"";
  col.fileParsing = [output];
  const { plain, errors } = buildConfig([col]);
  assert.deepEqual(errors, []);
  assert.equal(plain.columns[0].rename, undefined);
  assert.equal(plain.columns[0].qc, undefined);
});

test("validation: an empty column name is rejected", () => {
  const col = newColumn();
  const { errors } = buildConfig([col]);
  assert.ok(errors.some((e) => e.includes("name is required")));
});

test("validation: a genuinely invalid config (duplicate names) is also rejected by the real load_config()", () => {
  const a = newColumn();
  a.name = "sample_id";
  const b = newColumn();
  b.name = "sample_id";
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
  col.name = "organism";
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
  col.name = "organism";
  col.qc = { kind: "list", conditions: [condition("contains", "Escherichia")] };
  const { errors } = buildConfig([col]);
  assert.deepEqual(errors, []);
});

test("validation: case_insensitive: true round-trips through the real load_config(), and is omitted by default", () => {
  const col = newColumn();
  col.name = "organism";
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
  defaultCol.name = "status";
  defaultCol.qc = { kind: "list", conditions: [condition("=", "PASS")] };
  const { plain: defaultPlain, errors: defaultErrors } = buildConfig([defaultCol]);
  assert.deepEqual(defaultErrors, []);
  assert.doesNotMatch(serializeYAML(defaultPlain), /case_insensitive/);
});

test("validation: case_insensitive: true on a numeric-valued condition is rejected", () => {
  const col = newColumn();
  col.name = "read_count";
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
  col.name = "status";
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
  col.name = "status";
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
  col.name = "status";
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
