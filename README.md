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

`--qc-report` writes one row per failing sample/output pair, so a sample
failing three outputs produces three rows. Columns: `sample`, `column`
(the input's original name), `output_column` (its renamed name, the same
value if there was no rename, or the specific `file_parsing` output name
that failed), `operator`, `expected`, `actual`, `reason`. It's always
written, even header-only when nothing failed, so callers can check the
row count instead of the file's existence.

## `file_parsing`: extracting values from referenced files

A column marked `file_parsing` treats its cell as a file path instead of
a literal value. `file_parsing` is always a list of one or more named
outputs, each with its own command, run against that file — a single
entry pulls out one value, several pull out several values from the
*same* file into separate output columns. Each output's command result
becomes an output column's real value, flowing through that output's own
QC and into the output table like any other field.

A column with `file_parsing` gets its output name(s) and QC entirely
from that list — `rename` and `qc` on the column itself aren't valid
alongside it.

One output, one command:

```yaml
columns:
  - name: coverage_report
    file_parsing:
      - name: mean_depth
        command: |
          awk -F'\t' '$1 == "chr1" {print $7}' "$LIMSPORT_FILE"
        timeout_seconds: 30       # optional; omit for no timeout
        qc:
          - {operator: ">=", value: 30}
```

Several outputs pulled from the same file, each with its own command and
its own QC:

```yaml
columns:
  - name: coverage_report
    file_parsing:
      - name: mean_depth
        command: |
          awk -F'\t' '$1 == "chr1" {print $7}' "$LIMSPORT_FILE"
        qc:
          - {operator: ">=", value: 30}

      - name: coverage_pct
        command: |
          awk -F'\t' '$1 == "chr1" {print $6}' "$LIMSPORT_FILE"
        qc:
          - {operator: ">=", value: 95}

      - name: mean_mapq
        command: |
          awk -F'\t' '$1 == "chr1" {print $9}' "$LIMSPORT_FILE"
        timeout_seconds: 10       # each output can set its own timeout
```

- Every command in one column's `file_parsing` list runs via `bash -c`
  against the *same* localized copy of the file, so pipes and any tool
  you like work (`grep | cut`, `python3 -c "..."`, `jq`, whatever it
  needs) and a `gs://` source is only downloaded once no matter how many
  outputs pull values from it.
- The file's path only ever reaches each command through the
  `$LIMSPORT_FILE` environment variable, never spliced into the command
  string, since the path comes from TSV data and is less trusted than the
  config itself. Quote it (`"$LIMSPORT_FILE"`) the way you would in any
  bash script.
- A `gs://` path gets downloaded first, via `gcloud storage cp` into a
  fresh temp directory. Every output's command runs against that local
  copy, and the download is deleted afterward no matter what, even if a
  command fails.
- Each output's result has to be a single line. Trailing newlines get
  stripped, the same way bash's own `$(...)` behaves (most commands end
  their output with one), but a newline *inside* a result is a hard error.
- A failing command (non-zero exit) is a hard error too, and aborts any
  remaining outputs for that column. Both of these abort the entire
  export, not just that one row; a broken `file_parsing` command is a
  config problem, not a per-row data issue.
- QC failures are independent per output: if one output in a multi-output
  `file_parsing` column fails its `qc:`, that's what drops the sample —
  the QC report's `column` names the shared source column, and
  `output_column` names the specific output that actually failed.
- **Needs `--allow-file-parsing` on the command line even when the config
  asks for it.** Having `file_parsing` in a config isn't consent by
  itself; whoever's running the tool might not be who wrote the config.
  Without the flag, it refuses outright before reading a single row.

`examples/file_parsing/` has a full worked scenario — JSON via
`python3 -c`, a different TSV schema via `awk` (including a multi-output
column pulling several values out of one report), an invented report
format via `grep`/`cut`/`tr` — plus the error paths: a failing command,
an embedded newline, and the flag being left off.

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
