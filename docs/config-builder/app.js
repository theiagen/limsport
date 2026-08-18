import {
  OPERATORS,
  STRING_OPERATORS,
  NUMERIC_RE,
  newColumn,
  newCondition,
  newRule,
  newFileParsingOutput,
  buildConfig,
  serializeYAML,
} from "./schema.js";

const state = { columns: [] };

const columnsEl = document.getElementById("columns");
const previewEl = document.getElementById("preview");
const errorsEl = document.getElementById("errors");
const fileParsingNoticeEl = document.getElementById("file-parsing-notice");
const downloadBtn = document.getElementById("download-btn");
const copyBtn = document.getElementById("copy-btn");
const copyStatusEl = document.getElementById("copy-status");

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
  const label = el("label", { class: "field" }, [el("span", { class: "field-label", text: labelText }), inputEl]);
  return label;
}

function refreshPreview() {
  const { plain, errors } = buildConfig(state.columns);
  previewEl.textContent = serializeYAML(plain);

  errorsEl.innerHTML = "";
  if (errors.length) {
    errorsEl.classList.remove("hidden");
    const heading = el("p", { class: "errors-heading", text: `${errors.length} issue(s) to fix before this config is valid:` });
    const list = el("ul", { class: "errors-list" });
    for (const err of errors) list.appendChild(el("li", { text: err }));
    errorsEl.append(heading, list);
  } else {
    errorsEl.classList.add("hidden");
  }

  const hasFileParsing = state.columns.some((c) => c.isFileParsing);
  fileParsingNoticeEl.classList.toggle("hidden", !hasFileParsing);

  const ok = errors.length === 0 && state.columns.length > 0;
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
    const caseInsensitiveLabel = el("label", { class: "cond-case-insensitive" }, [
      caseInsensitiveInput,
      document.createTextNode(" ignore case"),
    ]);
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
      return operatorSelect.value === "=" && !equalityValueIsExplicitString();
    }
    function caseInsensitiveApplies() {
      if (operatorSelect.value === "=") {
        return equalityValueIsExplicitString() || forceStringInput.checked;
      }
      return STRING_OPERATORS.has(operatorSelect.value);
    }

    function syncOperatorDependentUI() {
      toleranceInput.classList.toggle("hidden", operatorSelect.value !== "~=");
      forceStringLabel.classList.toggle("hidden", !forceStringApplies());
      caseInsensitiveLabel.classList.toggle("hidden", !caseInsensitiveApplies());
      valueInput.placeholder = valuePlaceholders[operatorSelect.value] ?? "e.g. 1000";
    }
    syncOperatorDependentUI();

    operatorSelect.addEventListener("change", () => {
      cond.operator = operatorSelect.value;
      // Clear state tied to a now-hidden control -- otherwise a stale
      // leftover value (typed before switching operators) can trip
      // buildConfig's validation with no visible control left to fix it.
      if (cond.operator !== "~=") {  // strict inequality
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

function buildQCEditor(qc) {
  const container = el("div", { class: "qc-editor" });
  const kindSelect = el("select", { class: "qc-kind" }, [
    el("option", { value: "none", text: "No QC on this value" }),
    el("option", { value: "list", text: "Fixed threshold(s)" }),
    el("option", { value: "conditional", text: "Threshold depends on another column" }),
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
    if (qc.kind === "list" && qc.conditions.length === 0) qc.conditions.push(newCondition());
    if (qc.kind === "conditional" && qc.rules.length === 0) qc.rules.push(newRule());
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
    placeholder: "column name whose value picks the rule, e.g. predicted_taxon",
    value: qc.match,
  });
  matchInput.addEventListener("input", () => {
    qc.match = matchInput.value;
    refreshPreview();
  });

  const rulesContainer = el("div", { class: "rules" });

  function renderRule(rule) {
    const keyInput = el("input", {
      type: "text",
      class: "rule-key",
      placeholder: 'value to match, e.g. "Escherichia coli"',
      value: rule.key,
    });
    keyInput.addEventListener("input", () => {
      rule.key = keyInput.value;
      refreshPreview();
    });

    const removeBtn = el("button", {
      type: "button",
      class: "btn-icon",
      title: "Remove rule",
      text: "✕",
      onclick: () => {
        const idx = qc.rules.indexOf(rule);
        if (idx >= 0) qc.rules.splice(idx, 1);
        ruleBlock.remove();
        refreshPreview();
      },
    });

    const header = el("div", { class: "rule-header" }, [keyInput, removeBtn]);
    const conditionsEditor = buildConditionsEditor(rule.conditions);
    const ruleBlock = el("div", { class: "rule-block" }, [header, conditionsEditor]);
    return ruleBlock;
  }

  for (const rule of qc.rules) rulesContainer.appendChild(renderRule(rule));

  const addRuleBtn = el("button", {
    type: "button",
    class: "btn-add",
    text: "+ Add rule",
    onclick: () => {
      const rule = newRule();
      qc.rules.push(rule);
      rulesContainer.insertBefore(renderRule(rule), addRuleBtn);
      refreshPreview();
    },
  });
  rulesContainer.appendChild(addRuleBtn);

  const defaultToggle = el("label", { class: "checkbox-field" });
  const defaultCheckbox = el("input", { type: "checkbox" });
  defaultCheckbox.checked = qc.useDefault;
  defaultToggle.append(defaultCheckbox, document.createTextNode("Add a default rule for values that match none of the above"));

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
    text: "Without a default rule, a value with no matches is considered a QC fail",
  });

  container.append(
    labeledField("Match column", matchInput),
    rulesContainer,
    hint,
    defaultToggle,
    defaultBody
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
      summarySpan.textContent = fp.name.trim() || "( )";
    }

    const nameInput = el("input", { type: "text", placeholder: "output column name, e.g. mean_depth", value: fp.name });
    nameInput.addEventListener("input", () => {
      fp.name = nameInput.value;
      updateSummary();
      refreshPreview();
    });

    const commandInput = el("textarea", {
      rows: "3",
      placeholder: 'shell command; the file to parse is available as $LIMSPORT_FILE, e.g.\ncut -d, -f2 "$LIMSPORT_FILE"',
      text: fp.command,
    });
    commandInput.addEventListener("input", () => {
      fp.command = commandInput.value;
      refreshPreview();
    });

    const timeoutInput = el("input", { type: "text", placeholder: "optional timeout (seconds)", value: fp.timeoutSeconds });
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
      labeledField("Command (must print a single-line result)", commandInput),
      labeledField("Command timeout duration (seconds)", timeoutInput),
      qcEditor,
    ]);

    let collapsed = false;
    const chevron = el("span", { class: "chevron" });
    const collapseBtn = el("button", {
      type: "button",
      class: "btn-collapse",
      title: "Collapse this output",
      onclick: () => {
        collapsed = !collapsed;
        body.classList.toggle("hidden", collapsed);
        block.classList.toggle("collapsed", collapsed);
        collapseBtn.title = collapsed ? "Expand this output" : "Collapse this output";
      },
    }, [chevron]);

    const block = el("div", { class: "file-parsing-output" }, [
      el("div", { class: "output-header" }, [
        el("div", { class: "output-header-title" }, [el("strong", { text: "Output" }), summarySpan]),
        el("div", { class: "output-header-actions" }, [collapseBtn, removeBtn]),
      ]),
      body,
    ]);
    updateSummary();
    return block;
  }

  for (const fp of column.fileParsing) container.appendChild(renderOutput(fp));

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
// Column card
// ---------------------------------------------------------------------------

function buildColumnCard(col) {
  const summarySpan = el("span", { class: "column-summary" });
  function updateSummary() {
    const label = col.name.trim() || "( )";
    const bits = [];
    if (col.isFileParsing) bits.push("file parsing");
    else if (col.rename.trim()) bits.push(`→ ${col.rename.trim()}`);
    summarySpan.textContent = bits.length ? `${label} (${bits.join(", ")})` : label;
  }

  const nameInput = el("input", { type: "text", placeholder: "column name as it appears in your input table", value: col.name });
  nameInput.addEventListener("input", () => {
    col.name = nameInput.value;
    updateSummary();
    refreshPreview();
  });

  const renameInput = el("input", { type: "text", placeholder: "optional new name for the output", value: col.rename });
  renameInput.addEventListener("input", () => {
    col.rename = renameInput.value;
    updateSummary();
    refreshPreview();
  });
  const renameField = labeledField("Output column name (optional)", renameInput);

  const fileParsingCheckbox = el("input", { type: "checkbox" });
  fileParsingCheckbox.checked = col.isFileParsing;
  const fileParsingToggle = el("label", { class: "checkbox-field" }, [
    fileParsingCheckbox,
    document.createTextNode("This column holds a file path. Extract value(s) from the file instead of keeping the path"),
  ]);

  const qcEditor = buildQCEditor(col.qc);
  const qcSection = el("div", { class: "section" }, [el("h4", { text: "QC" }), qcEditor]);

  const fileParsingEditor = buildFileParsingEditor(col);
  const fileParsingSection = el("div", { class: "section" }, [el("h4", { text: "File parsing outputs" }), fileParsingEditor]);

  function syncMode() {
    renameField.classList.toggle("hidden", col.isFileParsing);
    qcSection.classList.toggle("hidden", col.isFileParsing);
    fileParsingSection.classList.toggle("hidden", !col.isFileParsing);
    updateSummary();
  }
  syncMode();

  fileParsingCheckbox.addEventListener("change", () => {
    col.isFileParsing = fileParsingCheckbox.checked;
    if (col.isFileParsing && col.fileParsing.length === 0) col.fileParsing.push(newFileParsingOutput());
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
    renameField,
    fileParsingToggle,
    qcSection,
    fileParsingSection,
  ]);

  let collapsed = false;
  const chevron = el("span", { class: "chevron" });
  const collapseBtn = el("button", {
    type: "button",
    class: "btn-collapse",
    title: "Collapse this column",
    onclick: () => {
      collapsed = !collapsed;
      body.classList.toggle("hidden", collapsed);
      card.classList.toggle("collapsed", collapsed);
      collapseBtn.title = collapsed ? "Expand this column" : "Collapse this column";
    },
  }, [chevron]);

  const card = el("div", { class: "column-card" }, [
    el("div", { class: "column-header" }, [
      el("div", { class: "column-header-title" }, [el("strong", { text: "Column" }), summarySpan]),
      el("div", { class: "column-header-actions" }, [collapseBtn, removeBtn]),
    ]),
    body,
  ]);
  updateSummary();
  return card;
}

function addColumn(col) {
  state.columns.push(col);
  columnsEl.appendChild(buildColumnCard(col));
  refreshPreview();
}

function clearAll() {
  state.columns.length = 0;
  columnsEl.innerHTML = "";
  refreshPreview();
}

// ---------------------------------------------------------------------------
// Top-level controls
// ---------------------------------------------------------------------------

document.getElementById("add-column-btn").addEventListener("click", () => addColumn(newColumn()));
document.getElementById("start-blank-btn").addEventListener("click", () => {
  if (state.columns.length && !confirm("Clear all columns and start over?")) return;
  clearAll();
});

downloadBtn.addEventListener("click", () => {
  const { plain, errors } = buildConfig(state.columns);
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
  const { plain, errors } = buildConfig(state.columns);
  if (errors.length) return;
  try {
    await copyToClipboard(serializeYAML(plain));
    copyStatusEl.textContent = "Copied!";
  } catch (err) {
    copyStatusEl.textContent = "Copy failed -- select the text in the preview and copy it manually.";
  }
  setTimeout(() => (copyStatusEl.textContent = ""), 2500);
});

// Start with one blank column so the form isn't empty on first load.
addColumn(newColumn());
