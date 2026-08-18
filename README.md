# LIMSport

`limsport` reads a TSV of samples, applies an optional YAML config to rename/select columns and run QC on each row, and writes a LIMS-importable TSV. Pass `--qc-report` to see exactly which samples failed which checks and why.

## Installation

LIMSport is currently unpublished, so install with pip like so:

```
pip install -e .
```

This registers a `limsport` console script (see `pyproject.toml`). Requires Python 3.10+.

## Quick start

```
limsport --input samples.tsv --output samples.lims.tsv
```

If neither `--config` nor `--samples` are provided, nothing happens. To transform and/or QC the data, add a configuration file:

```
limsport --input samples.tsv --config config.yaml --output samples.lims.tsv --qc-report qc_report.tsv
```

See `examples/` for more examples.

## CLI reference

| flag | required | meaning |
|------|----------|---------|
| `--input`, `-i` | yes | input TSV (or another delimited format, see below) |
| `--output`, `-o` | no | output path (default: `limsport.tsv`); not written if `--config`, `--samples`, and `--delimiter` are all omitted |
| `--config`, `-c` | no | YAML config: column allow-list, renaming, QC, `file_parsing` |
| `--samples`, `-s` | no | a file of sample names (one per line) to include; if omitted, every sample is included |
| `--qc-report`, `-r` | no | write a TSV of every QC failure (sample, column, reason, ...) |
| `--delimiter`, `-d` | no | output delimiter (default: tab) |
| `--allow-file-parsing` | no | required if the config uses `file_parsing` (see below) |

## The config file

A config is a YAML file with one required key, `columns`, listing every column to keep in the output. Only columns that are listed are kept. Output columns are returned in the order they appear in the config.

```yaml
columns:
  - name: sample_id # kept as-is: no rename, no qc

  - name: read_count
    rename: total_reads # renamed in the output
    qc: # all of these conditions must pass for the row to be included in the output
      - {operator: ">=", value: 1000}
      - {operator: "<=", value: 1000000} # each item is treated as an "AND"

  - name: status
    qc:
      - {operator: "=", value: PASS} # case-sensitive string matching

  - name: length
    qc:
      - {operator: "~=", value: 1000000, tolerance_percent: 5} # within 5% of value

  - name: organism
    qc:
      - {operator: "contains", value: "Escherichia"} # substring match, case-sensitive by default

  - name: lot_number # kept and renamed, but never QC'd
    rename: lot
```

A sample failing a `qc:` rule gets dropped from the output and reported.

Malformatted configs either (a) have config columns that are not present in the input table, (b) have ambiguous column names. These will cause the program to exit.

### QC operators

| operator | meaning | value type | notes |
|----------|---------|-------------|-------|
| `>`, `>=`, `<=`, `<` | ordinary numeric comparison | number | |
| `=` | equality | number or string | string comparison is case-sensitive unless `case_insensitive: true` is set |
| `~=` | within `tolerance_percent`% of `value`, either direction | number | requires a companion `tolerance_percent` field |
| `contains` | `value` is a substring of the cell | string | case-sensitive unless `case_insensitive: true` is set |
| `does_not_contain` | `value` is not a substring of the cell | string | case-sensitive unless `case_insensitive: true` is set |

`case_insensitive` (default `false`) is only valid on `=`, `contains`, and `does_not_contain` — it's a config error to set it alongside a numeric `value` or a numeric-only operator.

Empty or whitespace-only cells fail due to `"missing_value"`. Cells that are not able to be cast as a number fail QC (`"non-numeric value 'NA' cannot be compared with >= 1000"`).

Boolean conditions are not accepted at this time.

### Conditional `qc`: choosing thresholds per row

`qc` accepts two shapes. The plain list shown above is fixed: every row is checked against the same thresholds. The other shape picks which threshold list applies to a row from *another column's value in that same row*, e.g. different genome size bounds per predicted organism instead of one bound that has to fit every organism in the batch:

```yaml
columns:
  - name: gambit_predicted_taxon
    rename: predicted_taxon

  - name: assembly_length
    qc:
      match: gambit_predicted_taxon # another column's original name
      rules:
        "Escherichia coli":
          - {operator: ">=", value: 4600000}
          - {operator: "<=", value: 5900000}
        "Klebsiella pneumoniae":
          - {operator: ">=", value: 5200000}
          - {operator: "<=", value: 5900000}
      default: # optional (but samples that don't match will fail)
        - {operator: ">=", value: 1500000}
```

Which shape you get is purely structural: a YAML list is the plain form, a YAML mapping (`match`/`rules`/`default`) is the conditional form. No separate keyword to learn, no way to write both on the same `qc:`.

- `match` must be a column that exists (unambiguously) in the input header. It doesn't have to also be kept in the output `columns:` list.
- `rules` maps an exact, case-sensitive value of `match` to the `QCCondition` list to run for that row: same shape and semantics as the plain list form, just selected per row instead of fixed.
- A row whose `match` value isn't a key in `rules` uses `default` if one is configured. **Without a `default`, that's a QC failure**, reported with a reason like `no qc rule matches gambit_predicted_taxon='Vibrio cholerae' for column 'assembly_length', and no default is configured`, warned about, included in `--qc-report`, and dropped from the output: the same treatment as any other QC failure, not a silent pass. Only the row(s) with the unrecognized value are affected; every row whose `match` value has a rule is still checked normally.
- Because a conditional `qc` reads its `match` value straight from that  row, several columns can each key off a *different* `match` column in the same config (e.g. genome-size bounds keyed by predicted organism, read-quality bounds keyed by sequencing platform) with no shared top-level structure to keep in sync.
- **This works identically inside a `file_parsing` output's own `qc:`**: a parsed value can be checked against organism-specific thresholds the same way a plain column can:
  ```yaml
  - name: coverage_tsv
    file_parsing:
      - name: quast_n50
        command: |
          awk -F'\t' '$1 == "N50" {print $2}' "$LIMSPORT_FILE"
        qc:
          match: gambit_predicted_taxon
          rules:
            "Escherichia coli":
              - {operator: ">=", value: 20000}
  ```

### `file_parsing`: extracting values from referenced files

A column marked `file_parsing` treats its cell as a file path instead of a literal value. `file_parsing` is always a list of one or more named outputs, each with its own command run against that file: a single entry pulls out one value, several pull out several values from the *same* file into separate output columns. Each output's command result becomes an output column's real value, flowing through that output's own QC and into the output table like any other field.

A column with `file_parsing` gets its output name(s) and QC entirely from that list. `rename` and `qc` on the column itself aren't valid alongside it.

One output, one command:

```yaml
columns:
  - name: coverage_report
    file_parsing:
      - name: mean_depth
        command: |
          awk -F'\t' '$1 == "chr1" {print $7}' "$LIMSPORT_FILE"
        timeout_seconds: 30 # optional; omit for no timeout
        qc:
          - {operator: ">=", value: 30}
```

Several outputs pulled from the same file, each with its own command and its own QC:

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

- Every command in one column's `file_parsing` list runs via `bash -c` against the *same* localized copy of the file, so pipes and any tool you like work (`grep | cut`, `python3 -c "..."`, `jq`, whatever it needs) and a `gs://` source is only downloaded once no matter how many outputs pull values from it.
- The file's path only ever reaches each command through the `$LIMSPORT_FILE` environment variable, never spliced into the command string, since the path comes from TSV data and is less trusted than the config itself. Quote it (`"$LIMSPORT_FILE"`) the way you would in any bash script.
- A `gs://` path gets downloaded first, via `gcloud storage cp` into a fresh temp directory. Every output's command runs against that local copy, and the download is deleted afterward no matter what, even if a command fails.
- Each output's result has to be a single line. Trailing newlines get stripped, the same way bash's own `$(...)` behaves (most commands end their output with one), but a newline *inside* a result is a hard error.
- A failing command (non-zero exit) is a hard error too, and aborts any remaining outputs for that column. Both of these abort the entire export, not just that one row; a broken `file_parsing` command is a config problem, not a per-row data issue.
- QC failures are independent per output: if one output in a multi-output `file_parsing` column fails its `qc:`, that's what drops the sample. The QC report's `column` names the shared source column, `output_column` names the specific output that actually failed.
- **Needs `--allow-file-parsing` on the command line even when the config asks for it.** Having `file_parsing` in a config isn't consent by itself; whoever's running the tool might not be who wrote the config. Without the flag, it refuses outright before reading a single row.

`examples/file_parsing/` has a full worked scenario: JSON via `python3 -c`, a different TSV schema via `awk` (including a multi-output column pulling several values out of one report), an invented report format via `grep`/`cut`/`tr`, plus the error paths: a failing command, an embedded newline, and the flag left off.

## The QC report

`--qc-report` writes one row per failing sample/output pair, so a sample failing three outputs produces three rows. It contains the following columns:

- `sample`
- `column` (the input's original column name)
- `output_column` (the renamed column, the same value if there was no rename, or the specific `file_parsing` output name)
- `operator`
- `expected`
- `actual`
- `reason`

The header is always written. `operator`/`expected` are blank for a conditional `qc` failure with no matching rule (see below).

## Delimiter handling

The input's delimiter (tab, comma, semicolon, or pipe) is auto-detected from its header line, not the whole file, so one malformed row elsewhere can't break detection. If it can't be figured out confidently, it errors out.

With no `--delimiter`, the output defaults to tab. If the input is already tab-delimited and neither `--config` nor `--samples` is given either, there's truly nothing to do, so nothing gets written. If the input uses a different delimiter (comma, say), converting it to tab is a real change from the input, so it gets written even with no `--config` or `--samples`. Pass `--delimiter` explicitly to keep the input's original delimiter instead, or to convert to a third one.

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

Tests mirror this layout (`tests/test_<module>.py`). There's no shared fixtures directory: every test builds whatever input/config files it needs directly under pytest's own `tmp_path`, right next to the assertions that use them. `config.py` and `transform.py` each split their tests across three files instead of one: `test_<module>.py` for the core behavior, `test_<module>_file_parsing.py` and `test_<module>_conditional_qc.py` for those two features specifically.


## To-Do:

- [ ] enable cell-parsing (eg BUSCO, etc.)
- [x] substring QC
- [ ] set level QC - if "NTC" fails fail the entire run
- [x] config builder from a html thingy
