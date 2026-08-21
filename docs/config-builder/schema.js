/**
 * Pure logic for the LIMSport config builder: turns the wizard's in-memory
 * state into the same shape `limsport.config.ExportConfig` expects, validates
 * it the way that Pydantic model does, and serializes it to YAML.
 */

export const OPERATORS = [
  ">",
  ">=",
  "=",
  "<=",
  "<",
  "~=",
  "contains",
  "does_not_contain",
  "is_empty",
  "is_not_empty",
];

// Operators whose value is a string comparison, not a numeric one -- these
// are the only operators `case_insensitive` applies to.
export const STRING_OPERATORS = new Set(["=", "contains", "does_not_contain"]);

// Operators that take no `value` at all -- they test the cell itself
// (blank or not), not a comparison target.
export const NO_VALUE_OPERATORS = new Set(["is_empty", "is_not_empty"]);

let idCounter = 0;
function nextId() {
  idCounter += 1;
  return `id${idCounter}`;
}

export function newCondition() {
  // forceString only applies to "=": it overrides the numeric-looking-value
  // auto-detection below, so e.g. value "1000" can be forced to the string
  // "1000" instead of the number 1000.
  return {
    id: nextId(),
    operator: ">=",
    value: "",
    tolerancePercent: "",
    caseInsensitive: false,
    forceString: false,
  };
}

export function newCase() {
  return { id: nextId(), key: "", conditions: [newCondition()] };
}

export function newQC() {
  // kind: "none" | "list" | "conditional"
  return { kind: "none", conditions: [], matchColumn: "", cases: [], useDefault: false, default: [] };
}

export function newFileParsingOutput() {
  return {
    id: nextId(),
    outputColumn: "",
    command: "",
    timeoutSeconds: "",
    qc: newQC(),
  };
}

export function newColumn() {
  return {
    id: nextId(),
    inputColumn: "",
    isFileParsing: false,
    outputColumn: "",
    output: true,
    qc: newQC(),
    fileParsing: [newFileParsingOutput()],
  };
}

export function newSetQCMatch() {
  // kind: "pattern" | "regex" | "samples" -- exactly one of these is ever
  // built into the config; the others are just unused wizard state.
  return { kind: "pattern", samplePattern: "", sampleRegex: "", samples: "" };
}

export function newSetQCCheck() {
  return { id: nextId(), inputColumn: "", conditions: [newCondition()] };
}

export function newSetQCRule() {
  // columns: one or more {input_column, qc} checks (emitted as `checks:`),
  // all read from the same
  // matched sample(s) -- lets one rule check e.g. both read count and
  // contamination percent without repeating `match` across separate rules.
  return {
    id: nextId(),
    ruleName: "",
    matchSamples: newSetQCMatch(),
    checks: [newSetQCCheck()],
  };
}

function findDuplicates(arr) {
  const seen = new Set();
  const dupes = new Set();
  for (const item of arr) {
    if (seen.has(item)) dupes.add(item);
    seen.add(item);
  }
  return [...dupes];
}

export const NUMERIC_RE = /^-?\d+(\.\d+)?$/;

function buildCondition(cond, label, errors) {
  if (!cond.operator) {
    errors.push(`${label}: operator is required`);
    return null;
  }
  if (NO_VALUE_OPERATORS.has(cond.operator)) {
    return { operator: cond.operator };
  }
  const raw = (cond.value ?? "").toString().trim();
  if (!raw) {
    errors.push(`${label}: value is required`);
    return null;
  }
  const isNumeric = NUMERIC_RE.test(raw);
  let value;
  if (cond.operator === "contains" || cond.operator === "does_not_contain") {
    value = raw; // substring checks always compare against a string
  } else if (cond.operator === "=") {
    value = cond.forceString || !isNumeric ? raw : Number(raw);
  } else {
    if (!isNumeric) {
      errors.push(`${label}: operator "${cond.operator}" requires a numeric value, got "${raw}"`);
      return null;
    }
    value = Number(raw);
  }

  const out = { operator: cond.operator, value };
  const rawTolerance = (cond.tolerancePercent ?? "").toString().trim();
  if (cond.operator === "~=") {
    if (!rawTolerance) {
      errors.push(`${label}: tolerance_percent is required for operator "~="`);
    } else {
      const tol = Number(rawTolerance);
      if (Number.isNaN(tol) || tol <= 0) {
        errors.push(`${label}: tolerance_percent must be a number greater than 0`);
      } else {
        out.tolerance_percent = tol;
      }
    }
  } else if (rawTolerance) {
    errors.push(`${label}: tolerance_percent is only valid with operator "~="`);
  }

  if (cond.caseInsensitive === true) {
    if (typeof value !== "string") {
      errors.push(`${label}: case_insensitive: true is only valid when value is a string`);
    } else {
      out.case_insensitive = true;
    }
  }
  return out;
}

function buildConditionList(conditions, label, errors) {
  return conditions
    .map((c, i) => buildCondition(c, `${label} condition #${i + 1}`, errors))
    .filter(Boolean);
}

/** Returns a plain `qc` value (array or {match_column, cases, default}), or undefined if there's no QC. */
function buildQC(qc, label, errors) {
  if (!qc || qc.kind === "none") return undefined;

  if (qc.kind === "list") {
    const conditions = buildConditionList(qc.conditions, label, errors);
    return conditions.length ? conditions : undefined;
  }

  // conditional
  const matchColumn = (qc.matchColumn ?? "").trim();
  if (!matchColumn) errors.push(`${label}: conditional qc requires a "match_column" name`);
  if (!qc.cases.length) errors.push(`${label}: conditional qc requires at least one case`);

  const cases = {};
  const caseKeys = [];
  qc.cases.forEach((qcCase, i) => {
    const key = (qcCase.key ?? "").trim();
    const caseLabel = `${label} case "${key || `#${i + 1}`}"`;
    if (!key) errors.push(`${caseLabel}: case key is required`);
    caseKeys.push(key);
    const conditions = buildConditionList(qcCase.conditions, caseLabel, errors);
    if (!conditions.length) errors.push(`${caseLabel}: needs at least one condition`);
    if (key) cases[key] = conditions;
  });
  const dupeKeys = findDuplicates(caseKeys.filter(Boolean));
  if (dupeKeys.length) errors.push(`${label}: duplicate case key(s): ${dupeKeys.join(", ")}`);

  const result = { match_column: matchColumn, cases };
  if (qc.useDefault) {
    const def = buildConditionList(qc.default, `${label} default`, errors);
    if (def.length) result.default = def;
  }
  return result;
}

/** Returns a plain `match` value ({sample_pattern}/{sample_regex}/{samples}), or null if invalid. */
function buildSetQCMatch(match, label, errors) {
  if (match.kind === "pattern") {
    const pattern = (match.samplePattern ?? "").trim();
    if (!pattern) {
      errors.push(`${label}: sample_pattern is required`);
      return null;
    }
    return { sample_pattern: pattern };
  }
  if (match.kind === "regex") {
    const regex = (match.sampleRegex ?? "").trim();
    if (!regex) {
      errors.push(`${label}: sample_regex is required`);
      return null;
    }
    // Not validated client-side: JS and Python regex dialects diverge, so a
    // browser-side syntax check could reject a pattern load_config() would
    // accept (or vice versa). The real load_config() is the only gate.
    return { sample_regex: regex };
  }
  // samples: comma-or-newline-separated free text -> a list of exact names
  const samples = (match.samples ?? "")
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (!samples.length) {
    errors.push(`${label}: at least one sample name is required`);
    return null;
  }
  return { samples };
}

/** Returns a plain `input_column`/`qc` check within a set_qc rule, or null if it has unrecoverable errors. */
function buildSetQCCheck(check, label, errors) {
  const checkLabel = `${label} > column "${check.inputColumn.trim() || "( )"}"`;
  if (!check.inputColumn.trim()) errors.push(`${checkLabel}: column is required`);
  const conditions = buildConditionList(check.conditions, checkLabel, errors);
  if (!conditions.length) errors.push(`${checkLabel}: needs at least one condition`);

  if (!check.inputColumn.trim() || !conditions.length) return null;
  return { input_column: check.inputColumn.trim(), qc: conditions };
}

/** Returns a plain `set_qc` rule, or null if it has unrecoverable errors. */
function buildSetQCRule(rule, idx, errors) {
  const label = rule.ruleName.trim() || `set_qc rule #${idx + 1}`;
  if (!rule.ruleName.trim()) errors.push(`${label}: name is required`);

  const match = buildSetQCMatch(rule.matchSamples, label, errors);
  const checks = rule.checks.map((c) => buildSetQCCheck(c, label, errors)).filter(Boolean);
  if (!rule.checks.length) errors.push(`${label}: needs at least one column to check`);

  const columnDupes = findDuplicates(rule.checks.map((c) => c.inputColumn.trim()).filter(Boolean));
  if (columnDupes.length) errors.push(`${label}: duplicate column(s) within this rule: ${columnDupes.join(", ")}`);

  if (!rule.ruleName.trim() || match === null || checks.length !== rule.checks.length || !checks.length) {
    return null;
  }
  return { rule_name: rule.ruleName.trim(), match_samples: match, checks };
}

/**
 * Builds the plain, ExportConfig-shaped object from wizard state and
 * collects human-readable validation errors (mirroring the checks
 * `limsport/config.py` enforces). `plain` is still returned even when
 * there are errors so the preview pane can show a best-effort draft; only
 * `errors` should gate the download/copy actions.
 */
export function buildConfig(columns, setQCRules = []) {
  const errors = [];
  // Every name this config will actually produce in the output header, plus
  // where it came from -- lets the cross-column duplicate check below name
  // names, not just flag that *some* collision exists somewhere.
  const outputProvenance = [];

  if (!columns.length && !setQCRules.length) {
    errors.push("Add at least one column or set-level QC rule.");
  }

  const plainColumns = columns.map((col, idx) => {
    const label = col.inputColumn.trim() || `Column #${idx + 1}`;
    if (!col.inputColumn.trim()) errors.push(`${label}: name is required`);

    if (col.isFileParsing) {
      if (!col.fileParsing.length) {
        errors.push(`${label}: file parsing needs at least one output`);
      }
      const outputNames = [];
      const outputs = col.fileParsing.map((fp, fpIdx) => {
        const fpLabel = `${label} > output "${fp.outputColumn.trim() || `#${fpIdx + 1}`}"`;
        const name = fp.outputColumn.trim();
        if (!name) errors.push(`${fpLabel}: output name is required`);
        if (!fp.command.trim()) errors.push(`${fpLabel}: command is required`);
        outputNames.push(name);
        if (name) outputProvenance.push({ name, source: fpLabel });

        const out = { output_column: name, command: fp.command };
        const rawTimeout = (fp.timeoutSeconds ?? "").toString().trim();
        if (rawTimeout) {
          const timeout = Number(rawTimeout);
          if (Number.isNaN(timeout) || timeout <= 0) {
            errors.push(`${fpLabel}: timeout_seconds must be a number greater than 0`);
          } else {
            out.timeout_seconds = timeout;
          }
        }
        const qc = buildQC(fp.qc, fpLabel, errors);
        if (qc !== undefined) out.qc = qc;
        return out;
      });
      const dupes = findDuplicates(outputNames.filter(Boolean));
      if (dupes.length) errors.push(`${label}: duplicate file_parsing output name(s): ${dupes.join(", ")}`);
      return { input_column: col.inputColumn.trim(), file_parsing: outputs };
    }

    const entry = { input_column: col.inputColumn.trim() };
    // output: false and output_column are mutually exclusive (a hidden
    // column has no output name), the same structural exclusivity as
    // output_column/qc vs. file_parsing above -- the wizard hides
    // outputColumnField whenever this checkbox is on, so a stale rename
    // typed before hiding it is simply never read here.
    if (col.output === false) {
      entry.output = false;
    } else if (col.outputColumn.trim()) {
      entry.output_column = col.outputColumn.trim();
    }
    const qc = buildQC(col.qc, label, errors);
    if (qc !== undefined) entry.qc = qc;
    if (col.output !== false) {
      const outputName = (entry.output_column || entry.input_column).trim();
      if (outputName) outputProvenance.push({ name: outputName, source: label });
    }
    return entry;
  });

  const nameDupes = findDuplicates(columns.map((c) => c.inputColumn.trim()).filter(Boolean));
  if (nameDupes.length) errors.push(`Duplicate column name(s): ${nameDupes.join(", ")}`);

  if (columns.length && columns.every((c) => c.output === false)) {
    errors.push(
      "At least one column must not be excluded from the output (every column is currently excluded, which would produce an output table with no columns)."
    );
  }

  // Group every produced name by its sources (column label, or "column >
  // output" for file_parsing) so a collision -- whether within one
  // column's file_parsing list, between two different file_parsing
  // columns, or between a file_parsing output and an ordinary rename --
  // always names exactly where it came from.
  const sourcesByName = new Map();
  for (const { name, source } of outputProvenance) {
    if (!sourcesByName.has(name)) sourcesByName.set(name, []);
    sourcesByName.get(name).push(source);
  }
  for (const [name, sources] of sourcesByName) {
    if (sources.length > 1) {
      errors.push(`Duplicate output column name "${name}" is produced by: ${sources.join(", ")}`);
    }
  }

  const plainSetQC = setQCRules.map((rule, idx) => buildSetQCRule(rule, idx, errors)).filter(Boolean);
  const setQCNameDupes = findDuplicates(setQCRules.map((r) => r.ruleName.trim()).filter(Boolean));
  if (setQCNameDupes.length) errors.push(`Duplicate set_qc rule name(s): ${setQCNameDupes.join(", ")}`);

  const plain = { columns: plainColumns };
  if (setQCRules.length) plain.set_qc = plainSetQC;
  return { plain, errors };
}

// ---------------------------------------------------------------------------
// YAML serialization
// ---------------------------------------------------------------------------

const QUOTE_NEEDED_RE = /^\s|\s$|^$|[:#{}\[\],&*!|>'"%@`\n\t<=]/;
const AMBIGUOUS_SCALAR_RE = /^(true|false|null|yes|no|on|off|~|-?\d+(\.\d+)?)$/i;

function yamlScalar(value) {
  if (typeof value === "number") return String(value);
  const s = String(value);
  if (QUOTE_NEEDED_RE.test(s) || AMBIGUOUS_SCALAR_RE.test(s)) {
    return JSON.stringify(s);
  }
  return s;
}

// Rule keys (organism names, etc.) are always quoted -- they're free-form
// strings matched against real data and often contain spaces.
function yamlKey(value) {
  return JSON.stringify(String(value));
}

// A string-operator ('=', 'contains', 'does_not_contain') condition's value
// that isn't numeric (e.g. "PASS") is a string match target -- always quote
// it, regardless of whether it happens to be plain alphanumeric, so it's
// visually unambiguous that this is a string comparison, not a bare keyword.
function yamlEqualityValue(value) {
  if (typeof value === "number") return String(value);
  return JSON.stringify(String(value));
}

function renderConditionInline(cond) {
  const parts = [`operator: ${yamlScalar(cond.operator)}`];
  if (!NO_VALUE_OPERATORS.has(cond.operator)) {
    const valueScalar = STRING_OPERATORS.has(cond.operator)
      ? yamlEqualityValue(cond.value)
      : yamlScalar(cond.value);
    parts.push(`value: ${valueScalar}`);
  }
  if (cond.tolerance_percent !== undefined) {
    parts.push(`tolerance_percent: ${yamlScalar(cond.tolerance_percent)}`);
  }
  if (cond.case_insensitive === true) {
    parts.push(`case_insensitive: true`);
  }
  return `{${parts.join(", ")}}`;
}

function renderConditionList(list, ind) {
  return list.map((c) => `${ind}- ${renderConditionInline(c)}`).join("\n");
}

function renderQC(qc, kInd) {
  const contentInd = `${kInd}  `;
  if (Array.isArray(qc)) {
    return `${kInd}qc:\n${renderConditionList(qc, contentInd)}`;
  }
  const lines = [`${kInd}qc:`, `${contentInd}match_column: ${yamlScalar(qc.match_column)}`, `${contentInd}cases:`];
  const rulesInd = `${contentInd}  `;
  for (const [key, conditions] of Object.entries(qc.cases)) {
    lines.push(`${rulesInd}${yamlKey(key)}:`);
    lines.push(renderConditionList(conditions, `${rulesInd}  `));
  }
  if (qc.default) {
    lines.push(`${contentInd}default:`);
    lines.push(renderConditionList(qc.default, `${contentInd}  `));
  }
  return lines.join("\n");
}

function renderCommandBlock(command, kInd) {
  const contentInd = `${kInd}  `;
  const body = command.replace(/\r\n/g, "\n").replace(/\n+$/, "");
  const bodyLines = body.split("\n").map((l) => (l.length ? contentInd + l : ""));
  return `${kInd}command: |\n${bodyLines.join("\n")}`;
}

function renderFileParsingOutput(fp, dashInd) {
  const kInd = `${dashInd}  `;
  const lines = [`${dashInd}- output_column: ${yamlScalar(fp.output_column)}`];
  lines.push(renderCommandBlock(fp.command, kInd));
  if (fp.timeout_seconds !== undefined) {
    lines.push(`${kInd}timeout_seconds: ${yamlScalar(fp.timeout_seconds)}`);
  }
  if (fp.qc !== undefined) lines.push(renderQC(fp.qc, kInd));
  return lines.join("\n");
}

function renderColumn(col, dashInd) {
  const kInd = `${dashInd}  `;
  const lines = [`${dashInd}- input_column: ${yamlScalar(col.input_column)}`];
  if (col.output_column) lines.push(`${kInd}output_column: ${yamlScalar(col.output_column)}`);
  if (col.output === false) lines.push(`${kInd}output: false`);
  if (col.qc !== undefined) lines.push(renderQC(col.qc, kInd));
  if (col.file_parsing) {
    lines.push(`${kInd}file_parsing:`);
    col.file_parsing.forEach((fp) => lines.push(renderFileParsingOutput(fp, `${kInd}  `)));
  }
  return lines.join("\n");
}

// sample_pattern/sample_regex/samples are all free-form strings intended as
// exact/substring/regex match targets against real sample names -- always
// quoted, same "unambiguously a string" reasoning as yamlKey/rule keys.
function renderSetQCMatch(match, kInd) {
  const contentInd = `${kInd}  `;
  if (match.sample_pattern !== undefined) {
    return `${kInd}match_samples:\n${contentInd}sample_pattern: ${yamlKey(match.sample_pattern)}`;
  }
  if (match.sample_regex !== undefined) {
    return `${kInd}match_samples:\n${contentInd}sample_regex: ${yamlKey(match.sample_regex)}`;
  }
  const samples = match.samples.map((s) => yamlKey(s)).join(", ");
  return `${kInd}match_samples:\n${contentInd}samples: [${samples}]`;
}

function renderSetQCCheck(check, dashInd) {
  const kInd = `${dashInd}  `;
  const lines = [`${dashInd}- input_column: ${yamlScalar(check.input_column)}`, `${kInd}qc:`];
  lines.push(renderConditionList(check.qc, `${kInd}  `));
  return lines.join("\n");
}

function renderSetQCRule(rule, dashInd) {
  const kInd = `${dashInd}  `;
  const lines = [`${dashInd}- rule_name: ${yamlKey(rule.rule_name)}`];
  lines.push(renderSetQCMatch(rule.match_samples, kInd));
  lines.push(`${kInd}checks:`);
  rule.checks.forEach((check) => lines.push(renderSetQCCheck(check, `${kInd}  `)));
  return lines.join("\n");
}

/** Serializes a plain ExportConfig-shaped object (as returned by buildConfig().plain) to a YAML string. */
export function serializeYAML(plainConfig) {
  const lines = [];
  // Omit `columns:` entirely when there are none -- an explicit
  // `columns: []` is always rejected by the real load_config(), even when
  // set_qc is configured; only omitting the key means "pass every input
  // column through unfiltered."
  if (plainConfig.columns.length) {
    lines.push("columns:");
    plainConfig.columns.forEach((col, i) => {
      if (i > 0) lines.push("");
      lines.push(renderColumn(col, "  "));
    });
  }
  if (plainConfig.set_qc && plainConfig.set_qc.length) {
    lines.push("set_qc:");
    plainConfig.set_qc.forEach((rule, i) => {
      if (i > 0) lines.push("");
      lines.push(renderSetQCRule(rule, "  "));
    });
  }
  lines.push("");
  return lines.join("\n");
}
