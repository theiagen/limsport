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

// A case's matchType picks which config block it's written to: "exact" ->
// `cases`, "pattern" -> `pattern_cases`, "regex" -> `regex_cases`.
export const CASE_MATCH_TYPES = ["exact", "pattern", "regex"];
const CASE_BLOCK_FOR = {
    exact: "cases",
    pattern: "pattern_cases",
    regex: "regex_cases",
};

export function newCase() {
    return {
        id: nextId(),
        matchType: "exact",
        key: "",
        conditions: [newCondition()],
    };
}

export function newQC() {
    // kind: "none" | "list" | "conditional"
    return {
        kind: "none",
        conditions: [],
        matchColumn: "",
        cases: [],
        useDefault: false,
        default: [],
    };
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
    // A set_qc check has no "No QC" option, so its qc starts as a fixed list.
    return {
        id: nextId(),
        inputColumn: "",
        qc: { ...newQC(), kind: "list", conditions: [newCondition()] },
    };
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
        includeSampleQC: false,
    };
}

export function newSkipSampleQC() {
    // enabled: false means the config has no `skip_sample_qc` key at all.
    return { enabled: false, match: newSetQCMatch() };
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
            errors.push(
                `${label}: operator "${cond.operator}" requires a numeric value, got "${raw}"`,
            );
            return null;
        }
        value = Number(raw);
    }

    const out = { operator: cond.operator, value };
    const rawTolerance = (cond.tolerancePercent ?? "").toString().trim();
    if (cond.operator === "~=") {
        if (!rawTolerance) {
            errors.push(
                `${label}: tolerance_percent is required for operator "~="`,
            );
        } else {
            const tol = Number(rawTolerance);
            if (Number.isNaN(tol) || tol <= 0) {
                errors.push(
                    `${label}: tolerance_percent must be a number greater than 0`,
                );
            } else {
                out.tolerance_percent = tol;
            }
        }
    } else if (rawTolerance) {
        errors.push(
            `${label}: tolerance_percent is only valid with operator "~="`,
        );
    }

    if (cond.caseInsensitive === true) {
        if (typeof value !== "string") {
            errors.push(
                `${label}: case_insensitive: true is only valid when value is a string`,
            );
        } else {
            out.case_insensitive = true;
        }
    }
    return out;
}

function buildConditionList(conditions, label, errors) {
    return conditions
        .map((c, i) =>
            buildCondition(c, `${label} condition #${i + 1}`, errors),
        )
        .filter(Boolean);
}

/** Returns a plain `qc` value (array or {match_column, cases, pattern_cases, regex_cases, default}), or undefined if there's no QC. */
function buildQC(qc, label, errors) {
    if (!qc || qc.kind === "none") return undefined;

    if (qc.kind === "list") {
        if (!qc.conditions.length) {
            errors.push(
                `${label}: qc is set to "Fixed threshold(s)" but has no conditions. Add one, or set it to "No QC on this value"`,
            );
            return undefined;
        }
        const conditions = buildConditionList(qc.conditions, label, errors);
        return conditions.length ? conditions : undefined;
    }

    // conditional
    const matchColumn = (qc.matchColumn ?? "").trim();
    if (!matchColumn)
        errors.push(`${label}: conditional qc requires a "match_column" name`);
    if (!qc.cases.length)
        errors.push(`${label}: conditional qc requires at least one case`);

    const blocks = { cases: {}, pattern_cases: {}, regex_cases: {} };
    const caseKeys = { cases: [], pattern_cases: [], regex_cases: [] };
    qc.cases.forEach((qcCase, i) => {
        const block = CASE_BLOCK_FOR[qcCase.matchType ?? "exact"];
        const key = (qcCase.key ?? "").trim();
        const caseLabel = `${label} case "${key || `#${i + 1}`}"`;
        if (!key) errors.push(`${caseLabel}: case key is required`);
        caseKeys[block].push(key);
        const conditions = buildConditionList(
            qcCase.conditions,
            caseLabel,
            errors,
        );
        if (!conditions.length)
            errors.push(`${caseLabel}: needs at least one condition`);
        if (key) blocks[block][key] = conditions;
    });
    for (const [block, keys] of Object.entries(caseKeys)) {
        const dupeKeys = findDuplicates(keys.filter(Boolean));
        if (dupeKeys.length)
            errors.push(
                `${label}: duplicate ${block} key(s): ${dupeKeys.join(", ")}`,
            );
    }

    // Empty blocks are omitted; load_config() only needs one of the three.
    const result = { match_column: matchColumn };
    for (const [block, entries] of Object.entries(blocks)) {
        if (Object.keys(entries).length) result[block] = entries;
    }
    if (qc.useDefault) {
        const def = buildConditionList(qc.default, `${label} default`, errors);
        if (def.length) result.default = def;
    }
    return result;
}

/** Returns a plain `match` value ({sample_pattern}/{sample_regex}/{samples}); a blank one is still returned, for the preview, alongside its error. */
function buildSetQCMatch(match, label, errors) {
    if (match.kind === "pattern") {
        const pattern = (match.samplePattern ?? "").trim();
        if (!pattern) errors.push(`${label}: sample_pattern is required`);
        return { sample_pattern: pattern };
    }
    if (match.kind === "regex") {
        const regex = (match.sampleRegex ?? "").trim();
        if (!regex) errors.push(`${label}: sample_regex is required`);
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
    if (!samples.length)
        errors.push(`${label}: at least one sample name is required`);
    return { samples };
}

/** Returns a plain `input_column`/`qc` check within a set_qc rule -- a best-effort draft when it has errors, like a column. */
function buildSetQCCheck(check, label, errors) {
    const checkLabel = `${label} > column "${check.inputColumn.trim() || "( )"}"`;
    if (!check.inputColumn.trim())
        errors.push(`${checkLabel}: input_column is required`);
    let qc;
    if (check.qc.kind === "list" && !check.qc.conditions.length) {
        errors.push(`${checkLabel}: needs at least one condition`);
    } else {
        qc = buildQC(check.qc, checkLabel, errors);
        if (check.qc.kind === "none")
            errors.push(`${checkLabel}: needs a QC check`);
    }

    const out = { input_column: check.inputColumn.trim() };
    if (qc !== undefined) out.qc = qc;
    return out;
}

/** Returns a plain `set_qc` rule -- a best-effort draft when it has errors, so the preview grows as it's filled in. */
function buildSetQCRule(rule, idx, errors, hasSampleQC) {
    const label = rule.ruleName.trim() || `set_qc rule #${idx + 1}`;
    if (!rule.ruleName.trim()) errors.push(`${label}: rule_name is required`);

    const match = buildSetQCMatch(rule.matchSamples, label, errors);
    const checks = rule.checks.map((c) => buildSetQCCheck(c, label, errors));
    if (!rule.checks.length && !rule.includeSampleQC)
        errors.push(
            `${label}: needs at least one column to check, or "Include all sample-level QC checks in this rule" turned on`,
        );
    // same check as ExportConfig._include_sample_qc_has_qc_to_include()
    if (rule.includeSampleQC && !hasSampleQC)
        errors.push(
            `${label}: "Include all sample-level QC checks in this rule" is on, but no sample-level QC column has QC to include`,
        );

    const columnDupes = findDuplicates(
        rule.checks.map((c) => c.inputColumn.trim()).filter(Boolean),
    );
    if (columnDupes.length)
        errors.push(
            `${label}: duplicate column(s) within this rule: ${columnDupes.join(", ")}`,
        );

    const out = { rule_name: rule.ruleName.trim(), match_samples: match };
    if (checks.length) out.checks = checks;
    if (rule.includeSampleQC) out.include_sample_qc = true;
    return out;
}

/** Returns the names in `names` that `match` (a plain matcher) selects, or null if its regex won't compile in JS. */
function namesMatching(match, names) {
    if (match.samples) {
        const listed = new Set(match.samples);
        return names.filter((n) => listed.has(n));
    }
    // a blank matcher (still being typed) selects nothing yet
    if (match.sample_pattern !== undefined)
        return match.sample_pattern
            ? names.filter((n) => n.includes(match.sample_pattern))
            : [];
    if (!match.sample_regex) return [];
    let regex;
    try {
        // JS and Python regex dialects differ; a pattern only Python accepts
        // is skipped rather than guessed at.
        regex = new RegExp(match.sample_regex);
    } catch {
        return null;
    }
    return names.filter((n) => regex.test(n));
}

/**
 * Predicts which samples would be both skipped (skip_sample_qc) and pulled
 * into a set_qc rule's include_sample_qc -- a run-time error in limsport. Only
 * overlaps that are certain from the config alone are reported: a sample list
 * checked against the other matcher, or two patterns where one contains the
 * other. A regex against a pattern or regex can't be decided, so it's skipped.
 *
 * Returns a list of warning strings.
 */
function predictSkipCollisions(plainSetQC, skipMatch) {
    const warnings = [];
    if (!skipMatch) return warnings;
    for (const rule of plainSetQC) {
        if (!rule.include_sample_qc) continue;
        const ruleMatch = rule.match_samples;
        const name = rule.rule_name || "(unnamed rule)";
        const rulePrefix = `The set-level QC rule "${name}" requires sample-level QC for`;
        // A rule's `samples:` must all be in the run, so an overlap there
        // always fails; any other overlap only fails if the sample shows up.
        let overlap = null;
        let certain = false;
        if (ruleMatch.samples?.length) {
            overlap = namesMatching(skipMatch, ruleMatch.samples);
            certain = true;
        } else if (skipMatch.samples?.length) {
            overlap = namesMatching(ruleMatch, skipMatch.samples);
        } else if (ruleMatch.sample_pattern && skipMatch.sample_pattern) {
            const [a, b] = [ruleMatch.sample_pattern, skipMatch.sample_pattern];
            if (a.includes(b) || b.includes(a)) {
                const longer = a.length >= b.length ? a : b;
                warnings.push(
                    `${rulePrefix} samples containing "${longer}", which are marked to skip it. This may cause LIMSport to fail.`,
                );
            }
            continue;
        }
        if (!overlap?.length) continue;
        const matched =
            overlap.length === 1
                ? `"${overlap[0]}", which is marked to skip it.`
                : `[${overlap.map((n) => `"${n}"`).join(", ")}], which are marked to skip it.`;
        const consequence = certain
            ? "This will cause LIMSport to fail."
            : "This may cause LIMSport to fail.";
        warnings.push(
            `${rulePrefix} ${matched} ${consequence}`,
        );
    }
    return warnings;
}

/**
 * Builds the plain, ExportConfig-shaped object from wizard state and
 * collects human-readable validation errors (mirroring the checks
 * `limsport/config.py` enforces). `plain` is still returned even when
 * there are errors so the preview pane can show a best-effort draft; only
 * `errors` should gate the download/copy actions. `warnings` flag likely
 * run-time problems that don't make the config invalid.
 */
export function buildConfig(columns, setQCRules = [], skipSampleQC = null) {
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
        if (!col.inputColumn.trim())
            errors.push(`${label}: input_column is required`);

        if (col.isFileParsing) {
            if (!col.fileParsing.length) {
                errors.push(`${label}: file parsing needs at least one output`);
            }
            const outputNames = [];
            const outputs = col.fileParsing.map((fp, fpIdx) => {
                const fpLabel = `${label} > output "${fp.outputColumn.trim() || `#${fpIdx + 1}`}"`;
                const name = fp.outputColumn.trim();
                if (!name) errors.push(`${fpLabel}: output name is required`);
                if (!fp.command.trim())
                    errors.push(`${fpLabel}: command is required`);
                outputNames.push(name);
                if (name) outputProvenance.push({ name, source: fpLabel });

                const out = { output_column: name, command: fp.command };
                const rawTimeout = (fp.timeoutSeconds ?? "").toString().trim();
                if (rawTimeout) {
                    const timeout = Number(rawTimeout);
                    if (Number.isNaN(timeout) || timeout <= 0) {
                        errors.push(
                            `${fpLabel}: timeout_seconds must be a number greater than 0`,
                        );
                    } else {
                        out.timeout_seconds = timeout;
                    }
                }
                const qc = buildQC(fp.qc, fpLabel, errors);
                if (qc !== undefined) out.qc = qc;
                return out;
            });
            const dupes = findDuplicates(outputNames.filter(Boolean));
            if (dupes.length)
                errors.push(
                    `${label}: duplicate file_parsing output name(s): ${dupes.join(", ")}`,
                );
            return {
                input_column: col.inputColumn.trim(),
                file_parsing: outputs,
            };
        }

        const entry = { input_column: col.inputColumn.trim() };
        // output: false and output_column are mutually exclusive (a hidden
        // column has no output name), the same structural exclusivity as
        // output_column/qc vs. file_parsing above -- the wizard hides
        // outputColumnField whenever this mode is selected, so a stale
        // rename typed before switching mode is simply never read here.
        if (col.output === false) {
            entry.output = false;
        } else if (col.outputColumn.trim()) {
            entry.output_column = col.outputColumn.trim();
        }
        const qc = buildQC(col.qc, label, errors);
        if (qc !== undefined) entry.qc = qc;
        // Not a config.py rule -- load_config() allows output:false with no
        // qc -- but a column excluded from the output that nothing checks
        // does literally nothing, so the wizard flags it as likely a mistake.
        if (col.output === false && col.qc.kind === "none") {
            errors.push(
                `${label}: excluded from the output but has no QC configured. It would do nothing; add a QC check or keep it in the output`,
            );
        }
        if (col.output !== false) {
            const outputName = (
                entry.output_column || entry.input_column
            ).trim();
            if (outputName)
                outputProvenance.push({ name: outputName, source: label });
        }
        return entry;
    });

    const nameDupes = findDuplicates(
        columns.map((c) => c.inputColumn.trim()).filter(Boolean),
    );
    if (nameDupes.length)
        errors.push(`Duplicate column name(s): ${nameDupes.join(", ")}`);

    if (columns.length && columns.every((c) => c.output === false)) {
        errors.push(
            "At least one column must not be excluded from the output (every column is currently excluded, which would produce an output table with no columns).",
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
            errors.push(
                `Duplicate output column name "${name}" is produced by: ${sources.join(", ")}`,
            );
        }
    }

    const hasSampleQC = columns.some((c) =>
        c.isFileParsing
            ? c.fileParsing.some((fp) => fp.qc.kind !== "none")
            : c.qc.kind !== "none",
    );
    const plainSetQC = setQCRules.map((rule, idx) =>
        buildSetQCRule(rule, idx, errors, hasSampleQC),
    );
    const setQCNameDupes = findDuplicates(
        setQCRules.map((r) => r.ruleName.trim()).filter(Boolean),
    );
    if (setQCNameDupes.length)
        errors.push(
            `Duplicate set_qc rule name(s): ${setQCNameDupes.join(", ")}`,
        );

    const plain = { sample_qc: plainColumns };
    if (skipSampleQC?.enabled) {
        plain.skip_sample_qc = buildSetQCMatch(
            skipSampleQC.match,
            "Skip sample-level QC",
            errors,
        );
    }
    if (setQCRules.length) plain.set_qc = plainSetQC;
    const warnings = predictSkipCollisions(plainSetQC, plain.skip_sample_qc);
    return { plain, errors, warnings };
}

// ---------------------------------------------------------------------------
// Import: a parsed config.yaml back into wizard state
// ---------------------------------------------------------------------------

const isMapping = (v) =>
    v !== null && typeof v === "object" && !Array.isArray(v);

function checkKeys(obj, allowed, path, errors) {
    for (const key of Object.keys(obj)) {
        if (!allowed.includes(key))
            errors.push({
                path: [...path, key],
                message: `unknown key "${key}"`,
            });
    }
}

function listAt(raw, path, errors, what) {
    if (raw === undefined || raw === null) return [];
    if (!Array.isArray(raw)) {
        errors.push({ path, message: `expected a list of ${what}` });
        return [];
    }
    return raw;
}

// For the name-like fields buildConfig() trims: outer whitespace would be
// silently dropped on download, so it's rejected instead.
function readName(obj, key, path, errors) {
    const v = obj[key];
    if (v === undefined || v === null) return "";
    if (typeof v !== "string") {
        errors.push({
            path: [...path, key],
            message: "must be a string (quote it)",
        });
        return "";
    }
    if (v !== v.trim()) {
        errors.push({
            path: [...path, key],
            message:
                "has leading or trailing whitespace, which the builder would drop",
        });
    }
    return v.trim();
}

// String(1e-7) is "1e-7", which NUMERIC_RE rejects and PyYAML reads as a
// string (its floats need a dot) -- spell exponent forms out as decimals.
function numberToText(n) {
    const s = String(n);
    const m = /^(-?)(\d)(?:\.(\d+))?e([+-]\d+)$/.exec(s);
    if (!m) return s;
    const [, sign, int, frac = "", expText] = m;
    const digits = int + frac;
    const exp = Number(expText);
    if (exp < 0) return `${sign}0.${"0".repeat(-exp - 1)}${digits}`;
    return sign + digits.padEnd(exp + 1, "0");
}

function readNumberText(obj, key, path, errors) {
    const v = obj[key];
    if (v === undefined || v === null) return "";
    if (typeof v !== "number" || !Number.isFinite(v)) {
        errors.push({ path: [...path, key], message: "must be a number" });
        return "";
    }
    return numberToText(v);
}

function conditionFromConfig(raw, path, errors) {
    const cond = newCondition();
    if (!isMapping(raw)) {
        errors.push({
            path,
            message: 'expected a condition like {operator: ">=", value: 1000}',
        });
        return cond;
    }
    checkKeys(
        raw,
        ["operator", "value", "tolerance_percent", "case_insensitive"],
        path,
        errors,
    );
    if (!OPERATORS.includes(raw.operator)) {
        errors.push({
            path: [...path, "operator"],
            message: `unknown operator ${JSON.stringify(raw.operator)}`,
        });
        return cond;
    }
    cond.operator = raw.operator;

    const value = raw.value;
    const valuePath = [...path, "value"];
    if (NO_VALUE_OPERATORS.has(cond.operator)) {
        if (value !== undefined && value !== null)
            errors.push({
                path: valuePath,
                message: `operator "${cond.operator}" does not take a value`,
            });
    } else if (typeof value === "number") {
        if (!Number.isFinite(value)) {
            errors.push({
                path: valuePath,
                message: "must be a finite number",
            });
        } else if (
            STRING_OPERATORS.has(cond.operator) &&
            cond.operator !== "="
        ) {
            errors.push({
                path: valuePath,
                message: `operator "${cond.operator}" requires a string value`,
            });
        } else {
            cond.value = numberToText(value);
        }
    } else if (typeof value === "string") {
        if (!STRING_OPERATORS.has(cond.operator)) {
            errors.push({
                path: valuePath,
                message: `operator "${cond.operator}" requires a numeric value, not a string`,
            });
        } else if (value !== value.trim()) {
            errors.push({
                path: valuePath,
                message:
                    "has leading or trailing whitespace, which the builder would drop",
            });
        } else {
            cond.value = value;
            // A quoted numeric-looking value is a string match on purpose.
            cond.forceString = cond.operator === "=" && NUMERIC_RE.test(value);
        }
    } else if (value !== undefined && value !== null) {
        errors.push({
            path: valuePath,
            message:
                "must be a number or a string (quote it if you mean the text)",
        });
    }

    cond.tolerancePercent = readNumberText(
        raw,
        "tolerance_percent",
        path,
        errors,
    );
    if (raw.case_insensitive === true) {
        cond.caseInsensitive = true;
    } else if (
        raw.case_insensitive !== undefined &&
        raw.case_insensitive !== false
    ) {
        errors.push({
            path: [...path, "case_insensitive"],
            message: "must be true or false",
        });
    }
    return cond;
}

function conditionListFromConfig(raw, path, errors) {
    return listAt(raw, path, errors, "conditions").map((c, i) =>
        conditionFromConfig(c, [...path, i], errors),
    );
}

function qcFromConfig(raw, path, errors) {
    const qc = newQC();
    if (raw === undefined || raw === null) return qc;
    if (Array.isArray(raw)) {
        if (raw.length) {
            qc.kind = "list";
            qc.conditions = conditionListFromConfig(raw, path, errors);
        }
        return qc;
    }
    if (!isMapping(raw)) {
        errors.push({
            path,
            message:
                "expected a list of conditions or a {match_column, cases} mapping",
        });
        return qc;
    }
    checkKeys(
        raw,
        ["match_column", "cases", "pattern_cases", "regex_cases", "default"],
        path,
        errors,
    );
    qc.kind = "conditional";
    qc.matchColumn = readName(raw, "match_column", path, errors);

    for (const matchType of CASE_MATCH_TYPES) {
        const block = CASE_BLOCK_FOR[matchType];
        const blockPath = [...path, block];
        if (isMapping(raw[block])) {
            for (const [key, conditions] of Object.entries(raw[block])) {
                const qcCase = newCase();
                qcCase.matchType = matchType;
                if (key !== key.trim()) {
                    errors.push({
                        path: [...blockPath, key],
                        message:
                            "case key has leading or trailing whitespace, which the builder would drop",
                    });
                }
                qcCase.key = key.trim();
                qcCase.conditions = conditionListFromConfig(
                    conditions,
                    [...blockPath, key],
                    errors,
                );
                qc.cases.push(qcCase);
            }
        } else if (raw[block] !== undefined && raw[block] !== null) {
            errors.push({
                path: blockPath,
                message: "expected a mapping of match value to conditions",
            });
        }
    }

    if (raw.default !== undefined && raw.default !== null) {
        const defaultPath = [...path, "default"];
        qc.useDefault = true;
        qc.default = conditionListFromConfig(raw.default, defaultPath, errors);
        if (Array.isArray(raw.default) && !raw.default.length) {
            errors.push({
                path: defaultPath,
                message:
                    "an empty default passes every unmatched value, which the builder can't express; remove it or add a condition",
            });
        }
    }
    return qc;
}

function fileParsingOutputFromConfig(raw, path, errors) {
    const fp = newFileParsingOutput();
    if (!isMapping(raw)) {
        errors.push({
            path,
            message:
                "expected a file_parsing output (output_column, command, ...)",
        });
        return fp;
    }
    checkKeys(
        raw,
        ["output_column", "command", "timeout_seconds", "qc"],
        path,
        errors,
    );
    fp.outputColumn = readName(raw, "output_column", path, errors);
    if (typeof raw.command === "string") {
        // serializeYAML() writes commands as `|` blocks, which end in exactly one newline.
        fp.command = raw.command.replace(/\n+$/, "");
    } else if (raw.command !== undefined && raw.command !== null) {
        errors.push({
            path: [...path, "command"],
            message: "must be a string",
        });
    }
    fp.timeoutSeconds = readNumberText(raw, "timeout_seconds", path, errors);
    fp.qc = qcFromConfig(raw.qc, [...path, "qc"], errors);
    return fp;
}

function columnFromConfig(raw, path, errors) {
    const col = newColumn();
    if (!isMapping(raw)) {
        errors.push({ path, message: "expected a column (input_column: ...)" });
        return col;
    }
    checkKeys(
        raw,
        ["input_column", "output_column", "output", "qc", "file_parsing"],
        path,
        errors,
    );
    col.inputColumn = readName(raw, "input_column", path, errors);
    if (raw.output !== undefined && typeof raw.output !== "boolean")
        errors.push({
            path: [...path, "output"],
            message: "must be true or false",
        });

    // The wizard's three column modes are exclusive, and buildConfig() drops
    // whatever belongs to the other modes -- so a combination is rejected
    // rather than loaded and then silently lost on download.
    if (raw.file_parsing !== undefined && raw.file_parsing !== null) {
        col.isFileParsing = true;
        for (const key of ["output_column", "qc"]) {
            const v = raw[key];
            if (
                v !== undefined &&
                v !== null &&
                !(Array.isArray(v) && !v.length)
            )
                errors.push({
                    path: [...path, key],
                    message: `can't be combined with file_parsing`,
                });
        }
        if (raw.output === false)
            errors.push({
                path: [...path, "output"],
                message: "can't be combined with file_parsing",
            });
        col.fileParsing = listAt(
            raw.file_parsing,
            [...path, "file_parsing"],
            errors,
            "outputs",
        ).map((fp, i) =>
            fileParsingOutputFromConfig(
                fp,
                [...path, "file_parsing", i],
                errors,
            ),
        );
        return col;
    }

    col.output = raw.output !== false;
    col.outputColumn = readName(raw, "output_column", path, errors);
    if (!col.output && col.outputColumn)
        errors.push({
            path: [...path, "output_column"],
            message: "can't be combined with output: false",
        });
    col.qc = qcFromConfig(raw.qc, [...path, "qc"], errors);
    return col;
}

function setQCMatchFromConfig(raw, match, path, errors) {
    if (raw === undefined || raw === null) return;
    if (!isMapping(raw)) {
        errors.push({
            path,
            message: "expected one of sample_pattern, sample_regex, or samples",
        });
        return;
    }
    checkKeys(raw, ["sample_pattern", "sample_regex", "samples"], path, errors);
    const present = ["sample_pattern", "sample_regex", "samples"].filter(
        (k) => raw[k] !== undefined && raw[k] !== null,
    );
    if (present.length > 1) {
        errors.push({ path, message: `set only one of ${present.join(", ")}` });
        return;
    }
    if (present[0] === "sample_pattern") {
        match.kind = "pattern";
        match.samplePattern = readName(raw, "sample_pattern", path, errors);
    } else if (present[0] === "sample_regex") {
        match.kind = "regex";
        match.sampleRegex = readName(raw, "sample_regex", path, errors);
    } else if (present[0] === "samples") {
        match.kind = "samples";
        const samplesPath = [...path, "samples"];
        // The form holds samples as one comma-separated string.
        const names = listAt(
            raw.samples,
            samplesPath,
            errors,
            "sample names",
        ).map((name, i) => {
            if (
                typeof name !== "string" ||
                !name.trim() ||
                name !== name.trim() ||
                /[,\n]/.test(name)
            ) {
                errors.push({
                    path: [...samplesPath, i],
                    message:
                        "sample names must be non-empty strings with no commas, newlines, or outer whitespace",
                });
            }
            return String(name);
        });
        match.samples = names.join(", ");
    }
}

function setQCRuleFromConfig(raw, path, errors) {
    const rule = newSetQCRule();
    if (!isMapping(raw)) {
        errors.push({
            path,
            message:
                "expected a set_qc rule (rule_name, match_samples, checks)",
        });
        return rule;
    }
    checkKeys(
        raw,
        ["rule_name", "match_samples", "checks", "include_sample_qc"],
        path,
        errors,
    );
    rule.ruleName = readName(raw, "rule_name", path, errors);
    setQCMatchFromConfig(
        raw.match_samples,
        rule.matchSamples,
        [...path, "match_samples"],
        errors,
    );
    if (raw.include_sample_qc === true) {
        rule.includeSampleQC = true;
    } else if (
        raw.include_sample_qc !== undefined &&
        raw.include_sample_qc !== false
    ) {
        errors.push({
            path: [...path, "include_sample_qc"],
            message: "must be true or false",
        });
    }
    rule.checks = listAt(raw.checks, [...path, "checks"], errors, "checks").map(
        (rawCheck, i) => {
            const checkPath = [...path, "checks", i];
            const check = newSetQCCheck();
            if (!isMapping(rawCheck)) {
                errors.push({
                    path: checkPath,
                    message: "expected a check (input_column, qc)",
                });
                return check;
            }
            checkKeys(rawCheck, ["input_column", "qc"], checkPath, errors);
            check.inputColumn = readName(
                rawCheck,
                "input_column",
                checkPath,
                errors,
            );
            check.qc = qcFromConfig(rawCheck.qc, [...checkPath, "qc"], errors);
            // A check with no qc has no "No QC" mode in the form; load it as an
            // empty list so buildConfig() reports it.
            if (check.qc.kind === "none") check.qc.kind = "list";
            return check;
        },
    );
    return rule;
}

/**
 * The inverse of buildConfig(): turns a parsed config.yaml back into wizard
 * state. Anything the wizard can't hold exactly (an unknown key, a value
 * type the form has no control for, a combination buildConfig() would
 * drop) is an error, so a config that loads always downloads unchanged.
 * Checks buildConfig() already makes (missing names, duplicates, ...) are
 * left to it and show up in the usual error list once loaded.
 *
 * Returns {columns, setQCRules, skipSampleQC, errors}; each error is {path, message},
 * where path is the keys/indexes leading to the offending node.
 */
export function stateFromConfig(obj) {
    const errors = [];
    const result = {
        columns: [],
        setQCRules: [],
        skipSampleQC: newSkipSampleQC(),
        errors,
    };
    if (obj === undefined || obj === null) {
        errors.push({ path: [], message: "the file is empty" });
        return result;
    }
    if (!isMapping(obj)) {
        errors.push({
            path: [],
            message:
                "expected a mapping with sample_qc:, set_qc:, and/or skip_sample_qc:",
        });
        return result;
    }
    checkKeys(
        obj,
        ["sample_qc", "columns", "set_qc", "skip_sample_qc"],
        [],
        errors,
    );
    // `columns` is limsport's old name for `sample_qc`, still accepted; the
    // download always writes `sample_qc`.
    if ("sample_qc" in obj && "columns" in obj) {
        errors.push({
            path: ["columns"],
            message:
                "columns is the old name for sample_qc; the file can't use both",
        });
    }
    const columnsKey = "sample_qc" in obj ? "sample_qc" : "columns";
    const rawColumns = obj[columnsKey];
    if (Array.isArray(rawColumns) && !rawColumns.length) {
        errors.push({
            path: [columnsKey],
            message: `${columnsKey}: [] is rejected by limsport; remove the key to keep every input column`,
        });
    }
    result.columns = listAt(rawColumns, [columnsKey], errors, "columns").map(
        (col, i) => columnFromConfig(col, [columnsKey, i], errors),
    );
    result.setQCRules = listAt(obj.set_qc, ["set_qc"], errors, "rules").map(
        (rule, i) => setQCRuleFromConfig(rule, ["set_qc", i], errors),
    );
    if (obj.skip_sample_qc !== undefined && obj.skip_sample_qc !== null) {
        result.skipSampleQC.enabled = true;
        setQCMatchFromConfig(
            obj.skip_sample_qc,
            result.skipSampleQC.match,
            ["skip_sample_qc"],
            errors,
        );
    }
    return result;
}

// ---------------------------------------------------------------------------
// YAML serialization
// ---------------------------------------------------------------------------

const QUOTE_NEEDED_RE = /^\s|\s$|^$|[:#{}\[\],&*!|>'"%@`\n\t<=]/;
const AMBIGUOUS_SCALAR_RE =
    /^(true|false|null|yes|no|on|off|~|-?\d+(\.\d+)?)$/i;

function yamlScalar(value) {
    if (typeof value === "number") return numberToText(value);
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
    if (typeof value === "number") return numberToText(value);
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
    const lines = [
        `${kInd}qc:`,
        `${contentInd}match_column: ${yamlScalar(qc.match_column)}`,
    ];
    const rulesInd = `${contentInd}  `;
    for (const block of ["cases", "pattern_cases", "regex_cases"]) {
        if (!qc[block]) continue;
        lines.push(`${contentInd}${block}:`);
        for (const [key, conditions] of Object.entries(qc[block])) {
            lines.push(`${rulesInd}${yamlKey(key)}:`);
            lines.push(renderConditionList(conditions, `${rulesInd}  `));
        }
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
    const bodyLines = body
        .split("\n")
        .map((l) => (l.length ? contentInd + l : ""));
    return `${kInd}command: |\n${bodyLines.join("\n")}`;
}

function renderFileParsingOutput(fp, dashInd) {
    const kInd = `${dashInd}  `;
    const lines = [
        `${dashInd}- output_column: ${yamlScalar(fp.output_column)}`,
    ];
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
    if (col.output_column)
        lines.push(`${kInd}output_column: ${yamlScalar(col.output_column)}`);
    if (col.output === false) lines.push(`${kInd}output: false`);
    if (col.qc !== undefined) lines.push(renderQC(col.qc, kInd));
    if (col.file_parsing) {
        lines.push(`${kInd}file_parsing:`);
        col.file_parsing.forEach((fp) =>
            lines.push(renderFileParsingOutput(fp, `${kInd}  `)),
        );
    }
    return lines.join("\n");
}

// sample_pattern/sample_regex/samples are all free-form strings intended as
// exact/substring/regex match targets against real sample names -- always
// quoted, same "unambiguously a string" reasoning as yamlKey/rule keys.
function renderSetQCMatch(match, kInd, key = "match_samples") {
    const contentInd = `${kInd}  `;
    if (match.sample_pattern !== undefined) {
        return `${kInd}${key}:\n${contentInd}sample_pattern: ${yamlKey(match.sample_pattern)}`;
    }
    if (match.sample_regex !== undefined) {
        return `${kInd}${key}:\n${contentInd}sample_regex: ${yamlKey(match.sample_regex)}`;
    }
    const samples = match.samples.map((s) => yamlKey(s)).join(", ");
    return `${kInd}${key}:\n${contentInd}samples: [${samples}]`;
}

function renderSetQCCheck(check, dashInd) {
    const kInd = `${dashInd}  `;
    const lines = [`${dashInd}- input_column: ${yamlScalar(check.input_column)}`];
    if (check.qc !== undefined) lines.push(renderQC(check.qc, kInd));
    return lines.join("\n");
}

function renderSetQCRule(rule, dashInd) {
    const kInd = `${dashInd}  `;
    const lines = [`${dashInd}- rule_name: ${yamlKey(rule.rule_name)}`];
    lines.push(renderSetQCMatch(rule.match_samples, kInd));
    if (rule.include_sample_qc) lines.push(`${kInd}include_sample_qc: true`);
    if (rule.checks) {
        lines.push(`${kInd}checks:`);
        rule.checks.forEach((check) =>
            lines.push(renderSetQCCheck(check, `${kInd}  `)),
        );
    }
    return lines.join("\n");
}

/** Serializes a plain ExportConfig-shaped object (as returned by buildConfig().plain) to a YAML string. */
export function serializeYAML(plainConfig) {
    const lines = [];
    // Omit `sample_qc:` entirely when there are none -- an explicit
    // `sample_qc: []` is always rejected by the real load_config(), even when
    // set_qc is configured; only omitting the key means "pass every input
    // column through unfiltered."
    if (plainConfig.sample_qc.length) {
        lines.push("sample_qc:");
        plainConfig.sample_qc.forEach((col, i) => {
            if (i > 0) lines.push("");
            lines.push(renderColumn(col, "  "));
        });
    }
    // Same order as the form's sections; each top-level key after the first
    // gets a blank line before it.
    if (plainConfig.set_qc && plainConfig.set_qc.length) {
        if (lines.length) lines.push("");
        lines.push("set_qc:");
        plainConfig.set_qc.forEach((rule, i) => {
            if (i > 0) lines.push("");
            lines.push(renderSetQCRule(rule, "  "));
        });
    }
    if (plainConfig.skip_sample_qc) {
        if (lines.length) lines.push("");
        lines.push(
            renderSetQCMatch(plainConfig.skip_sample_qc, "", "skip_sample_qc"),
        );
    }
    lines.push("");
    return lines.join("\n");
}
