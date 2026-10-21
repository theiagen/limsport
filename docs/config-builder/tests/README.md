# LIMSport Config Builder

A browser-based, no-install tool that walks a non-technical user through
building a `config.yaml` for [LIMSport](https://github.com/theiagen/limsport)
-- add columns, rename them, add QC thresholds, or extract values from a
file, and download the finished config. Similar in *purpose* to seqsender's
Submission Wizard, but its own look and a from-scratch implementation.

## Running it locally

It's plain HTML/CSS/JS with no build step and no npm dependencies -- but
`app.js` imports `schema.js` as an ES module, and browsers block module
imports over `file://` URLs (Chrome/Edge will refuse; Firefox is
inconsistent). Serve the folder over HTTP instead:

```bash
cd docs/config-builder
python3 -m http.server 8000
# then open http://localhost:8000/
```

## How it's organized

- **`schema.js`** -- pure logic, no DOM. Turns the wizard's in-memory state
  into the same shape `limsport.config.ExportConfig` expects, runs the same
  validation checks as the Pydantic models in `limsport/config.py`
  (duplicate names, `tolerance_percent` only valid with `~=`, etc.), and
  serializes the result to YAML.
- **`importer.js`** / **`vendor/js-yaml.js`** -- the "open an existing
  config" path (see below).
- **`examples.js`** -- builds wizard state matching
  `../examples/configs/config.yaml`. There's no "load example" button in the UI (deliberately
  removed -- worry was users would download an example unmodified instead
  of building their own config), but this module is still the backbone of
  the test suite below.
- **`app.js`** -- DOM wiring: builds the form, keeps it in sync with state,
  and re-renders the YAML preview / error list / download-copy buttons on
  every change.
- **`index.html` / `style.css`** -- structure and styling.

## Tests

```bash
cd config-builder
node --test tests/
```

No npm install needed -- `tests/schema.test.mjs` uses only Node's built-in
test runner. The real test, though, is that it shells out to
`tests/load_and_dump.py`, which runs the generated YAML through the actual
`limsport.config.load_config()` (the real Pydantic validator, not a
reimplementation) and checks the result is semantically identical to the
checked-in `examples/*/config.yaml` fixtures. That requires `limsport`'s
dependencies (`pyyaml`, `pydantic`) to be importable -- e.g. an editable
install (`pip install -e ..`) or `PYTHONPATH` pointing at `../src`.

## Editing an existing config

"Open existing config.yaml", or dropping a file anywhere on the page, loads
a config back into the form. `importer.js` parses it with
[js-yaml](https://github.com/nodeca/js-yaml) 4.1.0, vendored unmodified as
`vendor/js-yaml.js` (its `dist/js-yaml.mjs`, MIT; named `.js` so it's
served as JavaScript), and `schema.js`'s `stateFromConfig()` turns the
result into wizard state.

A file the form can't hold exactly is rejected, not partially loaded, with
each problem listed by line number (unknown keys, `value: true`, sample
names containing commas, an empty `default: []`, an unquoted `yes`/`no`/
`on`/`off` that limsport's YAML 1.1 parser reads as a boolean, and so on).
That way a config that loads always downloads unchanged. Comments are not
kept, since the download is regenerated from the form; the page warns when
the file has any.

## Where this should live long-term

This folder is intentionally decoupled from the docs site so it could be
built and tested before that existed. Once the Zensical docs site is set
up, move this folder under `docs/` (e.g. `docs/tools/config-builder/`) and
link to it from a short landing page and the nav.
