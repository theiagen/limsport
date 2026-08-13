# LIMSport

`limsport` reads a TSV of samples, applies an optional YAML config to
rename/select columns and run QC on each row, and writes a LIMS-importable
TSV. Pass `--qc-report` and it'll also tell you exactly which samples
failed which checks and why.

With no config and no sample list, the output is just a copy of the input.
Everything else — renaming, dropping columns, filtering samples, QC,
delimiter conversion, pulling values out of referenced files — is opt-in
through the config file and a few flags.

## Installation

Not published anywhere yet, so install from a checkout:

```
pip install -e .
```

This registers a `limsport` console script (see `pyproject.toml`).
Requires Python 3.10+.

## Quick start

```
limsport --input samples.tsv --output samples.lims.tsv
```

With no `--config` or `--samples`, this just copies `samples.tsv` to
`samples.lims.tsv` (see "Delimiter handling" below for the one exception).
To actually transform and QC the data, add a config:

```
limsport --input samples.tsv --config config.yaml --output samples.lims.tsv --qc-report qc_report.tsv
```

`examples/` walks through every operator, every warning and error path,
and a separate scenario for `file_parsing`, with its own README. Probably
the fastest way to see the whole tool in action.

## CLI reference

| flag | required | meaning |
|------|----------|---------|
| `--input`, `-i` | yes | input TSV (or another delimited format, see below) |
| `--output`, `-o` | yes | output path |
| `--config`, `-c` | no | YAML config: column allow-list, renaming, QC, `file_parsing` |
| `--samples`, `-s` | no | a file of sample names (one per line) to include; if omitted, every sample is included |
| `--qc-report`, `-r` | no | write a TSV of every QC failure (sample, column, reason, ...) |
| `--delimiter`, `-d` | no | output delimiter; defaults to the input's own (auto-detected) delimiter |
| `--allow-file-parsing` | no | required if the config uses `file_parsing` (see below) |

Exit codes: `0` on success, `1` for a config/input/file_parsing problem
(printed as one clean line, never a raw traceback), `2` for a usage error
like a missing flag (argparse's own).

## The config file

A config is a YAML file with one required key, `columns`, listing every
column to keep in the output. Anything not listed gets dropped. Order
matters too: the output's columns come out in the order you list them here.

```yaml
columns:
  - name: sample_id          # kept as-is: no rename, no qc

  - name: read_count
    rename: total_reads      # renamed in the output
    qc:
      - {operator: ">=", value: 1000}     # both conditions must pass
      - {operator: "<=", value: 1000000}  # treated as an "AND"

  - name: status
    qc:
      - {operator: "=", value: PASS}      # case-sensitive string match

  - name: length
    qc:
      - {operator: "~=", value: 1000000, tolerance_percent: 5}  # within 5%

  - name: lot_number          # kept and renamed, but never QC'd
    rename: lot
```

A sample failing a `qc:` rule gets dropped from the output and reported
(see below), but that doesn't abort the run. A few things do abort the
whole run before any output is written: a config column that doesn't
exist in the input header, a column name that's ambiguous (shows up more
than once in the header), or a data row with more fields than the header.

### QC operators

| operator | meaning | value type | notes |
|----------|---------|-------------|-------|
| `>`, `>=`, `<=`, `<` | ordinary numeric comparison | number | |
| `=` | equality | number or string | string comparison is exact and case-sensitive |
| `~=` | within `tolerance_percent`% of `value`, either direction | number | requires a companion `tolerance_percent` field |

A column can list several conditions to express a range, like `read_count`
above. They're ANDed, and checking stops at the first failure, so the
reported reason is whichever condition got checked first — not necessarily
every condition that would have failed.

An empty or whitespace-only cell always fails as `"missing value"`; it's
never compared against anything. A cell that won't parse as a number
against a numeric operator fails as an ordinary QC failure instead of
crashing (e.g. `"non-numeric value 'NA' cannot be compared with >= 1000"`)
— one bad cell shouldn't take down the whole batch. Boolean YAML values
(`value: true`) are rejected in the config outright, since `true` almost
always means the text `"true"`, not the number `1`.

### The QC report

`--qc-report` writes one row per failing sample/column pair, so a sample
failing three columns produces three rows. Columns: `sample`, `column`
(the input's original name), `output_column` (its renamed name, or the
same value if there was no rename), `operator`, `expected`, `actual`,
`reason`. It's always written, even header-only when nothing failed, so
callers can check the row count instead of the file's existence.

## `file_parsing`: extracting values from referenced files

A column marked `file_parsing` treats its cell as a file path instead of
a literal value. The configured command runs against that file, and its
output becomes the cell's real value, flowing through QC and into the
output like any other field.

```yaml
columns:
  - name: coverage_report
    rename: mean_depth
    file_parsing:
      command: |
        awk -F'\t' '$1 == "chr1" {print $7}' "$LIMSPORT_FILE"
      timeout_seconds: 30       # optional; omit for no timeout
    qc:
      - {operator: ">=", value: 30}
```

- The command runs via `bash -c`, so pipes and any tool you like work
  (`grep | cut`, `python3 -c "..."`, `jq`, whatever it needs).
- The file's path only ever reaches the command through the
  `$LIMSPORT_FILE` environment variable, never spliced into the command
  string, since the path comes from TSV data and is less trusted than the
  config itself. Quote it (`"$LIMSPORT_FILE"`) the way you would in any
  bash script.
- A `gs://` path gets downloaded first, via `gcloud storage cp` into a
  fresh temp directory. The command runs against that local copy, and the
  download is deleted afterward no matter what, even if the command fails.
- The result has to be a single line. Trailing newlines get stripped, the
  same way bash's own `$(...)` behaves (most commands end their output
  with one), but a newline *inside* the result is a hard error.
- A failing command (non-zero exit) is a hard error too. Both of these
  abort the entire export, not just that one row; a broken `file_parsing`
  command is a config problem, not a per-row data issue.
- **Needs `--allow-file-parsing` on the command line even when the config
  asks for it.** Having `file_parsing` in a config isn't consent by
  itself; whoever's running the tool might not be who wrote the config.
  Without the flag, it refuses outright before reading a single row.

`examples/file_parsing/` has a full worked scenario — JSON via
`python3 -c`, a different TSV schema via `awk`, an invented report format
via `grep`/`cut`/`tr` — plus the error paths: a failing command, an
embedded newline, and the flag being left off.

## Delimiter handling

The input's delimiter (tab, comma, semicolon, or pipe) is auto-detected
from its header line, not the whole file, so one malformed row elsewhere
can't break detection. If it can't be figured out confidently (a
single-column file, say), that's a clean error rather than a silent guess.

With no `--delimiter`, the output keeps the input's own delimiter.
Combined with no `--config` or `--samples`, the file gets copied directly,
never parsed or rewritten. Pass `--delimiter` to convert to something else on
the way out.

## Development

```
pip install -e ".[dev]"
pytest
```

### Layout

```
src/limsport/
├── cli.py           argparse entry point; flag parsing, exit codes
├── config.py        pydantic models for the YAML config + its loader
├── table_io.py      TSV/delimited I/O: read/write, delimiter detection
├── qc.py            pure QC evaluation (cell + condition -> pass/fail/why)
├── file_parsing.py  file_parsing: cloud localization, bash execution
├── transform.py     orchestrates one export: wires the above together
├── report.py        turns QC results into log lines and the report TSV
└── exceptions.py    the LIMSportError hierarchy cli.py catches
```

Tests mirror this layout one-to-one (`tests/test_<module>.py`), plus
fixtures under `tests/fixtures/`.
