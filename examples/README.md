# LIMSport examples

`run_examples.sh` reproduces a successful run, a set qc failure run, and a run that skips sample-level QC for some samples.

## Files

- `configs/config.yaml` — every QC operator, conditional (per-organism) QC, a pass-through column, a renamed column, `file_parsing` against a local QUAST report, `set_qc` run-level rules for the controls, and `skip_sample_qc` listing their samples.
- `configs/config_skip_sample_qc.yaml` — a smaller config (no `file_parsing`) for `skip_sample_qc`: `480757` is listed directly; `NTC1` and the three real samples of the contamination rule (`19050801924`, `461023`, `CL2021-00283104`) are skipped because their `set_qc` rules have `include_sample_qc` off; `PC1` keeps its sample-level QC because the PC rule has `include_sample_qc: true`.
- `inputs/theiaprok_illumina_pe.tsv` — 11 samples (9 real + synthetic `NTC1`/`PC1` controls) from a real `TheiaProk_Illumina_PE` Terra table.
- `inputs/input_ntc_contaminated.tsv` — same table, except `NTC1`'s `raw_read_count` is raised to `5000`, over the `set_qc` threshold.
- `inputs/samples.txt` — sample list passed via `--samples`.
- `files/*_quast_report.tsv` — local QUAST reports for every sample (the 9 real samples' reports were pulled once from their `gs://` source and committed, so running the examples needs no cloud access).
- `outputs/output_lims_report.tsv`, `outputs/output_qc_fail_report.tsv`, `outputs/output_qc_summary.tsv` — committed result of the successful run.
- `outputs/output_ntc_contaminated_lims_report.tsv`, `outputs/output_ntc_contaminated_qc_fail_report.tsv`, `outputs/output_ntc_contaminated_qc_summary.tsv` — committed result of the set_qc-failure run.
- `outputs/output_skip_sample_qc_lims_report.tsv`, `outputs/output_skip_sample_qc_qc_fail_report.tsv`, `outputs/output_skip_sample_qc_qc_summary.tsv` — committed result of the skip sample-level QC run.

## Expected end state

- **Successful run**: `set_qc_status` is `PASS` for every sample. `19050801924` and `155734` pass sample-level QC; `NTC1` and `PC1` are `SKIPPED` in `sample_qc_status` (their `set_qc` rules have `include_sample_qc` off). The rest each fail at least one sample-level QC check.
- **set_qc-failure run**: `NTC1`'s `raw_read_count` fails its `set_qc` rule, which fails the whole run, so `set_qc_status` is `FAIL` for every sample and `output_ntc_contaminated_lims_report.tsv` ends up header-only. `sample_qc_status` still shows each sample's own sample-level result (`NTC1` and `PC1` are `SKIPPED`).
- **skip sample-level QC run**: `set_qc_status` is `PASS` for every sample. `480757`, `NTC1`, and the contamination rule's `19050801924`, `461023`, and `CL2021-00283104` are `SKIPPED` in `sample_qc_status` and are written to the output, though `480757`, `461023`, and `CL2021-00283104` would fail `coverage`. `PC1` is `PASS` after its sample-level QC (the PC rule's `include_sample_qc: true`). Only `03-98DDCS` fails (`read_pairs` is missing).
