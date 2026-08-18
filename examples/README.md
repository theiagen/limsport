# LIMSport examples

One consolidated scenario, split by file type into subdirectories (`configs/`, `inputs/`, `outputs/`, `files/`), plus one `run_examples.sh` that runs every command below in order (the successful runs and the deliberate error cases alike) so you can reproduce the whole thing with one command instead of copy-pasting from this file:

```
./examples/run_examples.sh
```

It assumes `limsport` is installed (`pip install -e .` from the repo root) and is safe to re-run: it only ever writes into `/tmp` or back over its own committed files in `outputs/`.

## Everything, built around one real Terra data table

This is a real Terra data table from PHB's `TheiaProk_Illumina_PE` workflow (originally 491 columns / 70 samples), trimmed down to the 15 columns and 11 samples the config below actually exercises. Two of the real samples' cells were deliberately edited, and two more rows are synthetic, added to demonstrate edge cases the real data didn't happen to contain on its own — every edit is called out below and in `config.yaml`'s own header comment.

A second, wholly separate small table (`input_multi_format.tsv` + `config_multi_format.yaml`, further down this page) demonstrates `file_parsing` against two more file formats that don't correspond to anything in a real `TheiaProk_Illumina_PE` table.

### Files

Split by type: `configs/` (YAML), `inputs/` (TSVs and the sample list), `outputs/` (committed golden results), `files/` (per-sample fixtures referenced from inside the TSVs, e.g. the synthetic NTC1/PC1 QUAST reports and the multi-format adjunct's JSON/report files below).

- `inputs/theiaprok_illumina_pe.tsv`: 11 samples — 9 real, 2 synthetic:

  | sample | organism | demonstrates |
  |--------|----------|--------------|
  | `19050801924` | *E. coli* | passes every check |
  | `461023` | *E. coli* | `est_coverage_clean` edited to `"NA"` — non-numeric cell, can't be compared |
  | `SRR16579222_Ecoli_stxPos2completeOperons` | *E. coli* | passes every check; deliberately excluded from `samples.txt` (see below) |
  | `03-98DDCS` | *E. coli* | `fastq_scan_num_reads_clean_pairs` is genuinely blank in the source data → "missing value" |
  | `CL2021-00283104` | *E. coli* | a genuinely fragmented assembly: fails `n50_value`, `number_contigs`, `est_coverage_clean`, *and* the file_parsing-derived `quast_n50` all at once |
  | `155734` | *K. pneumoniae* | passes every check, including `assembly_length`/`quast_n50` under *K. pneumoniae*'s own conditional range |
  | `480757` | *E. coli* | fails only `est_coverage_clean` (28.6x, just under the 30x minimum) |
  | `SAMN24249320` | *P. aeruginosa* | `combined_mean_q_clean` edited to whitespace-only (`"   "`) — also "missing value", but distinct from `03-98DDCS`'s genuinely empty field; otherwise passes, including its own ~6.95 Mb genome under *P. aeruginosa*'s own conditional range |
  | `369711` | *Salmonella enterica* | no conditional-qc rule matches this organism (no `default:`): fails `assembly_length` *and* `quast_n50` with a blank `operator`/`expected` in the report |
  | `NTC1` *(synthetic)* | — | negative control: blank `screening_notes` and a low `raw_read_count`, both expected and passing, gated entirely by `set_qc` |
  | `PC1` *(synthetic)* | — | positive control: `screening_notes` confirms *E. coli* (case-insensitive `contains`), `qc_status` is lowercase `pass` (passes only because `=` is `case_insensitive`) |

  Real QC failures aren't isolated one-per-sample: `CL2021-00283104` fails four unrelated metrics at once, and `369711` fails because its organism simply isn't covered by any conditional-qc rule. Only `19050801924`, `155734`, `NTC1`, and `PC1` pass every check and appear in `outputs/output.tsv`.

- `configs/config.yaml`: keeps 15 of the real table's 491 columns (17 in the output, once `quast_report`'s `file_parsing` expands to three). It exercises:
  - **Every QC operator**: `>=`/`<=` (as a plain AND range, on `number_contigs`), `>` (`n50_value`), `~=` (`combined_mean_q_clean`), `=` with `case_insensitive: true` (`qc_status`), `contains`/`does_not_contain`/`is_empty`/`is_not_empty` (all four via `set_qc`, below)
  - **Conditional qc**: `assembly_length` and file_parsing output `quast_n50` both pick their threshold from `gambit_predicted_taxon` (kept as a pass-through `predicted_taxon` column and used as the match key) instead of one fixed range for every organism. There's deliberately no `default:` on either — `369711` (*Salmonella enterica*) has no matching rule for either check, which surfaces as a real QC failure ("no qc rule matches...") rather than a silent pass
  - **A pass-through column** (`assembler`, never QC'd) and a **renamed-but-unchecked** one (`sequencing_platform` → `platform`)
  - **`file_parsing` against a real `gs://` file**: `quast_report` downloads each real sample's actual QUAST report from Google Cloud Storage once (local files for the synthetic `NTC1`/`PC1`) and runs three independent `awk` commands against it (`quast_n50`, `quast_gc_pct`, `quast_total_length`), cross-validating the extracted values against the native `n50`/`assembly_length` columns pulled from the same row
  - **`set_qc` (run-level QC)**, gating columns (`screening_notes`, `raw_read_count`, `notes`) that deliberately have **no** per-column `qc:` — a single fixed rule can't express "blank for the negative control, non-blank and contamination-free for everyone else":

    | rule | match kind | columns checked | operators |
    |------|-----------|------------------|-----------|
    | "NTC has no organism flagged and low raw read count" | `sample_pattern: "NTC"` | `screening_notes`, `raw_read_count` | `is_empty`, `<=` |
    | "Positive control organism identity confirmed" | `sample_regex: "^PC-?\d*$"` | `screening_notes`, `notes` | `contains` (case-insensitive), `is_not_empty` |
    | "No cross-contamination flagged in real samples" | `samples: [...]` (exact list) | `screening_notes` | `does_not_contain` (case-insensitive) |

    Each rule shows a different match kind, and the first two show a single rule checking **more than one column** under the same match.
  - **Column allow-list at real scale**: 491 real columns down to 15, i.e. every other real column is dropped just by never being mentioned

- `inputs/samples.txt`: requests the 8 real samples used in the failure catalog above plus `NTC1`/`PC1`, deliberately omitting `SRR16579222_Ecoli_stxPos2completeOperons` (excluded silently, not requested — it would otherwise pass identically to `19050801924`), plus `SAMPLE_DOES_NOT_EXIST` (requested but not found, generates a warning, not an error).
- `outputs/output.tsv` / `outputs/qc_report.tsv`: the committed result of the command below.

### Reproduce it

```
limsport \
  --input examples/inputs/theiaprok_illumina_pe.tsv \
  --config examples/configs/config.yaml \
  --samples examples/inputs/samples.txt \
  --output examples/outputs/output.tsv \
  --qc-report examples/outputs/qc_report.tsv \
  --allow-file-parsing
```

This needs `gcloud` installed and authenticated with read access to the referenced bucket to reproduce the real `gs://` file_parsing in full; `run_examples.sh` skips this (and the localization-failure demo below) rather than counting it as a failure if `gcloud` isn't on `PATH`.

A few more commands cover the rest of what the tool does outside of errors:

```
# Nothing-to-do path: no --config, no --samples, no --delimiter at all.
# Nothing would change, so nothing is written, not even --output
limsport --input examples/inputs/theiaprok_illumina_pe.tsv --output /tmp/copy.tsv
# -> "INFO: no config, samples, or delimiter change given; nothing to do"
ls /tmp/copy.tsv   # No such file or directory

# With no --config at all, there's no QC to run, so --qc-report is skipped
# entirely rather than writing an empty file, even if you pass it anyway
limsport --input examples/inputs/theiaprok_illumina_pe.tsv --output /tmp/copy.tsv --qc-report /tmp/qc_report.tsv
ls /tmp/qc_report.tsv   # No such file or directory

# Delimiter conversion: same config, written as CSV instead of TSV
limsport --input examples/inputs/theiaprok_illumina_pe.tsv --config examples/configs/config.yaml \
  --samples examples/inputs/samples.txt --output /tmp/output.csv --delimiter , --allow-file-parsing
```

### A `set_qc` failure fails the *entire* run

`input_ntc_contaminated.tsv` is identical to `theiaprok_illumina_pe.tsv` except `NTC1`'s `raw_read_count` is `5000` instead of `800` — above the set_qc rule's `<= 1000` threshold. Unlike an ordinary per-row `qc:` failure (which only drops that one sample), this drops **every** sample: the offending sample gets a full-detail report row, and every other sample gets one collateral row naming the rule that failed the run.

```
limsport \
  --input examples/inputs/input_ntc_contaminated.tsv \
  --config examples/configs/config.yaml \
  --samples examples/inputs/samples.txt \
  --output examples/outputs/output_ntc_contaminated.tsv \
  --qc-report examples/outputs/qc_report_ntc_contaminated.tsv \
  --allow-file-parsing
```

`output_ntc_contaminated.tsv` ends up header-only (0 of 10 requested samples pass); `qc_report_ntc_contaminated.tsv` has `NTC1`'s full-detail failure plus one collateral row per other sample:

```
sample        column          output_column   operator  expected  actual  reason
NTC1          raw_read_count  raw_read_count  <=        1000      5000    5000.0 <= 1000 is False
19050801924                                                                run failed QC due to set_qc rule(s): ['NTC has no organism flagged and low raw read count']
```

(Every other requested sample gets the same collateral row as `19050801924`.)

### Bonus: an omitted `columns:`

`config_columns_omitted.yaml` has no `columns:` key at all — the config exists purely for its `set_qc` rule, so every input column passes through unchanged (same as running with no `--config` at all), while `set_qc` still gates the run:

```
limsport --input examples/inputs/theiaprok_illumina_pe.tsv --config examples/configs/config_columns_omitted.yaml --output /tmp/passthrough.tsv
```

### Confirming a localization failure surfaces Google's own error

`file_parsing`'s `_localize` step shells out to `gcloud storage cp` and, on a non-zero exit, wraps *gcloud's own stderr text* into the `FileParsingError` it raises. It never rewrites or swallows it: if whoever runs this config doesn't have read access to the bucket a sample's file lives in (or the path is wrong), they need Google's own explanation to know what to fix (request bucket access, fix a typo, etc.), not a generic "failed" message.

`input_forbidden_bucket.tsv` + `config_forbidden_bucket.yaml` demonstrate this against a real, nonexistent bucket (the same code path a genuine permission error takes: both are just a non-zero `gcloud` exit with a message on stderr):

```
limsport \
  --input examples/inputs/input_forbidden_bucket.tsv \
  --config examples/configs/config_forbidden_bucket.yaml \
  --output /tmp/out.tsv \
  --allow-file-parsing
# -> "gs://this-bucket-does-not-exist-or-you-lack-access-to-it/report.tsv:
#     failed to localize with gcloud storage cp (exit 1): ERROR:
#     (gcloud.storage.cp) gs://this-bucket-does-not-exist-or-you-lack-access-to-it
#     not found: 404."
```

The message above is gcloud's real, unedited stderr output: exit code `1`, no `--output` file created. A real 403 permission-denied error from a bucket the caller lacks access to would surface the same way, with Google's own permission-denied text in place of the 404.

### Error scenarios

Everything above only ever produces *warnings*: the run still finishes and writes an output file. These instead trigger a *hard error*: `limsport` exits `1`, prints one clean line to stderr (never a raw traceback), and never creates `--output` at all.

```
# 1. config references a column that doesn't exist in the input header
limsport --input examples/inputs/theiaprok_illumina_pe.tsv --config examples/configs/config_bad_column.yaml --output /tmp/out.tsv
# -> "config references column 'assembly_lenght', which is not in the input header"

# 2. config isn't valid YAML syntax at all
limsport --input examples/inputs/theiaprok_illumina_pe.tsv --config examples/configs/config_malformed.yaml --output /tmp/out.tsv
# -> "invalid YAML: while parsing a flow sequence ..."

# 3. a data row has more fields than the header (never padded or
#    truncated, since guessing which extra field to drop could quietly
#    corrupt data)
limsport --input examples/inputs/input_ragged_too_long.tsv --samples examples/inputs/samples.txt --output /tmp/out.tsv
# -> "row has 4 columns, expected 2 (based on the header): ['SAMPLE_B', '100', '200', 'extra']"

# 4. a set_qc rule matches zero samples in the run -- a hard error (no
#    sample to attach a QC failure to), not a QC failure
limsport --input examples/inputs/theiaprok_illumina_pe.tsv --config examples/configs/config_zero_match.yaml --output /tmp/out.tsv
# -> "set_qc rule 'NTC has no organism flagged' matched no samples in this run"

# 5. an explicit `columns: []` is always rejected, even with set_qc
#    configured -- unlike omitting the key, an empty list looks like a
#    mistake, not a deliberate "pass everything through"
limsport --input examples/inputs/theiaprok_illumina_pe.tsv --config examples/configs/config_empty_columns.yaml --output /tmp/out.tsv
# -> "config 'columns' must not be empty; omit it entirely to pass every
#     input column through unfiltered instead"

# 6. main config's file_parsing used without the safety flag
limsport --input examples/inputs/theiaprok_illumina_pe.tsv --config examples/configs/config.yaml --samples examples/inputs/samples.txt --output /tmp/out.tsv
# -> "config uses file_parsing on column(s) ['quast_report'], but --allow-file-parsing was not given"
```

## Multi-format adjunct: `file_parsing` against JSON and an invented report format

`quast_report` above only demonstrates one non-TSV-ish format (a real QUAST report, parsed with `awk`). This small, separate scenario — reusing the same `configs/`/`inputs/`/`outputs/`/`files/` subdirectories — demonstrates `file_parsing` against **two more genuinely different formats**, each with a **different tool**, plus the multi-output case:

| column          | file format                                          | tool           |
|------------------|-------------------------------------------------------|----------------|
| `metadata_json`  | JSON, with the value nested three levels deep         | `python3 -c`   |
| `coverage_tsv`   | a *different* TSV schema (mimics `samtools coverage`) | `awk` (two outputs) |
| `qc_report`      | an invented `key :: value` report format with section markers | `grep` + `cut` + `tr` (piped) |

`coverage_tsv` is the multi-output example: `file_parsing` is a list there with **two** entries (`chr1_meandepth` and `chr1_coverage_pct`), each its own `awk` command and its own `qc:`, pulling two different columns out of the same chr1 row of one file. `metadata_json`/`qc_report` use single-entry lists instead.

- `inputs/input_multi_format.tsv`: 4 samples (`SAMPLE_A`-`SAMPLE_D`), each pointing at its own JSON/TSV/report trio (the `SAMPLE_A_*`/`SAMPLE_B_*`/`SAMPLE_C_*`/`SAMPLE_D_*` files in `files/`). `SAMPLE_A` passes every check; `SAMPLE_B`/`SAMPLE_C`/`SAMPLE_D` each fail exactly one of the four parsed-and-QC'd values, isolating each format's extraction.
- `configs/config_multi_format.yaml`: uses YAML's literal block scalar (`command: |`) for every command instead of a plain or quoted scalar. These commands are full of colons (JSON access, `awk -F`, `grep` patterns), which a plain YAML scalar would otherwise misread as the start of a mapping key.
- `outputs/output_multi_format.tsv` / `outputs/qc_report_multi_format.tsv`: the committed result of the command below.

```
limsport \
  --input examples/inputs/input_multi_format.tsv \
  --config examples/configs/config_multi_format.yaml \
  --output examples/outputs/output_multi_format.tsv \
  --qc-report examples/outputs/qc_report_multi_format.tsv \
  --allow-file-parsing
```

### Error scenarios

```
# 7. file_parsing used without the safety flag
limsport --input examples/inputs/input_multi_format.tsv --config examples/configs/config_multi_format.yaml --output /tmp/out.tsv
# -> "config uses file_parsing on column(s) [...], but --allow-file-parsing was not given"

# 8. the command references a JSON key that doesn't exist (a realistic
#    config typo): the resulting Python KeyError is a non-zero exit
limsport --input examples/inputs/input_multi_format.tsv --config examples/configs/config_multi_format_bad_command.yaml --output /tmp/out.tsv --allow-file-parsing
# -> "file_parsing command failed (exit 1) for '...': ...KeyError: 'depth'"

# 9. the command's result contains a newline
limsport --input examples/inputs/input_multi_format.tsv --config examples/configs/config_multi_format_newline.yaml --output /tmp/out.tsv --allow-file-parsing
# -> "... produced a value containing a newline, which cannot be written to a TSV cell: ..."
```

Both exit `1` and never create `--output`, same as every other error scenario above.
