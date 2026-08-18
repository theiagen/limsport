/**
 * Pure logic for the LIMSport config builder: turns the wizard's in-memory
 * state into the same shape `limsport.config.ExportConfig` expects, validates
 * it the way that Pydantic model does, and serializes it to YAML.
 *
 * No DOM access here on purpose -- this file is imported both by the
 * browser page (app.js) and by the Node test suite (tests/schema.test.mjs),
 * which feeds its output straight through the real `load_config()` in
 * src/limsport/config.py to confirm it's accepted.
 */

export const OPERATORS = [">", ">=", "=", "<=", "<", "~=", "contains", "does_not_contain"];

// Operators whose value is a string comparison, not a numeric one -- these
// are the only operators `case_insensitive` applies to.
export const STRING_OPERATORS = new Set(["=", "contains", "does_not_contain"]);

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

export function newRule() {
  return { id: nextId(), key: "", conditions: [newCondition()] };
}

export function newQC() {
  // kind: "none" | "list" | "conditional"
  return { kind: "none", conditions: [], match: "", rules: [], useDefault: false, default: [] };
}

export function newFileParsingOutput() {
  return {
    id: nextId(),
    name: "",
    command: "",
    timeoutSeconds: "",
    qc: newQC(),
  };
}

export function newColumn() {
  return {
    id: nextId(),
    name: "",
    isFileParsing: false,
    rename: "",
    qc: newQC(),
    fileParsing: [newFileParsingOutput()],
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

/** Returns a plain `qc` value (array or {match, rules, default}), or undefined if there's no QC. */
function buildQC(qc, label, errors) {
  if (!qc || qc.kind === "none") return undefined;

  if (qc.kind === "list") {
    const conditions = buildConditionList(qc.conditions, label, errors);
    return conditions.length ? conditions : undefined;
  }

  // conditional
  const match = (qc.match ?? "").trim();
  if (!match) errors.push(`${label}: conditional qc requires a "match" column name`);
  if (!qc.rules.length) errors.push(`${label}: conditional qc requires at least one rule`);

  const rules = {};
  const ruleKeys = [];
  qc.rules.forEach((rule, i) => {
    const key = (rule.key ?? "").trim();
    const ruleLabel = `${label} rule "${key || `#${i + 1}`}"`;
    if (!key) errors.push(`${ruleLabel}: rule key is required`);
    ruleKeys.push(key);
    const conditions = buildConditionList(rule.conditions, ruleLabel, errors);
    if (!conditions.length) errors.push(`${ruleLabel}: needs at least one condition`);
    if (key) rules[key] = conditions;
  });
  const dupeKeys = findDuplicates(ruleKeys.filter(Boolean));
  if (dupeKeys.length) errors.push(`${label}: duplicate rule key(s): ${dupeKeys.join(", ")}`);

  const result = { match, rules };
  if (qc.useDefault) {
    const def = buildConditionList(qc.default, `${label} default`, errors);
    if (def.length) result.default = def;
  }
  return result;
}

/**
 * Builds the plain, ExportConfig-shaped object from wizard state and
 * collects human-readable validation errors (mirroring the checks
 * `src/limsport/config.py` enforces). `plain` is still returned even when
 * there are errors so the preview pane can show a best-effort draft; only
 * `errors` should gate the download/copy actions.
 */
export function buildConfig(columns) {
  const errors = [];
  // Every name this config will actually produce in the output header, plus
  // where it came from -- lets the cross-column duplicate check below name
  // names, not just flag that *some* collision exists somewhere.
  const outputProvenance = [];

  if (!columns.length) {
    errors.push("Add at least one column.");
  }

  const plainColumns = columns.map((col, idx) => {
    const label = col.name.trim() || `Column #${idx + 1}`;
    if (!col.name.trim()) errors.push(`${label}: name is required`);

    if (col.isFileParsing) {
      if (!col.fileParsing.length) {
        errors.push(`${label}: file parsing needs at least one output`);
      }
      const outputNames = [];
      const outputs = col.fileParsing.map((fp, fpIdx) => {
        const fpLabel = `${label} > output "${fp.name.trim() || `#${fpIdx + 1}`}"`;
        const name = fp.name.trim();
        if (!name) errors.push(`${fpLabel}: output name is required`);
        if (!fp.command.trim()) errors.push(`${fpLabel}: command is required`);
        outputNames.push(name);
        if (name) outputProvenance.push({ name, source: fpLabel });

        const out = { name, command: fp.command };
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
      return { name: col.name.trim(), file_parsing: outputs };
    }

    const entry = { name: col.name.trim() };
    if (col.rename.trim()) entry.rename = col.rename.trim();
    const qc = buildQC(col.qc, label, errors);
    if (qc !== undefined) entry.qc = qc;
    const outputName = (entry.rename || entry.name).trim();
    if (outputName) outputProvenance.push({ name: outputName, source: label });
    return entry;
  });

  const nameDupes = findDuplicates(columns.map((c) => c.name.trim()).filter(Boolean));
  if (nameDupes.length) errors.push(`Duplicate column name(s): ${nameDupes.join(", ")}`);

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

  return { plain: { columns: plainColumns }, errors };
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
  const valueScalar = STRING_OPERATORS.has(cond.operator)
    ? yamlEqualityValue(cond.value)
    : yamlScalar(cond.value);
  const parts = [`operator: ${yamlScalar(cond.operator)}`, `value: ${valueScalar}`];
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
  const lines = [`${kInd}qc:`, `${contentInd}match: ${yamlScalar(qc.match)}`, `${contentInd}rules:`];
  const rulesInd = `${contentInd}  `;
  for (const [key, conditions] of Object.entries(qc.rules)) {
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
  const lines = [`${dashInd}- name: ${yamlScalar(fp.name)}`];
  lines.push(renderCommandBlock(fp.command, kInd));
  if (fp.timeout_seconds !== undefined) {
    lines.push(`${kInd}timeout_seconds: ${yamlScalar(fp.timeout_seconds)}`);
  }
  if (fp.qc !== undefined) lines.push(renderQC(fp.qc, kInd));
  return lines.join("\n");
}

function renderColumn(col, dashInd) {
  const kInd = `${dashInd}  `;
  const lines = [`${dashInd}- name: ${yamlScalar(col.name)}`];
  if (col.rename) lines.push(`${kInd}rename: ${yamlScalar(col.rename)}`);
  if (col.qc !== undefined) lines.push(renderQC(col.qc, kInd));
  if (col.file_parsing) {
    lines.push(`${kInd}file_parsing:`);
    col.file_parsing.forEach((fp) => lines.push(renderFileParsingOutput(fp, `${kInd}  `)));
  }
  return lines.join("\n");
}

/** Serializes a plain ExportConfig-shaped object (as returned by buildConfig().plain) to a YAML string. */
export function serializeYAML(plainConfig) {
  if (!plainConfig.columns.length) return "columns: []\n";
  const lines = ["columns:"];
  plainConfig.columns.forEach((col, i) => {
    if (i > 0) lines.push("");
    lines.push(renderColumn(col, "  "));
  });
  lines.push("");
  return lines.join("\n");
}
