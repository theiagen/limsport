/**
 * Reads an existing config.yaml into wizard state: parses it with the
 * vendored js-yaml, hands the result to stateFromConfig(), and turns each
 * error's path into a line number in the original text.
 */

import { load, YAMLException } from "./vendor/js-yaml.js";
import { newSkipSampleQC, stateFromConfig } from "./schema.js";

// Unquoted scalars that PyYAML (YAML 1.1, what limsport uses) and js-yaml
// (YAML 1.2) resolve differently, so the builder would re-emit them with a
// different meaning: yes/no/on/off are booleans only to PyYAML; exponents
// without a dot and signed exponent, 0o17, and leading zeros differ in
// type or value; 1:30 is a number (90) only to PyYAML.
const YAML11_BOOL_RE = /^(yes|no|on|off)$/i;
const YAML11_NUMBER_RE =
    /^[-+]?((\d[\d_]*(\.[\d_]*)?|\.[\d_]+)[eE][-+]?\d+|0o[0-7]+|0[\d_]+|\d[\d_]*(:[0-5]?\d)+(\.[\d_]*)?)$/;

/**
 * js-yaml has no source-position API, but its `listener` option fires an
 * open/close event around every node it composes. Nesting those events
 * rebuilds the node tree, with each node's 1-based starting line and raw
 * source text. A mapping's children alternate key, value.
 */
function parseWithLines(text) {
    const root = { children: [] };
    const stack = [root];
    const value = load(text, {
        listener(event, state) {
            if (event === "open") {
                const node = {
                    line: state.line + 1,
                    start: state.position,
                    children: [],
                };
                stack[stack.length - 1].children.push(node);
                stack.push(node);
            } else {
                const node = stack.pop();
                node.kind = state.kind;
                node.result = state.result;
                node.raw = text.slice(node.start, state.position);
            }
        },
    });
    return { value, tree: root.children[0] };
}

function walk(node, visit) {
    if (!node) return;
    visit(node);
    for (const child of node.children) walk(child, visit);
}

/** The line of the deepest node along `path` that exists in the file. */
function lineOf(tree, path) {
    let node = tree;
    let line = tree?.line ?? 1;
    for (const segment of path) {
        let next;
        if (node?.kind === "mapping") {
            for (let i = 0; i + 1 < node.children.length; i += 2) {
                if (String(node.children[i].result) === String(segment)) {
                    line = node.children[i].line;
                    next = node.children[i + 1];
                    break;
                }
            }
        } else if (node?.kind === "sequence") {
            next = node.children[segment];
            if (next) line = next.line;
        }
        if (!next) break;
        node = next;
    }
    return line;
}

function formatPath(path) {
    return path
        .map((segment, i) => {
            if (typeof segment === "number") return `[${segment}]`;
            if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(segment))
                return i ? `.${segment}` : segment;
            return `[${JSON.stringify(segment)}]`;
        })
        .join("");
}

// A "#" at the start of a line or after whitespace, outside a `|`/`>` block
// (where it's part of a command). A "#" after a space inside a quoted string
// also matches, which only costs a spurious warning.
function hasComments(text, tree) {
    const blockLines = new Set();
    walk(tree, (node) => {
        if (node.kind !== "scalar" || !/^\s*[|>]/.test(node.raw ?? "")) return;
        const span = node.raw.trimEnd().split("\n").length;
        for (let i = 0; i < span; i++) blockLines.add(node.line + i);
    });
    return text
        .split("\n")
        .some((line, i) => !blockLines.has(i + 1) && /(^|\s)#/.test(line));
}

/**
 * Parses config.yaml text into wizard state.
 *
 * Args:
 *   text: the file's contents.
 *
 * Returns:
 *   {columns, setQCRules, skipSampleQC, errors, warnings}. `errors` and `warnings` are
 *   display strings; when `errors` is non-empty, nothing should be loaded.
 */
export function importConfigYAML(text) {
    const empty = {
        columns: [],
        setQCRules: [],
        skipSampleQC: newSkipSampleQC(),
        warnings: [],
    };
    let parsed;
    try {
        parsed = parseWithLines(text);
    } catch (err) {
        if (!(err instanceof YAMLException)) throw err;
        const where = err.mark ? `Line ${err.mark.line + 1}: ` : "";
        return { ...empty, errors: [`${where}${err.reason}`] };
    }

    const located = [];
    walk(parsed.tree, (node) => {
        if (node.kind !== "scalar") return;
        const raw = (node.raw ?? "").trim();
        if (YAML11_BOOL_RE.test(raw)) {
            located.push({
                line: node.line,
                text: `unquoted "${raw}" is read as true/false by limsport; quote it if you mean the text`,
            });
        } else if (YAML11_NUMBER_RE.test(raw)) {
            located.push({
                line: node.line,
                text: `unquoted "${raw}" is read differently by limsport; write it as a plain decimal (e.g. 0.0000001), or quote it if it's text`,
            });
        }
    });

    const { columns, setQCRules, skipSampleQC, errors } = stateFromConfig(
        parsed.value,
    );
    for (const { path, message } of errors) {
        const where = path.length ? `${formatPath(path)}: ` : "";
        located.push({
            line: lineOf(parsed.tree, path),
            text: `${where}${message}`,
        });
    }
    if (located.length) {
        located.sort((a, b) => a.line - b.line);
        return {
            ...empty,
            errors: located.map((e) => `Line ${e.line}: ${e.text}`),
        };
    }

    const warnings = hasComments(text, parsed.tree)
        ? [
              "Comments in this file are not kept: the downloaded config is regenerated from the form.",
          ]
        : [];
    return { columns, setQCRules, skipSampleQC, errors: [], warnings };
}
