# LIMSport examples

`run_examples.sh` reproduces a successful run and a set qc failure run.

## Files

- `configs/config.yaml` — every QC operator, conditional (per-organism) QC, a pass-through column, a renamed column, `file_parsing` against a local QUAST report, and `set_qc` run-level rules.
- `inputs/theiaprok_illumina_pe.tsv` — 11 samples (9 real + synthetic `NTC1`/`PC1` controls) from a real `TheiaProk_Illumina_PE` Terra table.
- `inputs/input_ntc_contaminated.tsv` — same table, except `NTC1`'s `raw_read_count` is raised to `5000`, over the `set_qc` threshold.
- `inputs/samples.txt` — sample list passed via `--samples`.
- `files/*_quast_report.tsv` — local QUAST reports for every sample (the 9 real samples' reports were pulled once from their `gs://` source and committed, so running the examples needs no cloud access).
- `outputs/output.tsv`, `outputs/qc_report.tsv` — committed result of the successful run.
- `outputs/output_ntc_contaminated.tsv`, `outputs/qc_report_ntc_contaminated.tsv` — committed result of the set_qc-failure run.

## Expected end state

- **Successful run**: 4 of 10 requested samples pass (`19050801924`, `155734`, `NTC1`, `PC1`); the rest each fail at least one QC check.
- **set_qc-failure run**: 0 of 10 samples pass — `NTC1`'s `raw_read_count` fails its `set_qc` rule, which fails the whole run, so `output_ntc_contaminated.tsv` ends up header-only.
