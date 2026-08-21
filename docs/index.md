# LIMSport

`limsport` reads a TSV of samples, applies an optional YAML config to rename/select columns and run QC on each row, and writes a LIMS-importable TSV.

## Installation

Install with pip:

```
pip install -e .
```

LIMSport requires Python 3.10+.

## Quick start

```
limsport --input samples.tsv --output samples.lims.tsv
```

If neither `--config` nor `--samples` are provided, nothing happens. To transform and/or QC the data, add a configuration file:

```
limsport --input samples.tsv --config config.yaml --output samples.lims.tsv --qc-report qc_report.tsv
```

See `examples/` for a demo.

## CLI reference

| flag                          | required | meaning                                                                                |
| ----------------------------- | -------- | -------------------------------------------------------------------------------------- |
| `--input`, `-i`               | yes      | input TSV (delimiters auto-detected)                                                   |
| `--output`, `-o`              | no       | output path (default: `limsport.tsv`)                                                  |
| `--config`, `-c`              | no       | YAML configuration                                                                     |
| `--samples`, `-s`             | no       | a file of sample names (one per line) to include; if omitted, every sample is included |
| `--qc-report`, `-r`           | no       | QC failure report TSV path (default: `qc_report.tsv`)                                  |
| `--delimiter`, `-d`           | no       | output delimiter (default: tab)                                                        |
| `--allow-file-parsing`        | no       | required if the config uses `file_parsing` (see below)                                 |
| `--max-file-parsing-failures` | no       | abort once more than N rows fail `file_parsing` (default: no limit)                    |

## The config file

A config is a YAML file with two primary keys: `columns` and `set_qc`.

`columns` lists every column to keep in the output, ordered to match how they appear in the config. `columns` can be omitted entirely if a [`set_qc`](#set-level-qc) list exists. `columns: []` will be rejected.

`set_qc` lists a series of rules where if one fails, all samples fail the run.

A config with neither `columns` nor `set_qc` is treated as malformatted and fails.

```yaml
# a basic configuration example that renames a column

columns:
    - input_column: sample_id # kept as-is: no rename, no qc

    - input_column: lot_number # kept and renamed, but never QC'd
      output_column: lot
```

### Config QC

A sample failing a `qc` rule gets dropped from the output and reported in the log and qc_report file.

Configs are considered malformatted when either (a) config columns that are not present in the input table, or (b) contain ambiguous column names. Malformatted configs cause the program to exit.

#### QC operators

| operator             | meaning                                                  | value type       | notes                                                                      |
| -------------------- | -------------------------------------------------------- | ---------------- | -------------------------------------------------------------------------- |
| `>`, `>=`, `<=`, `<` | ordinary numeric comparison                              | number           |                                                                            |
| `=`                  | equality                                                 | number or string | string comparison is case-sensitive unless `case_insensitive: true` is set |
| `~=`                 | within `tolerance_percent`% of `value`, either direction | number           | requires a companion `tolerance_percent` field                             |
| `contains`           | `value` is a substring of the cell                       | string           | case-sensitive unless `case_insensitive: true` is set                      |
| `does_not_contain`   | `value` is not a substring of the cell                   | string           | case-sensitive unless `case_insensitive: true` is set                      |
| `is_empty`           | the cell is blank or whitespace-only                     | _(none)_         | takes no `value`                                                           |
| `is_not_empty`       | the cell has real content                                | _(none)_         | takes no `value`                                                           |

`case_insensitive` (default `false`) is only valid on `=`, `contains`, and `does_not_contain` — it's a config error to set it alongside a numeric `value` or a numeric-only operator.

Empty or whitespace-only cells fail due to `"missing_value"` for every operator **except** `is_empty`/`is_not_empty`.

Cells that are not able to be cast as a number fail QC (`"non-numeric value 'NA' cannot be compared with >= 1000"`).

```yaml
# fixed threshold qc examples

columns:
    - input_column: read_count
      output_column: total_reads # renamed in the output
      qc: # all of these conditions must pass for the row to be included in the output
          - { operator: ">=", value: 1000 }
          - { operator: "<=", value: 1000000 } # each item is treated as an "AND"

    - input_column: status
      qc:
          - { operator: "=", value: PASS } # case-sensitive string matching

    - input_column: length
      qc:
          - { operator: "~=", value: 1000000, tolerance_percent: 5 } # within 5% of value

    - input_column: organism
      qc:
          - {
                operator: "contains",
                value: "Escherichia",
                case_insensitive: true,
            } # substring match, case-insensitivity turned on
```

#### Conditional `qc`

`qc` accepts two shapes. The plain list shown above is fixed -- every row is checked against the same thresholds.

A conditional QC shape picks which threshold list applies to a row from _another column's value in that same row_, e.g. different genome size bounds per predicted organism instead of one bound that has to fit every organism in the batch:

```yaml
# conditional qc example

columns:
    - input_column: gambit_predicted_taxon
      output_column: predicted_taxon

    - input_column: assembly_length
      qc:
          match_column: gambit_predicted_taxon # another column's original name
          cases:
              "Escherichia coli":
                  - { operator: ">=", value: 4600000 }
                  - { operator: "<=", value: 5900000 }
              "Klebsiella pneumoniae":
                  - { operator: ">=", value: 5200000 }
                  - { operator: "<=", value: 5900000 }
          default: # optional; without a default, samples w/o a match will fail
              - { operator: ">=", value: 1500000 }
```

- `match_column` must be a column that exists in the input header. It doesn't need to be in the output `columns:` list.
- `cases` maps an exact, **case-sensitive** value of `match_column` to the `QCCondition` list to run for that row
- A row whose `match_column` value isn't a key in `cases` uses `default` if one is configured. Without a default, the row will fail QC with the message: `no qc rule matches gambit_predicted_taxon='Vibrio cholerae' for column 'assembly_length', and no default is configured`

#### Set-level QC

Every `qc` block is checked and then applied on a per-row basis. `set_qc` results are applied on the entire run.

`set_qc` checks specific sample(s) (e.g. a negative control) for specified metrics, and if any of its checks fail, **the entire run fails**.

```yaml
# set_qc example

# a columns section is not required when set_qc is configured; this example is a valid yaml

set_qc:
    - rule_name: "NTC has no detected organism and low reads"
      match_samples:
          sample_pattern: "NTC" # substring match against the sample name (row[0])
      checks:
          - input_column: reads
            qc:
                - { operator: "<=", value: 1000 }
          - input_column: detected_organism
            qc:
                - { operator: is_empty }
```

- `rule_name` is the name of the rule used in logs and the qc report. The rule name must be unique.
- `match_samples` identifies which sample(s) a rule applies to. There are three options available:
    - `sample_pattern`: uses a case-**sensitive** substring match against the sample name
    - `sample_regex`: uses `re.search` against the sample name
    - `samples`: an explicit, exact list of sample names
- `checks` lists one or more `{input_column, qc}` checks, all evaluated against all matched samples.

**Every** matched sample must pass **every** check for the rule to pass. If any matched sample fails any check, the whole run is considered a QC fail.

A rule that identifies zero samples in the run is itself a hard error (there's no sample to attach a QC failure to), not a QC failure -- the run aborts before any output is written. For `samples:` specifically, this applies **per name**: every sample name listed must exist in the run, not just at least one of them, so a typo'd or missing name (e.g. a dropped negative control) can't silently go unchecked.

### `file_parsing` configuration

A column marked with `file_parsing` treats the row's value as a file path.

`file_parsing` contains a list of one or more named outputs, each with its own command run against that file. The result of the command will become the output column's value.

A column with `file_parsing` is incompatible with `output_column` and `qc` on the same level. Invalid configuration errors will occur. All QC and output naming must occur within the `file_parsing` list.

```yaml
# file parsing example

columns:
    - input_column: coverage_report
      file_parsing:
          - output_column: mean_depth
            command: |
                awk -F'\t' '$1 == "chr1" {print $7}' "$FILE"
            qc:
                - { operator: ">=", value: 30 }

          - output_column: coverage_pct
            command: |
                awk -F'\t' '$1 == "chr1" {print $6}' "$FILE"
            qc:
                - { operator: ">=", value: 95 }

          - output_column: mean_mapq
            command: |
                awk -F'\t' '$1 == "chr1" {print $9}' "$FILE"
            timeout_seconds: 10 # each output can set its own timeout
```

The command is run via `bash -c`. Anything in your local environment can be used.

You can access the file in the command with a bash variable: `"$FILE"`.

Files in GCP are downloaded only once with `gcloud storage cp` into a temporary directory. The downloaded file is deleted after all command(s) are run.

Each output result **must** be a single line, internal newline characters will cause failures.

To activate file parsing, you **must** permit the behavior with the `--allow-file-parsing` option. It's a liability thing.

### When a file won't parse

A file that can't be parsed fails **that row's QC**, not the whole run. The sample is
left out of the output, its reason lands in the QC report like any other QC failure,
and every other sample is still processed and written. This covers a failing command,
a timeout, a result containing a newline, and a `gs://` path that can't be downloaded.

Three things stay fatal, because none of them is about one row's data:

- **a tool `file_parsing` needs isn't installed** (e.g. `gcloud`) — no row could succeed
- **every row's `file_parsing` failed** — a command or path template that is broken for
  every sample leaves nothing to write, and a header-only table with an exit code of 0
  would report that as a success
- **more than `--max-file-parsing-failures` rows failed**, if you set a limit

The all-rows-failed guard is always on and independent of the limit: the limit tunes
how much genuinely bad data you'll tolerate, while the guard asks whether anything
worked at all. `--max-file-parsing-failures` defaults to no limit; `0` aborts on the
first failure, which is how LIMSport behaved before.

## The QC report

A QC report (default: `qc_report.tsv`) contains one row per failing sample/output pair, so a sample failing three outputs produces three rows. It has the following columns:

| column          | explanation                                                                                                                  |
| --------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `sample`        | the sample name                                                                                                              |
| `input_column`  | the input's original column name                                                                                             |
| `output_column` | the config's `output_column`, the same value as the input column if none was set, or the specific `file_parsing` output name |
| `operator`      | the conditional used in the qc statement                                                                                     |
| `expected`      | the value in the qc statement                                                                                                |
| `actual`        | the actual row value                                                                                                         |
| `reason`        | the reason why it failed                                                                                                     |

## Delimiter handling

Delimiters are auto-detected from its header line. If auto-detection fails, LIMSport gives up.

With no `--delimiter`, the output defaults to tab. Change the output delimiter with `--delimiter`

### Repo Layout

```
limsport/
├── cli.py           argparse entry point; flag parsing, exit codes
├── config.py        pydantic models for the YAML config + its loader
├── table_io.py      TSV/delimited I/O: read/write, delimiter detection
├── qc.py            pure QC evaluation (cell + condition -> pass/fail/why)
├── file_parsing.py  file_parsing: cloud localization, bash execution
├── layout.py        validates the config against the input header, plans the output
├── ingest.py        reads the rows: sample filter, QC, writes the passers
├── pipeline.py      orchestrates one export: wires the above together
├── report.py        turns QC results into log lines and the report TSV
└── exceptions.py    the LIMSportError hierarchy cli.py catches
```

Tests mirror this layout (`tests/test_<module>.py`).
