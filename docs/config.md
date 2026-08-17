# Config Builder

Build a `config.yaml` for LIMSport without writing YAML by hand: add the columns you want to keep,
optionally rename them, add QC thresholds, or extract values from a file, then download the finished
config.

<link rel="stylesheet" href="config-builder/style.css" />

<div class="limsport-config-builder">
  <div class="toolbar">
    <button type="button" id="start-blank-btn" class="toolbar-secondary">Clear all columns</button>
  </div>

  <main class="layout">
    <section class="form-pane">
      <div id="columns"></div>
      <button type="button" id="add-column-btn" class="btn-add btn-add-column">+ Add column</button>
    </section>

    <aside class="preview-pane">
      <h2>config.yaml</h2>
      <div id="file-parsing-notice" class="notice hidden">
        This config extracts values from files. Remember to pass <code>--allow-file-parsing</code> when
        you run <code>limsport</code>, or these columns will be rejected at run time.
      </div>
      <div id="errors" class="errors hidden"></div>
      <pre id="preview"></pre>
      <div class="preview-actions">
        <button type="button" id="download-btn">Download config.yaml</button>
        <button type="button" id="copy-btn">Copy to clipboard</button>
        <span id="copy-status" class="copy-status" aria-live="polite"></span>
      </div>
    </aside>
  </main>
</div>

<script type="module" src="config-builder/app.js"></script>

Prefer a full page instead? <a href="config-builder/index.html" target="_blank">Open the builder on its own page</a>.

