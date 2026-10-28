# Config Builder

Build a `config.yaml` for LIMSport without writing YAML yourself! Add the columns you want to keep,
optionally rename them, add QC thresholds, or extract values from a file. Download the finished
config when ready!

<!-- embed the html inline to look real nice -->

<link rel="stylesheet" href="config-builder/style.css" />

<div class="limsport-config-builder">
  <div class="toolbar">
    <div class="toolbar-open">
      <button type="button" id="open-btn" class="toolbar-secondary">Open existing config.yaml</button>
      <input type="file" id="open-input" class="hidden" accept=".yaml,.yml" />
      <span class="hint">or drop one anywhere on the page</span>
    </div>
    <button type="button" id="start-blank-btn" class="toolbar-secondary">Clear all</button>
  </div>

  <main class="layout">
    <section class="form-pane">
      <div class="form-section">
        <div class="section-header">
          <h3>Sample-level QC</h3>
          <button type="button" class="btn-collapse" title="Collapse this section"><span class="chevron"></span></button>
        </div>
        <div class="section-body">
          <p class="hint">
            Include only these columns in the output and exclude samples that fail a column's QC
          </p>
          <div id="columns"></div>
          <button type="button" id="add-column-btn" class="btn-add btn-add-column">+ Add column</button>
        </div>
      </div>

      <div class="form-section">
        <div class="section-header">
          <h3>Set-level QC (optional)</h3>
          <button type="button" class="btn-collapse" title="Collapse this section"><span class="chevron"></span></button>
        </div>
        <div class="section-body">
          <p class="hint">
            Fail the entire run if specific sample(s) fail a QC check
          </p>
          <div id="set-qc-rules"></div>
          <button type="button" id="add-set-qc-btn" class="btn-add btn-add-set-qc">+ Add set-level QC rule</button>
        </div>
      </div>

      <div class="form-section">
        <div class="section-header">
          <h3>Skip sample-level QC (optional)</h3>
          <button type="button" class="btn-collapse" title="Collapse this section"><span class="chevron"></span></button>
        </div>
        <div class="section-body">
          <p class="hint">
            Samples (e.g. negative controls) that skip sample-level QC but are still checked by set-level QC rules
          </p>
          <div id="skip-sample-qc"></div>
          <button type="button" id="add-skip-sample-qc-btn" class="btn-add btn-add-set-qc">+ Add samples to skip</button>
        </div>
      </div>
    </section>

    <aside class="preview-pane">
      <h2>config.yaml</h2>
      <div id="import-status" class="hidden" aria-live="polite"></div>
      <div id="file-parsing-notice" class="notice hidden">
        This config extracts values from files. Remember to pass <code>--allow-file-parsing</code> when
        you run <code>limsport</code>, or these columns will be rejected at run time.
      </div>
      <div id="errors" class="errors hidden"></div>
      <div id="warnings" class="warnings hidden"></div>
      <pre id="preview"></pre>
      <div class="preview-actions">
        <button type="button" id="download-btn">Download config.yaml</button>
        <button type="button" id="copy-btn">Copy to clipboard</button>
        <span id="copy-status" class="copy-status" aria-live="polite"></span>
      </div>
    </aside>
  </main>

  <div id="drop-overlay" class="drop-overlay hidden">Drop a config.yaml to edit it</div>
</div>

<script type="module" src="config-builder/app.js"></script>

Prefer a full page instead? <a href="config-builder/index.html" target="_blank">Open the builder on its own page</a>.
