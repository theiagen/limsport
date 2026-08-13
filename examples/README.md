# LIMSport examples

A single scenario exercising every QC comparison operator, every warning
path, every column/sample transformation, and every hard-error path
`limsport` supports. `file_parsing/` is a second, separate scenario for
just the `file_parsing` feature — it needs its own dataset, since a
`file_parsing` failure aborts the entire run and can't share the main
scenario's one successful pass.

## Files

- `input.tsv` — 14 samples. Every sample except `SAMPLE_001` is set up to
  fail (or get excluded/flagged as unknown) in one specific, isolated way:

  | sample      | demonstrates                                                          |
  |-------------|------------------------------------------------------------------------|
  | SAMPLE_001  | passes every check                                                    |
  | SAMPLE_002  | `read_count` below the `>=` minimum                                   |
  | SAMPLE_003  | `read_count` above the `<=` maximum                                   |
  | SAMPLE_004  | `quality_score` fails `>`                                             |
  | SAMPLE_005  | `error_rate` fails `<`                                                |
  | SAMPLE_006  | `status` fails `=` (string mismatch)                                 |
  | SAMPLE_007  | `length` fails `~=` (outside 5% tolerance)                           |
  | SAMPLE_008  | `read_count` is non-numeric ("NA"), can't be compared                |
  | SAMPLE_009  | `quality_score` is blank, "missing value"                            |
  | SAMPLE_010  | not listed in `samples.txt`, excluded silently, no warning           |
  | SAMPLE_011  | fails **two** checks at once: `read_count` (`>=`) and `status` (`=`)  |
  | SAMPLE_012  | fails **three** checks at once: `quality_score`, `error_rate`, `length` |
  | SAMPLE_013  | `quality_score` is whitespace-only (`"   "`), also "missing value" but distinct from SAMPLE_009's genuinely empty field |
  | SAMPLE_014  | a genuinely **short row** in the raw file (only 4 of 8 fields), padded instead of crashing, which surfaces as two independent "missing value" failures (`status`, `length`) |

  SAMPLE_011/012 show that a sample failing multiple columns gets one
  `QCFailure`/report row/warning line **per failing column**, not just
  the first one hit. `evaluate_sample` doesn't short-circuit across
  columns (it does short-circuit *within* one column's own `qc:` list,
  which is why the range check on `read_count` only ever reports whichever
  of `>=`/`<=` it hits first).

- `config.yaml` — one column per operator (`>=`+`<=` combined as a range,
  `>`, `<`, `=`, `~=`), a renamed-but-unchecked pass-through column
  (`lot_number` → `lot`), and a column dropped just by leaving it out of
  the list (`notes`).
- `samples.txt` — requests `SAMPLE_001`–`SAMPLE_009` and `SAMPLE_011`–`SAMPLE_014`,
  deliberately skipping `SAMPLE_010`, plus `SAMPLE_999`, which doesn't
  exist in the input at all.
- `output.tsv` / `qc_report.tsv` — the committed result of the command below.

## Reproduce it

```
limsport \
  --input examples/input.tsv \
  --config examples/config.yaml \
  --samples examples/samples.txt \
  --output examples/output.tsv \
  --qc-report examples/qc_report.tsv
```

This single run touches:
- **Every operator**: `>=`, `<=` (as an AND range), `>`, `<`, `=`, `~=`
- **Every warning**: an unknown name in `--samples` (`SAMPLE_999`), an
  ordinary out-of-range failure, a string mismatch, an approx-tolerance
  failure, a non-numeric-cell failure, and two different ways to trigger
  "missing value" (an empty field and a whitespace-only one)
- **Multi-failure samples**: two and three simultaneous column failures
  on a single sample
- **A malformed row handled gracefully**: a short row gets padded instead
  of crashing, and that itself produces a multi-column failure
- **Column allow-list**: `notes` is dropped for not being listed
- **Renaming**: `read_count`→`total_reads`, `quality_score`→`qc_score`,
  `lot_number`→`lot`
- **Pass-through column**: `lot`, listed but with no `qc:` rules
- **Sample filtering**: `SAMPLE_010` excluded silently (not requested);
  `SAMPLE_999` warned about (requested but not found)
- **The QC report file**: one row per failing sample×column pair

A few more commands cover the rest of what the tool does outside of errors:

```
# Byte-identical fast path: no --config, no --samples at all
limsport --input examples/input.tsv --output /tmp/copy.tsv
# -> "INFO: 14/14 samples included (no QC configured)"
diff examples/input.tsv /tmp/copy.tsv   # empty diff

# Delimiter conversion: same config, written as CSV instead of TSV
limsport --input examples/input.tsv --config examples/config.yaml \
  --samples examples/samples.txt --output /tmp/output.csv --delimiter ,
```

With no `--config` at all, there's no QC to run, so the summary line says
"included" rather than "passed QC" — and `--qc-report` is skipped
entirely rather than writing an empty file, even if you pass it anyway:

```
limsport --input examples/input.tsv --output /tmp/copy.tsv --qc-report /tmp/qc_report.tsv
ls /tmp/qc_report.tsv   # No such file or directory
```

## Error scenarios

Everything above only ever produces *warnings* — the run still finishes
and writes an output file. These three files instead trigger a *hard
error*: `limsport` exits `1`, prints one clean line to stderr (never a
raw traceback), and never creates `--output` at all.

```
# 1. config references a column that doesn't exist in the input header
limsport --input examples/input.tsv --config examples/config_bad_column.yaml --output /tmp/out.tsv
# -> "config references column 'does_not_exist', which is not in the input header"

# 2. config isn't valid YAML syntax at all
limsport --input examples/input.tsv --config examples/config_malformed.yaml --output /tmp/out.tsv
# -> "invalid YAML: while parsing a flow sequence ..."

# 3. a data row has MORE fields than the header (unlike SAMPLE_014's short
#    row above, a long row is never padded or truncated, since guessing
#    which extra field to drop could quietly corrupt data)
limsport --input examples/input_ragged_too_long.tsv --samples examples/samples.txt --output /tmp/out.tsv
# -> "row has 4 columns, expected 2 (based on the header): ['SAMPLE_B', '100', '200', 'extra']"
```

## file_parsing scenario (`file_parsing/`)

A column marked with `file_parsing` treats its cell as a *file path* and
runs a configured command against that file. The command's output
becomes the effective cell value, which then flows through QC and into
the output like any other field. It needs `--allow-file-parsing` on the
CLI even when the config asks for it (see "error scenarios" below).

To make it clear this is genuinely arbitrary-format parsing and not just
"another TSV reader," each of the three `file_parsing` columns here parses
a **different, non-standard format** with a **different tool**:

| column          | file format                                          | tool           |
|------------------|-------------------------------------------------------|----------------|
| `metadata_json`  | JSON, with the value nested three levels deep         | `python3 -c`   |
| `coverage_tsv`   | a *different* TSV schema (mimics `samtools coverage`) | `awk`          |
| `qc_report`      | an invented `key :: value` report format with section markers | `grep` + `cut` + `tr` (piped) |

- `input.tsv` — 4 samples, each pointing at its own JSON/TSV/report trio.
  `SAMPLE_A` passes every check; `SAMPLE_B`/`SAMPLE_C`/`SAMPLE_D` each
  fail exactly one of the three parsed-and-QC'd values, isolating each
  format's extraction the same way the main scenario isolates each
  operator.
- `config.yaml` — uses YAML's literal block scalar (`command: |`) for
  every command instead of a plain or quoted scalar. These commands are
  full of colons (JSON access, `awk -F`, `grep` patterns), which a plain
  YAML scalar would otherwise misread as the start of a mapping key.
- `output.tsv` / `qc_report.tsv` — the committed result of the command below.

```
limsport \
  --input examples/file_parsing/input.tsv \
  --config examples/file_parsing/config.yaml \
  --output examples/file_parsing/output.tsv \
  --qc-report examples/file_parsing/qc_report.tsv \
  --allow-file-parsing
```

Cloud paths (`gs://...`, localized via `gcloud storage cp` before the
command runs, then deleted afterward) aren't demonstrated here since that
needs real cloud credentials and a real bucket. It's covered by mocked
unit tests in `tests/test_file_parsing.py` instead.

### file_parsing error scenarios

Unlike the main scenario's `config_bad_column.yaml`/`config_malformed.yaml`,
these two failure modes are specific to `file_parsing`: a failing command
or a disallowed newline aborts the *entire* export, which is why they
need their own dataset instead of getting mixed into `input.tsv`'s
successful run.

```
# 1. file_parsing used without the safety flag
limsport --input examples/file_parsing/input.tsv --config examples/file_parsing/config.yaml --output /tmp/out.tsv
# -> "config uses file_parsing on column(s) [...], but --allow-file-parsing was not given"

# 2. the command references a JSON key that doesn't exist (a realistic
#    config typo) -- the resulting Python KeyError is a non-zero exit
limsport --input examples/file_parsing/input.tsv --config examples/file_parsing/config_bad_command.yaml --output /tmp/out.tsv --allow-file-parsing
# -> "file_parsing command failed (exit 1) for '...': ...KeyError: 'depth'"

# 3. the command's result contains a newline
limsport --input examples/file_parsing/input.tsv --config examples/file_parsing/config_newline.yaml --output /tmp/out.tsv --allow-file-parsing
# -> "... produced a value containing a newline, which cannot be written to a TSV cell: ..."
```

Both exit `1` and never create `--output`, same as the main scenario's
error cases.
