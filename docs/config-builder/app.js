import {
    OPERATORS,
    STRING_OPERATORS,
    NO_VALUE_OPERATORS,
    NUMERIC_RE,
    newColumn,
    newCondition,
    newCase,
    newFileParsingOutput,
    newSetQCRule,
    newSetQCCheck,
    newSkipSampleQC,
    buildConfig,
    serializeYAML,
} from "./schema.js";
import { importConfigYAML } from "./importer.js";

const state = {
    columns: [],
    setQCRules: [],
    skipSampleQC: newSkipSampleQC(),
};

const columnsEl = document.getElementById("columns");
const setQCRulesEl = document.getElementById("set-qc-rules");
const skipSampleQCEl = document.getElementById("skip-sample-qc");
const addSkipSampleQCBtn = document.getElementById("add-skip-sample-qc-btn");
const previewEl = document.getElementById("preview");
const errorsEl = document.getElementById("errors");
const warningsEl = document.getElementById("warnings");
const fileParsingNoticeEl = document.getElementById("file-parsing-notice");
const downloadBtn = document.getElementById("download-btn");
const copyBtn = document.getElementById("copy-btn");
const copyStatusEl = document.getElementById("copy-status");
const importStatusEl = document.getElementById("import-status");
const openInputEl = document.getElementById("open-input");
const dropOverlayEl = document.getElementById("drop-overlay");

function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = value;
        else if (key.startsWith("on") && typeof value === "function") {
            node.addEventListener(key.slice(2), value);
        } else {
            node.setAttribute(key, value);
        }
    }
    for (const child of children) node.appendChild(child);
    return node;
}

function labeledField(labelText, inputEl) {
    const label = el("label", { class: "field" }, [
        el("span", { class: "field-label", text: labelText }),
        inputEl,
    ]);
    return label;
}

// Double-clicking a card or section title toggles it like its collapse
// button; double-clicks on the header's own controls are left alone.
function toggleOnDoubleClick(header, collapseBtn) {
    header.addEventListener("dblclick", (event) => {
        if (event.target.closest("button, input, select, textarea, .drag-handle"))
            return;
        collapseBtn.click();
    });
}

// The browser draws window.confirm() itself, pinned to the top of the
// window; a modal <dialog> is centered by default. It's appended inside the
// builder's root element so it picks up the builder's CSS variables.
function confirmDialog(message, confirmLabel) {
    const confirmBtn = el("button", {
        type: "button",
        class: "dialog-confirm",
        text: confirmLabel,
    });
    const cancelBtn = el("button", { type: "button", text: "Cancel" });
    const dialog = el("dialog", { class: "confirm-dialog" }, [
        el("p", { text: message }),
        el("div", { class: "dialog-actions" }, [cancelBtn, confirmBtn]),
    ]);
    document.querySelector(".limsport-config-builder").appendChild(dialog);
    return new Promise((resolve) => {
        confirmBtn.addEventListener("click", () => dialog.close("confirm"));
        cancelBtn.addEventListener("click", () => dialog.close());
        // Also fires on Escape, with an empty returnValue.
        dialog.addEventListener("close", () => {
            dialog.remove();
            resolve(dialog.returnValue === "confirm");
        });
        dialog.showModal();
    });
}

function currentConfig() {
    return buildConfig(state.columns, state.setQCRules, state.skipSampleQC);
}

function refreshPreview() {
    const { plain, errors, warnings } = currentConfig();
    previewEl.textContent = serializeYAML(plain);

    // warnings never disable download: the config is valid, it just may
    // fail at run time
    warningsEl.replaceChildren();
    warningsEl.classList.toggle("hidden", !warnings.length);
    if (warnings.length) {
        const list = el("ul", { class: "errors-list" });
        for (const warning of warnings)
            list.appendChild(el("li", { text: warning }));
        warningsEl.append(
            el("p", { class: "warnings-heading", text: "Warning:" }),
            list,
        );
    }

    errorsEl.innerHTML = "";
    if (errors.length) {
        errorsEl.classList.remove("hidden");
        const heading = el("p", {
            class: "errors-heading",
            text: `${errors.length} issue(s) to fix before this config is valid:`,
        });
        const list = el("ul", { class: "errors-list" });
        for (const err of errors) list.appendChild(el("li", { text: err }));
        errorsEl.append(heading, list);
    } else {
        errorsEl.classList.add("hidden");
    }

    const hasFileParsing = state.columns.some((c) => c.isFileParsing);
    fileParsingNoticeEl.classList.toggle("hidden", !hasFileParsing);

    const ok =
        errors.length === 0 &&
        (state.columns.length > 0 || state.setQCRules.length > 0);
    downloadBtn.disabled = !ok;
    copyBtn.disabled = !ok;
}

// ---------------------------------------------------------------------------
// Condition list editor (used for plain qc lists, rule condition lists, and
// default condition lists -- always the same three fields per row).
// ---------------------------------------------------------------------------

function buildConditionsEditor(conditions) {
    const container = el("div", { class: "conditions" });

    function renderRow(cond) {
        const operatorSelect = el("select", { class: "cond-operator" });
        for (const op of OPERATORS) {
            operatorSelect.appendChild(el("option", { value: op, text: op }));
        }
        operatorSelect.value = cond.operator;

        const valuePlaceholders = {
            "=": "e.g. PASS or 1000",
            contains: "e.g. Escherichia",
            does_not_contain: "e.g. contaminant",
        };

        const valueInput = el("input", {
            type: "text",
            class: "cond-value",
            placeholder: valuePlaceholders[cond.operator] ?? "e.g. 1000",
            value: cond.value,
        });

        const toleranceInput = el("input", {
            type: "text",
            class: "cond-tolerance",
            placeholder: "tolerance %",
            value: cond.tolerancePercent,
        });

        const forceStringInput = el("input", { type: "checkbox" });
        const forceStringLabel = el("label", { class: "cond-force-string" }, [
            forceStringInput,
            document.createTextNode(" treat as string"),
        ]);
        forceStringInput.checked = cond.forceString;

        const caseInsensitiveInput = el("input", { type: "checkbox" });
        const caseInsensitiveLabel = el(
            "label",
            { class: "cond-case-insensitive" },
            [caseInsensitiveInput, document.createTextNode(" ignore case")],
        );
        caseInsensitiveInput.checked = cond.caseInsensitive;

        // A "=" value with any non-numeric character is unambiguously a string
        // already -- no need to ask. "Treat as string" only matters for a
        // numeric-looking (or blank) value, where auto-detection is ambiguous.
        function equalityValueIsExplicitString() {
            const raw = (valueInput.value ?? "").toString().trim();
            return raw !== "" && !NUMERIC_RE.test(raw);
        }

        // "=" only treats its value as a string worth ignoring case on once
        // it's a string -- either because it's already non-numeric, or because
        // "treat as string" is checked. contains/does_not_contain always compare
        // strings, so they don't need either gate.
        function forceStringApplies() {
            return (
                operatorSelect.value === "=" && !equalityValueIsExplicitString()
            );
        }
        function caseInsensitiveApplies() {
            if (operatorSelect.value === "=") {
                return (
                    equalityValueIsExplicitString() || forceStringInput.checked
                );
            }
            return STRING_OPERATORS.has(operatorSelect.value);
        }

        function syncOperatorDependentUI() {
            valueInput.classList.toggle(
                "hidden",
                NO_VALUE_OPERATORS.has(operatorSelect.value),
            );
            toleranceInput.classList.toggle(
                "hidden",
                operatorSelect.value !== "~=",
            );
            forceStringLabel.classList.toggle("hidden", !forceStringApplies());
            caseInsensitiveLabel.classList.toggle(
                "hidden",
                !caseInsensitiveApplies(),
            );
            valueInput.placeholder =
                valuePlaceholders[operatorSelect.value] ?? "e.g. 1000";
        }
        syncOperatorDependentUI();

        operatorSelect.addEventListener("change", () => {
            cond.operator = operatorSelect.value;
            // Clear state tied to a now-hidden control -- otherwise a stale
            // leftover value (typed before switching operators) can trip
            // buildConfig's validation with no visible control left to fix it.
            if (NO_VALUE_OPERATORS.has(cond.operator)) {
                cond.value = "";
                valueInput.value = "";
            }
            if (cond.operator !== "~=") {
                // strict inequality
                cond.tolerancePercent = "";
                toleranceInput.value = "";
            }
            if (cond.operator !== "=") {
                cond.forceString = false;
                forceStringInput.checked = false;
            }
            if (!caseInsensitiveApplies()) {
                cond.caseInsensitive = false;
                caseInsensitiveInput.checked = false;
            }
            syncOperatorDependentUI();
            refreshPreview();
        });
        valueInput.addEventListener("input", () => {
            cond.value = valueInput.value;
            // The value itself (not just the operator) can flip whether "ignore
            // case" is currently valid -- e.g. typing a letter into a numeric-
            // looking "=" value makes it an explicit string.
            if (!caseInsensitiveApplies()) {
                cond.caseInsensitive = false;
                caseInsensitiveInput.checked = false;
            }
            syncOperatorDependentUI();
            refreshPreview();
        });
        toleranceInput.addEventListener("input", () => {
            cond.tolerancePercent = toleranceInput.value;
            refreshPreview();
        });
        forceStringInput.addEventListener("change", () => {
            cond.forceString = forceStringInput.checked;
            if (!caseInsensitiveApplies()) {
                cond.caseInsensitive = false;
                caseInsensitiveInput.checked = false;
            }
            syncOperatorDependentUI();
            refreshPreview();
        });
        caseInsensitiveInput.addEventListener("change", () => {
            cond.caseInsensitive = caseInsensitiveInput.checked;
            refreshPreview();
        });

        const removeBtn = el("button", {
            type: "button",
            class: "btn-icon",
            title: "Remove condition",
            text: "✕",
            onclick: () => {
                const idx = conditions.indexOf(cond);
                if (idx >= 0) conditions.splice(idx, 1);
                row.remove();
                refreshPreview();
            },
        });

        const row = el("div", { class: "condition-row" }, [
            operatorSelect,
            valueInput,
            toleranceInput,
            forceStringLabel,
            caseInsensitiveLabel,
            removeBtn,
        ]);
        return row;
    }

    for (const cond of conditions) container.appendChild(renderRow(cond));

    const addBtn = el("button", {
        type: "button",
        class: "btn-add",
        text: "+ Add condition",
        onclick: () => {
            const cond = newCondition();
            conditions.push(cond);
            container.insertBefore(renderRow(cond), addBtn);
            refreshPreview();
        },
    });
    container.appendChild(addBtn);

    return container;
}

// ---------------------------------------------------------------------------
// QC editor: none / fixed list / conditional (match + rules + optional default)
// ---------------------------------------------------------------------------

// allowNone is false for a set-level check, which always has to check something.
function buildQCEditor(qc, { allowNone = true } = {}) {
    const container = el("div", { class: "qc-editor" });
    const kindSelect = el("select", { class: "qc-kind" }, [
        ...(allowNone
            ? [el("option", { value: "none", text: "No QC on this value" })]
            : []),
        el("option", { value: "list", text: "Fixed threshold(s)" }),
        el("option", {
            value: "conditional",
            text: "Threshold depends on another column",
        }),
    ]);
    kindSelect.value = qc.kind;

    const body = el("div", { class: "qc-body" });

    function renderBody() {
        body.innerHTML = "";
        if (qc.kind === "list") {
            body.appendChild(buildConditionsEditor(qc.conditions));
        } else if (qc.kind === "conditional") {
            body.appendChild(buildConditionalQCEditor(qc));
        }
    }

    kindSelect.addEventListener("change", () => {
        qc.kind = kindSelect.value;
        if (qc.kind === "list" && qc.conditions.length === 0)
            qc.conditions.push(newCondition());
        if (qc.kind === "conditional" && qc.cases.length === 0)
            qc.cases.push(newCase());
        renderBody();
        refreshPreview();
    });

    renderBody();
    container.append(kindSelect, body);
    return container;
}

function buildConditionalQCEditor(qc) {
    const container = el("div", { class: "conditional-qc" });

    const matchInput = el("input", {
        type: "text",
        placeholder:
            "column name whose value picks the rule, e.g. predicted_taxon",
        value: qc.matchColumn,
    });
    matchInput.addEventListener("input", () => {
        qc.matchColumn = matchInput.value;
        refreshPreview();
    });

    const casesContainer = el("div", { class: "rules" });

    const keyPlaceholders = {
        exact: "e.g. Escherichia coli",
        pattern: "e.g. Shigella",
        regex: "e.g. ^Salmonella",
    };

    // Same stacked select-then-input layout as a set-level rule's
    // "Which sample(s) does this apply to?" matcher.
    function renderCase(qcCase) {
        const matchTypeSelect = el("select", { class: "case-match-type" }, [
            el("option", { value: "exact", text: "Value equals" }),
            el("option", { value: "pattern", text: "Value contains" }),
            el("option", { value: "regex", text: "Value matches a regex" }),
        ]);
        matchTypeSelect.value = qcCase.matchType;

        const keyInput = el("input", {
            type: "text",
            placeholder: keyPlaceholders[qcCase.matchType],
            value: qcCase.key,
        });
        keyInput.addEventListener("input", () => {
            qcCase.key = keyInput.value;
            refreshPreview();
        });
        matchTypeSelect.addEventListener("change", () => {
            qcCase.matchType = matchTypeSelect.value;
            keyInput.placeholder = keyPlaceholders[qcCase.matchType];
            refreshPreview();
        });

        const removeBtn = el("button", {
            type: "button",
            class: "btn-icon",
            title: "Remove case",
            text: "✕",
            onclick: () => {
                const idx = qc.cases.indexOf(qcCase);
                if (idx >= 0) qc.cases.splice(idx, 1);
                caseBlock.remove();
                refreshPreview();
            },
        });

        const matchField = labeledField(
            "Which match column value(s) does this case apply to?",
            el("div", { class: "set-qc-match" }, [matchTypeSelect, keyInput]),
        );
        matchField.classList.add("rule-key");
        const header = el("div", { class: "rule-header case-header" }, [
            matchField,
            removeBtn,
        ]);
        const conditionsEditor = buildConditionsEditor(qcCase.conditions);
        const caseBlock = el("div", { class: "rule-block" }, [
            header,
            conditionsEditor,
        ]);
        return caseBlock;
    }

    for (const qcCase of qc.cases)
        casesContainer.appendChild(renderCase(qcCase));

    const addCaseBtn = el("button", {
        type: "button",
        class: "btn-add",
        text: "+ Add case",
        onclick: () => {
            const qcCase = newCase();
            qc.cases.push(qcCase);
            casesContainer.insertBefore(renderCase(qcCase), addCaseBtn);
            refreshPreview();
        },
    });
    casesContainer.appendChild(addCaseBtn);

    const defaultToggle = el("label", { class: "checkbox-field" });
    const defaultCheckbox = el("input", { type: "checkbox" });
    defaultCheckbox.checked = qc.useDefault;
    defaultToggle.append(
        defaultCheckbox,
        document.createTextNode("Add a default rule"),
    );

    const defaultBody = el("div", { class: "default-body" });
    function renderDefaultBody() {
        defaultBody.innerHTML = "";
        defaultBody.classList.toggle("hidden", !qc.useDefault);
        if (qc.useDefault) {
            if (qc.default.length === 0) qc.default.push(newCondition());
            defaultBody.appendChild(buildConditionsEditor(qc.default));
        }
    }
    defaultCheckbox.addEventListener("change", () => {
        qc.useDefault = defaultCheckbox.checked;
        renderDefaultBody();
        refreshPreview();
    });
    renderDefaultBody();

    const hint = el("p", {
        class: "hint",
        text: "A value with no matches is considered a QC fail without a default rule",
    });

    container.append(
        labeledField("Match column", matchInput),
        casesContainer,
        hint,
        defaultToggle,
        defaultBody,
    );
    return container;
}

// ---------------------------------------------------------------------------
// file_parsing editor: a list of named outputs, each with its own command,
// optional timeout, and its own QC editor.
// ---------------------------------------------------------------------------

function buildFileParsingEditor(column) {
    const container = el("div", { class: "file-parsing" });

    function renderOutput(fp) {
        const summarySpan = el("span", { class: "output-summary" });
        function updateSummary() {
            summarySpan.textContent = fp.outputColumn.trim() || "( )";
        }

        const nameInput = el("input", {
            type: "text",
            placeholder: "output column name, e.g. mean_depth",
            value: fp.outputColumn,
        });
        nameInput.addEventListener("input", () => {
            fp.outputColumn = nameInput.value;
            updateSummary();
            refreshPreview();
        });

        const commandInput = el("textarea", {
            rows: "3",
            placeholder:
                'shell command; the file to parse is available as $FILE, e.g.\ncut -d, -f2 "$FILE"',
            text: fp.command,
        });
        commandInput.addEventListener("input", () => {
            fp.command = commandInput.value;
            refreshPreview();
        });

        const timeoutInput = el("input", {
            type: "text",
            placeholder: "optional timeout (seconds)",
            value: fp.timeoutSeconds,
        });
        timeoutInput.addEventListener("input", () => {
            fp.timeoutSeconds = timeoutInput.value;
            refreshPreview();
        });

        const removeBtn = el("button", {
            type: "button",
            class: "btn-remove",
            text: "Remove output",
            onclick: () => {
                const idx = column.fileParsing.indexOf(fp);
                if (idx >= 0) column.fileParsing.splice(idx, 1);
                block.remove();
                refreshPreview();
            },
        });

        const qcEditor = buildQCEditor(fp.qc);

        const body = el("div", { class: "output-body" }, [
            labeledField("Output column name", nameInput),
            labeledField(
                "Command (must print a single-line result)",
                commandInput,
            ),
            labeledField("Command timeout duration (seconds)", timeoutInput),
            qcEditor,
        ]);

        let collapsed = false;
        const chevron = el("span", { class: "chevron" });
        const collapseBtn = el(
            "button",
            {
                type: "button",
                class: "btn-collapse",
                title: "Collapse this output",
                onclick: () => {
                    collapsed = !collapsed;
                    body.classList.toggle("hidden", collapsed);
                    block.classList.toggle("collapsed", collapsed);
                    collapseBtn.title = collapsed
                        ? "Expand this output"
                        : "Collapse this output";
                },
            },
            [chevron],
        );

        const block = el("div", { class: "file-parsing-output" }, [
            el("div", { class: "output-header" }, [
                el("div", { class: "output-header-title" }, [
                    el("strong", { text: "Output" }),
                    summarySpan,
                ]),
                el("div", { class: "output-header-actions" }, [
                    collapseBtn,
                    removeBtn,
                ]),
            ]),
            body,
        ]);
        toggleOnDoubleClick(block.querySelector(".output-header"), collapseBtn);
        updateSummary();
        return block;
    }

    for (const fp of column.fileParsing)
        container.appendChild(renderOutput(fp));

    const addBtn = el("button", {
        type: "button",
        class: "btn-add",
        text: "+ Add another output from this file",
        onclick: () => {
            const fp = newFileParsingOutput();
            column.fileParsing.push(fp);
            container.insertBefore(renderOutput(fp), addBtn);
            refreshPreview();
        },
    });
    container.appendChild(addBtn);

    return container;
}

// ---------------------------------------------------------------------------
// Set-level QC rule card: which sample(s) it applies to (one of three
// matcher kinds), which column to check, and its own QC condition list.
// ---------------------------------------------------------------------------

function buildSetQCMatchEditor(match) {
    const kindSelect = el("select", {}, [
        el("option", { value: "pattern", text: "Sample name contains" }),
        el("option", { value: "regex", text: "Sample name matches a regex" }),
        el("option", { value: "samples", text: "Specific sample name(s)" }),
    ]);
    kindSelect.value = match.kind;

    const patternInput = el("input", {
        type: "text",
        placeholder: "e.g. NTC",
        value: match.samplePattern,
    });
    patternInput.addEventListener("input", () => {
        match.samplePattern = patternInput.value;
        refreshPreview();
    });

    const regexInput = el("input", {
        type: "text",
        placeholder: "e.g. ^NTC-?\\d*$",
        value: match.sampleRegex,
    });
    regexInput.addEventListener("input", () => {
        match.sampleRegex = regexInput.value;
        refreshPreview();
    });

    const samplesInput = el("input", {
        type: "text",
        placeholder: "comma-separated exact names, e.g. NTC1, NTC2",
        value: match.samples,
    });
    samplesInput.addEventListener("input", () => {
        match.samples = samplesInput.value;
        refreshPreview();
    });

    function syncKind() {
        patternInput.classList.toggle("hidden", match.kind !== "pattern");
        regexInput.classList.toggle("hidden", match.kind !== "regex");
        samplesInput.classList.toggle("hidden", match.kind !== "samples");
    }
    syncKind();

    kindSelect.addEventListener("change", () => {
        match.kind = kindSelect.value;
        syncKind();
        refreshPreview();
    });

    return el("div", { class: "set-qc-match" }, [
        kindSelect,
        patternInput,
        regexInput,
        samplesInput,
    ]);
}

// A rule's `columns`: one or more {column, qc} checks, all read from the
// same matched sample(s) -- same nested-list shape/CSS as the conditional-qc
// editor's rule list (.rules/.rule-block/.rule-header/.rule-key), reused
// as-is since the pattern ("a labeled key input + remove button, then a
// condition list, in a grouped-overlap list") is identical.
function buildSetQCChecksEditor(rule) {
    const container = el("div", { class: "rules" });

    function renderCheck(check) {
        const columnInput = el("input", {
            type: "text",
            class: "rule-key",
            placeholder: "column to check, e.g. reads",
            value: check.inputColumn,
        });
        columnInput.addEventListener("input", () => {
            check.inputColumn = columnInput.value;
            refreshPreview();
        });

        const removeBtn = el("button", {
            type: "button",
            class: "btn-icon",
            title: "Remove this column check",
            text: "✕",
            onclick: () => {
                const idx = rule.checks.indexOf(check);
                if (idx >= 0) rule.checks.splice(idx, 1);
                block.remove();
                refreshPreview();
            },
        });

        const header = el("div", { class: "rule-header" }, [
            columnInput,
            removeBtn,
        ]);
        const qcEditor = buildQCEditor(check.qc, { allowNone: false });
        const block = el("div", { class: "rule-block" }, [header, qcEditor]);
        return block;
    }

    for (const check of rule.checks) container.appendChild(renderCheck(check));

    const addBtn = el("button", {
        type: "button",
        class: "btn-add",
        text: "+ Add another column to check",
        onclick: () => {
            const check = newSetQCCheck();
            rule.checks.push(check);
            container.insertBefore(renderCheck(check), addBtn);
            refreshPreview();
        },
    });
    container.appendChild(addBtn);

    return container;
}

function buildSetQCRuleCard(rule) {
    const summarySpan = el("span", { class: "column-summary" });
    function updateSummary() {
        summarySpan.textContent = rule.ruleName.trim() || "( )";
    }

    const nameInput = el("input", {
        type: "text",
        placeholder: "e.g. NTC read count",
        value: rule.ruleName,
    });
    nameInput.addEventListener("input", () => {
        rule.ruleName = nameInput.value;
        updateSummary();
        refreshPreview();
    });

    const matchEditor = buildSetQCMatchEditor(rule.matchSamples);

    const includeSampleQCCheckbox = el("input", { type: "checkbox" });
    includeSampleQCCheckbox.checked = rule.includeSampleQC;
    includeSampleQCCheckbox.addEventListener("change", () => {
        rule.includeSampleQC = includeSampleQCCheckbox.checked;
        refreshPreview();
    });
    const includeSampleQCToggle = el("label", { class: "checkbox-field" }, [
        includeSampleQCCheckbox,
        document.createTextNode(
            "Include all sample-level QC checks in this rule",
        ),
    ]);

    const checksEditor = buildSetQCChecksEditor(rule);
    const checksSection = el("div", { class: "section" }, [
        el("h4", { text: "Columns to check" }),
        checksEditor,
    ]);

    const removeBtn = el("button", {
        type: "button",
        class: "btn-remove",
        text: "Remove rule",
        onclick: () => {
            const idx = state.setQCRules.indexOf(rule);
            if (idx >= 0) state.setQCRules.splice(idx, 1);
            card.remove();
            refreshPreview();
        },
    });

    const body = el("div", { class: "column-body" }, [
        labeledField("Rule name", nameInput),
        labeledField("Which sample(s) does this apply to?", matchEditor),
        includeSampleQCToggle,
        checksSection,
    ]);

    let collapsed = false;
    const chevron = el("span", { class: "chevron" });
    const collapseBtn = el(
        "button",
        {
            type: "button",
            class: "btn-collapse",
            title: "Collapse this rule",
            onclick: () => {
                collapsed = !collapsed;
                body.classList.toggle("hidden", collapsed);
                card.classList.toggle("collapsed", collapsed);
                collapseBtn.title = collapsed
                    ? "Expand this rule"
                    : "Collapse this rule";
            },
        },
        [chevron],
    );

    const card = el("div", { class: "set-qc-rule-card" });
    card.append(
        el("div", { class: "column-header" }, [
            el("div", { class: "column-header-title" }, [
                dragHandle(card, rule),
                el("strong", { text: "Set-level QC rule" }),
                summarySpan,
            ]),
            el("div", { class: "column-header-actions" }, [
                collapseBtn,
                removeBtn,
            ]),
        ]),
        body,
    );
    toggleOnDoubleClick(card.querySelector(".column-header"), collapseBtn);
    updateSummary();
    return card;
}

// ---------------------------------------------------------------------------
// Skip sample-level QC: at most one card (the config takes one matcher),
// using the same sample matcher a set-level rule uses.
// ---------------------------------------------------------------------------

function renderSkipSampleQC() {
    const skip = state.skipSampleQC;
    addSkipSampleQCBtn.classList.toggle("hidden", skip.enabled);
    if (!skip.enabled) {
        skipSampleQCEl.replaceChildren();
        return;
    }
    const removeBtn = el("button", {
        type: "button",
        class: "btn-remove",
        text: "Remove",
        onclick: () => {
            skip.enabled = false;
            renderSkipSampleQC();
            refreshPreview();
        },
    });
    const card = el("div", { class: "set-qc-rule-card" }, [
        el("div", { class: "column-header" }, [
            el("div", { class: "column-header-title" }, [
                el("strong", { text: "Skip sample-level QC" }),
            ]),
            el("div", { class: "column-header-actions" }, [removeBtn]),
        ]),
        el("div", { class: "column-body" }, [
            labeledField(
                "Which sample(s) skip sample-level QC?",
                buildSetQCMatchEditor(skip.match),
            ),
        ]),
    ]);
    skipSampleQCEl.replaceChildren(card);
}

// ---------------------------------------------------------------------------
// Drag-to-reorder for column and set-level rule cards. A card is only
// draggable while its handle is held, so text in its inputs can still be
// selected with the mouse. The state array is re-read from the DOM order
// when the drag ends.
// ---------------------------------------------------------------------------

const cardItems = new WeakMap(); // card -> the state object it renders
const containerLists = new WeakMap(); // card container -> its state array
let draggedCard = null;

function dragHandle(card, item) {
    cardItems.set(card, item);
    const handle = el("span", {
        class: "drag-handle",
        title: "Drag to reorder",
        text: "⠿",
    });
    handle.addEventListener("mousedown", () => (card.draggable = true));
    handle.addEventListener("mouseup", () => (card.draggable = false));
    card.addEventListener("dragstart", (event) => {
        draggedCard = card;
        event.dataTransfer.effectAllowed = "move";
        // Firefox won't start a drag without some data set.
        event.dataTransfer.setData("text/plain", "");
        card.classList.add("dragging");
    });
    card.addEventListener("dragend", () => {
        card.draggable = false;
        card.classList.remove("dragging");
        draggedCard = null;
        const container = card.parentElement;
        const list = containerLists.get(container);
        if (!list) return;
        const order = [...container.children].map((c) => cardItems.get(c));
        list.splice(0, list.length, ...order);
        refreshPreview();
    });
    return handle;
}

function enableReorder(container, list) {
    containerLists.set(container, list);
    container.addEventListener("dragover", (event) => {
        if (!draggedCard || draggedCard.parentElement !== container) return;
        event.preventDefault();
        const before = [...container.children].find((child) => {
            if (child === draggedCard) return false;
            const box = child.getBoundingClientRect();
            return event.clientY < box.top + box.height / 2;
        });
        container.insertBefore(draggedCard, before ?? null);
    });
    container.addEventListener("drop", (event) => {
        if (draggedCard?.parentElement === container) event.preventDefault();
    });
}
enableReorder(columnsEl, state.columns);
enableReorder(setQCRulesEl, state.setQCRules);

// Collapses a whole form section (its header button hides its body).
for (const section of document.querySelectorAll(".form-section")) {
    const button = section.querySelector(".section-header .btn-collapse");
    const body = section.querySelector(".section-body");
    toggleOnDoubleClick(section.querySelector(".section-header"), button);
    button.addEventListener("click", () => {
        const collapsed = !section.classList.contains("collapsed");
        section.classList.toggle("collapsed", collapsed);
        body.classList.toggle("hidden", collapsed);
        button.title = collapsed
            ? "Expand this section"
            : "Collapse this section";
    });
}

function addSetQCRule(rule) {
    state.setQCRules.push(rule);
    setQCRulesEl.appendChild(buildSetQCRuleCard(rule));
    refreshPreview();
}

// ---------------------------------------------------------------------------
// Column card
// ---------------------------------------------------------------------------

function buildColumnCard(col) {
    const summarySpan = el("span", { class: "column-summary" });
    function updateSummary() {
        const label = col.inputColumn.trim() || "( )";
        const bits = [];
        if (col.isFileParsing) bits.push("file parsing");
        else if (col.output === false)
            bits.push("QC only, excluded from output");
        else if (col.outputColumn.trim())
            bits.push(`→ ${col.outputColumn.trim()}`);
        summarySpan.textContent = bits.length
            ? `${label} (${bits.join(", ")})`
            : label;
    }

    const nameInput = el("input", {
        type: "text",
        placeholder: "column name as it appears in your input table",
        value: col.inputColumn,
    });
    nameInput.addEventListener("input", () => {
        col.inputColumn = nameInput.value;
        updateSummary();
        refreshPreview();
    });

    const outputColumnInput = el("input", {
        type: "text",
        placeholder: "optional new name for the output",
        value: col.outputColumn,
    });
    outputColumnInput.addEventListener("input", () => {
        col.outputColumn = outputColumnInput.value;
        updateSummary();
        refreshPreview();
    });
    const outputColumnField = labeledField(
        "Output column name (optional)",
        outputColumnInput,
    );

    // isFileParsing and output===false are mutually exclusive (same rule as
    // output_column/qc vs. file_parsing, config.py's model_validator) --
    // really one three-way mode, not two independent booleans. A single
    // select above the fields it controls means picking a mode only ever
    // reveals/hides what's below it, never yanks away a field above it.
    const modeSelect = el("select", {}, [
        el("option", {
            value: "keep",
            text: "Add to the output file",
        }),
        el("option", {
            value: "exclude",
            text: "Use for QC only",
        }),
        el("option", {
            value: "fileparsing",
            text: "Extract value(s) from a file",
        }),
    ]);
    function modeFor() {
        if (col.isFileParsing) return "fileparsing";
        if (col.output === false) return "exclude";
        return "keep";
    }
    modeSelect.value = modeFor();
    const modeField = labeledField(
        "What should happen to this column?",
        modeSelect,
    );

    const qcEditor = buildQCEditor(col.qc);
    const qcSection = el("div", { class: "section" }, [
        el("h4", { text: "QC" }),
        qcEditor,
    ]);

    const fileParsingEditor = buildFileParsingEditor(col);
    const fileParsingSection = el("div", { class: "section" }, [
        el("h4", { text: "File parsing outputs" }),
        fileParsingEditor,
    ]);

    function syncMode() {
        outputColumnField.classList.toggle(
            "hidden",
            col.isFileParsing || col.output === false,
        );
        qcSection.classList.toggle("hidden", col.isFileParsing);
        fileParsingSection.classList.toggle("hidden", !col.isFileParsing);
        updateSummary();
    }
    syncMode();

    modeSelect.addEventListener("change", () => {
        const mode = modeSelect.value;
        col.isFileParsing = mode === "fileparsing";
        col.output = mode === "exclude" ? false : true;
        if (col.isFileParsing && col.fileParsing.length === 0)
            col.fileParsing.push(newFileParsingOutput());
        syncMode();
        refreshPreview();
    });

    const removeBtn = el("button", {
        type: "button",
        class: "btn-remove",
        text: "Remove column",
        onclick: () => {
            const idx = state.columns.indexOf(col);
            if (idx >= 0) state.columns.splice(idx, 1);
            card.remove();
            refreshPreview();
        },
    });

    const body = el("div", { class: "column-body" }, [
        labeledField("Source column name", nameInput),
        modeField,
        outputColumnField,
        qcSection,
        fileParsingSection,
    ]);

    let collapsed = false;
    const chevron = el("span", { class: "chevron" });
    const collapseBtn = el(
        "button",
        {
            type: "button",
            class: "btn-collapse",
            title: "Collapse this column",
            onclick: () => {
                collapsed = !collapsed;
                body.classList.toggle("hidden", collapsed);
                card.classList.toggle("collapsed", collapsed);
                collapseBtn.title = collapsed
                    ? "Expand this column"
                    : "Collapse this column";
            },
        },
        [chevron],
    );

    const card = el("div", { class: "column-card" });
    card.append(
        el("div", { class: "column-header" }, [
            el("div", { class: "column-header-title" }, [
                dragHandle(card, col),
                el("strong", { text: "Column" }),
                summarySpan,
            ]),
            el("div", { class: "column-header-actions" }, [
                collapseBtn,
                removeBtn,
            ]),
        ]),
        body,
    );
    // :scope > -- not a nested file_parsing output's header
    toggleOnDoubleClick(card.querySelector(":scope > .column-header"), collapseBtn);
    updateSummary();
    return card;
}

function addColumn(col) {
    state.columns.push(col);
    columnsEl.appendChild(buildColumnCard(col));
    refreshPreview();
}

function clearAll() {
    loadState([], [], newSkipSampleQC());
    importStatusEl.classList.add("hidden");
}

function loadState(columns, setQCRules, skipSampleQC) {
    state.columns.length = 0;
    state.setQCRules.length = 0;
    state.skipSampleQC = skipSampleQC;
    columnsEl.innerHTML = "";
    setQCRulesEl.innerHTML = "";
    columns.forEach(addColumn);
    setQCRules.forEach(addSetQCRule);
    renderSkipSampleQC();
    refreshPreview();
}

// ---------------------------------------------------------------------------
// Opening an existing config.yaml (file picker or drag-and-drop)
// ---------------------------------------------------------------------------

function showImportStatus(heading, messages, isError) {
    importStatusEl.innerHTML = "";
    importStatusEl.className = isError ? "errors" : "notice";
    importStatusEl.appendChild(
        el("p", { class: isError ? "errors-heading" : "", text: heading }),
    );
    if (messages.length) {
        const list = el("ul", { class: "errors-list" });
        for (const message of messages)
            list.appendChild(el("li", { text: message }));
        importStatusEl.appendChild(list);
    }
}

function formHasContent() {
    return (
        state.setQCRules.length > 0 ||
        state.skipSampleQC.enabled ||
        state.columns.some((c) => c.inputColumn.trim())
    );
}

async function openConfigFile(file) {
    let text;
    try {
        text = await file.text();
    } catch (err) {
        showImportStatus(
            `Couldn't read ${file.name}: ${err.message}`,
            [],
            true,
        );
        return;
    }
    const { columns, setQCRules, skipSampleQC, errors, warnings } =
        importConfigYAML(text);
    if (errors.length) {
        showImportStatus(`${file.name} was not loaded:`, errors, true);
        return;
    }
    if (
        formHasContent() &&
        !(await confirmDialog(
            `Replace everything in the form with ${file.name}?`,
            "Replace",
        ))
    )
        return;
    loadState(columns, setQCRules, skipSampleQC);
    showImportStatus(`Loaded ${file.name}.`, warnings, false);
}

// ---------------------------------------------------------------------------
// Top-level controls
// ---------------------------------------------------------------------------

document
    .getElementById("add-column-btn")
    .addEventListener("click", () => addColumn(newColumn()));
document
    .getElementById("add-set-qc-btn")
    .addEventListener("click", () => addSetQCRule(newSetQCRule()));
addSkipSampleQCBtn.addEventListener("click", () => {
    state.skipSampleQC.enabled = true;
    renderSkipSampleQC();
    refreshPreview();
});
document
    .getElementById("start-blank-btn")
    .addEventListener("click", async () => {
        if (
            (state.columns.length ||
                state.setQCRules.length ||
                state.skipSampleQC.enabled) &&
            !(await confirmDialog("Clear everything and start over?", "Clear"))
        )
            return;
        clearAll();
    });

document
    .getElementById("open-btn")
    .addEventListener("click", () => openInputEl.click());
openInputEl.addEventListener("change", () => {
    const [file] = openInputEl.files;
    // Reset so choosing the same file again still fires "change".
    openInputEl.value = "";
    if (file) openConfigFile(file);
});

// Without preventDefault() on both dragover and drop, the browser opens the
// dropped file in place of this page, discarding the form. dragenter/
// dragleave fire for every element crossed, hence the depth count.
let dragDepth = 0;
const isFileDrag = (event) => event.dataTransfer?.types.includes("Files");
document.addEventListener("dragenter", (event) => {
    if (!isFileDrag(event)) return;
    dragDepth += 1;
    dropOverlayEl.classList.remove("hidden");
});
document.addEventListener("dragleave", (event) => {
    if (!isFileDrag(event)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) dropOverlayEl.classList.add("hidden");
});
document.addEventListener("dragover", (event) => {
    if (isFileDrag(event)) event.preventDefault();
});
document.addEventListener("drop", (event) => {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    dragDepth = 0;
    dropOverlayEl.classList.add("hidden");
    const [file] = event.dataTransfer.files;
    if (file) openConfigFile(file);
});

downloadBtn.addEventListener("click", () => {
    const { plain, errors } = currentConfig();
    if (errors.length) return;
    const blob = new Blob([serializeYAML(plain)], { type: "text/yaml" });
    const url = URL.createObjectURL(blob);
    const a = el("a", { href: url, download: "config.yaml" });
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
});

// navigator.clipboard only exists in a secure context (https://, or
// localhost/127.0.0.1) -- on a plain http:// origin like a Tailscale IP,
// it's undefined. Fall back to the older select+execCommand technique,
// which isn't restricted to secure contexts.
async function copyToClipboard(text) {
    if (window.isSecureContext && navigator.clipboard) {
        await navigator.clipboard.writeText(text);
        return;
    }
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    textarea.setSelectionRange(0, text.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(textarea);
    if (!ok) throw new Error("execCommand('copy') was unsuccessful");
}

copyBtn.addEventListener("click", async () => {
    const { plain, errors } = currentConfig();
    if (errors.length) return;
    try {
        await copyToClipboard(serializeYAML(plain));
        copyStatusEl.textContent = "Copied!";
    } catch (err) {
        copyStatusEl.textContent =
            "Copy failed! Select the text in the preview and copy it manually.";
    }
    setTimeout(() => (copyStatusEl.textContent = ""), 2500);
});

// Start with one blank column so the form isn't empty on first load.
renderSkipSampleQC();
addColumn(newColumn());
