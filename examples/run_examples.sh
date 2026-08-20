#!/usr/bin/env bash
# Runs every command documented in examples/README.md -- this single
# directory (split into configs/, inputs/, outputs/, files/) replaces the
# four separate example directories that used to exist (basic/,
# file_parsing/, set_qc/, theiaprok_illumina_pe/), consolidated around one
# real Terra data table.
#
# In order: the main run (real gs:// file_parsing + conditional qc + every
# plain QC operator + set_qc, all together), the extra fast-path/delimiter
# demos, the whole-run-fails set_qc demo, a bonus columns-omitted
# pass-through run, the multi-format file_parsing adjunct scenario, and
# every documented hard-error scenario.
#
# The main run and the forbidden-bucket demo both shell out to real
# `gcloud storage cp` calls against real gs:// paths (a real, public/
# workspace bucket, plus one deliberately nonexistent one) -- they need
# `gcloud` installed and authenticated with read access to reproduce in
# full. If `gcloud` isn't on PATH at all, those two are skipped (not
# counted as failures) rather than run against a tool that can't possibly
# work; if it's present but unauthenticated or offline, they'll fail for
# real, which itself is expected -- see examples/README.md's "Confirming a
# localization failure surfaces Google's own error" section.
#
# Safe to re-run -- it only writes back over this folder's own committed
# outputs/*.tsv (which should come out byte-identical) or into a scratch
# dir under /tmp.
#
# Runs from the repo root (like every command in examples/README.md), not
# from this script's own directory -- inputs/input_multi_format.tsv's
# file_parsing columns hold paths relative to the repo root.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
CONFIGS=examples/configs
INPUTS=examples/inputs
OUTPUTS=examples/outputs

if ! command -v limsport >/dev/null 2>&1; then
    echo "error: 'limsport' not found on PATH -- run 'pip install -e .' from the repo root first" >&2
    exit 1
fi

HAVE_GCLOUD=1
if ! command -v gcloud >/dev/null 2>&1; then
    HAVE_GCLOUD=0
    echo "warning: 'gcloud' not found on PATH -- skipping the real gs:// file_parsing demos" >&2
    echo
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0
fail=0
skipped=0

run_ok() {
    local name="$1"; shift
    echo "=== $name ==="
    if "$@"; then
        echo "--- ok ---"
        pass=$((pass + 1))
    else
        echo "--- FAILED (expected success, got exit $?) ---" >&2
        fail=$((fail + 1))
    fi
    echo
}

run_fail() {
    local name="$1"; shift
    echo "=== $name (expected to fail) ==="
    if "$@"; then
        echo "--- FAILED (expected a non-zero exit, but it succeeded) ---" >&2
        fail=$((fail + 1))
    else
        echo "--- failed as expected ---"
        pass=$((pass + 1))
    fi
    echo
}

skip() {
    echo "=== $1 (skipped: no gcloud on PATH) ==="
    echo
    skipped=$((skipped + 1))
}

if [ "$HAVE_GCLOUD" -eq 1 ]; then
    run_ok "main run: every QC operator, conditional qc, real gs:// file_parsing, and set_qc together" \
        limsport --input "$INPUTS/theiaprok_illumina_pe.tsv" --config "$CONFIGS/config.yaml" --samples "$INPUTS/samples.txt" \
            --output "$OUTPUTS/output.tsv" --qc-report "$OUTPUTS/qc_report.tsv" --allow-file-parsing

    run_ok "whole-run-fails: NTC1's raw read count violates its set_qc rule, zeroing the entire output" \
        limsport --input "$INPUTS/input_ntc_contaminated.tsv" --config "$CONFIGS/config.yaml" --samples "$INPUTS/samples.txt" \
            --output "$OUTPUTS/output_ntc_contaminated.tsv" --qc-report "$OUTPUTS/qc_report_ntc_contaminated.tsv" --allow-file-parsing
    if [ -s "$OUTPUTS/output_ntc_contaminated.tsv" ] && [ "$(wc -l < "$OUTPUTS/output_ntc_contaminated.tsv")" -gt 1 ]; then
        echo "(WARNING: output_ntc_contaminated.tsv has data rows -- the whole run should have failed)" >&2
    else
        echo "(confirmed: only the header was written -- the whole run failed as expected)"
    fi
    echo

    # the bucket is unreadable for the only sample in this input, so every row
    # fails and the guard aborts rather than writing an empty table
    run_fail "localization failure: a nonexistent/inaccessible gs:// bucket fails every row" \
        limsport --input "$INPUTS/input_forbidden_bucket.tsv" --config "$CONFIGS/config_forbidden_bucket.yaml" \
            --output "$TMP/out.tsv" --allow-file-parsing

    run_ok "delimiter conversion (same main config, written as CSV -- needs gcloud, same as the main run)" \
        limsport --input "$INPUTS/theiaprok_illumina_pe.tsv" --config "$CONFIGS/config.yaml" --samples "$INPUTS/samples.txt" \
            --output "$TMP/output.csv" --qc-report "$TMP/output_csv_qc_report.tsv" --delimiter , --allow-file-parsing
else
    skip "main run: every QC operator, conditional qc, real gs:// file_parsing, and set_qc together"
    skip "whole-run-fails: NTC1's raw read count violates its set_qc rule, zeroing the entire output"
    skip "localization failure: a nonexistent/inaccessible gs:// bucket fails every row"
    skip "delimiter conversion (same main config, written as CSV -- needs gcloud, same as the main run)"
fi

run_ok "nothing-to-do path (no --config, no --samples, no --delimiter)" \
    limsport --input "$INPUTS/theiaprok_illumina_pe.tsv" --output "$TMP/copy.tsv"
if [ -e "$TMP/copy.tsv" ]; then
    echo "(WARNING: copy.tsv was written even though nothing would have changed)" >&2
else
    echo "(confirmed: nothing was written)"
fi
echo

run_ok "no --config: --qc-report is skipped, not written empty" \
    limsport --input "$INPUTS/theiaprok_illumina_pe.tsv" --output "$TMP/copy.tsv" --qc-report "$TMP/qc_report.tsv"
if [ -e "$TMP/qc_report.tsv" ]; then
    echo "(WARNING: qc_report.tsv was written even though no QC ran)" >&2
else
    echo "(confirmed: no qc_report.tsv written)"
fi
echo

run_ok "bonus: columns omitted entirely -- every column passes through unchanged, set_qc still gates" \
    limsport --input "$INPUTS/theiaprok_illumina_pe.tsv" --config "$CONFIGS/config_columns_omitted.yaml" \
        --output "$TMP/passthrough.tsv" --qc-report "$TMP/passthrough_qc_report.tsv"

run_ok "bonus: columns-omitted config still fails the WHOLE run when set_qc fails (not just a silent pass-through)" \
    limsport --input "$INPUTS/input_ntc_contaminated.tsv" --config "$CONFIGS/config_columns_omitted.yaml" \
        --output "$TMP/passthrough_failed.tsv" --qc-report "$TMP/passthrough_failed_qc_report.tsv"
if [ -s "$TMP/passthrough_failed.tsv" ] && [ "$(wc -l < "$TMP/passthrough_failed.tsv")" -gt 1 ]; then
    echo "(WARNING: passthrough_failed.tsv has data rows -- the whole run should have failed)" >&2
else
    echo "(confirmed: only the header was written -- set_qc gates even with columns: omitted)"
fi
echo

run_ok "multi-format adjunct: JSON/custom-TSV/invented-report file_parsing, single- and multi-output" \
    limsport --input "$INPUTS/input_multi_format.tsv" --config "$CONFIGS/config_multi_format.yaml" \
        --output "$OUTPUTS/output_multi_format.tsv" --qc-report "$OUTPUTS/qc_report_multi_format.tsv" --allow-file-parsing

run_ok "partial file_parsing failure: SAMPLE_B's metadata file is missing, so only that row drops" \
    limsport --input "$INPUTS/input_multi_format_one_missing.tsv" --config "$CONFIGS/config_one_missing_file.yaml" \
        --output "$TMP/out_one_missing.tsv" --qc-report "$TMP/qc_one_missing.tsv" --allow-file-parsing

run_ok "  ^ confirm SAMPLE_A/C/D were still written and SAMPLE_B is in the QC report" \
    bash -c 'grep -q SAMPLE_A "$1" && grep -q SAMPLE_C "$1" && grep -q SAMPLE_D "$1" \
        && ! grep -q SAMPLE_B "$1" && grep -q SAMPLE_B "$2"' _ "$TMP/out_one_missing.tsv" "$TMP/qc_one_missing.tsv"

run_fail "  ^ and --max-file-parsing-failures 0 turns that same partial failure fatal" \
    limsport --input "$INPUTS/input_multi_format_one_missing.tsv" --config "$CONFIGS/config_one_missing_file.yaml" \
        --output "$TMP/out.tsv" --allow-file-parsing --max-file-parsing-failures 0

run_fail "error 1: config references a column that doesn't exist" \
    limsport --input "$INPUTS/theiaprok_illumina_pe.tsv" --config "$CONFIGS/config_bad_column.yaml" --output "$TMP/out.tsv"

run_fail "error 2: config isn't valid YAML" \
    limsport --input "$INPUTS/theiaprok_illumina_pe.tsv" --config "$CONFIGS/config_malformed.yaml" --output "$TMP/out.tsv"

run_fail "error 3: a data row has more fields than the header" \
    limsport --input "$INPUTS/input_ragged_too_long.tsv" --samples "$INPUTS/samples.txt" --output "$TMP/out.tsv"

run_fail "error 4: a set_qc rule matches zero samples in the run" \
    limsport --input "$INPUTS/theiaprok_illumina_pe.tsv" --config "$CONFIGS/config_zero_match.yaml" --output "$TMP/out.tsv"

run_fail "error 5: an explicit columns: [] is always rejected, even with set_qc configured" \
    limsport --input "$INPUTS/theiaprok_illumina_pe.tsv" --config "$CONFIGS/config_empty_columns.yaml" --output "$TMP/out.tsv"

run_fail "error 6: multi-format file_parsing used without --allow-file-parsing" \
    limsport --input "$INPUTS/input_multi_format.tsv" --config "$CONFIGS/config_multi_format.yaml" --output "$TMP/out.tsv"

# A file_parsing failure fails its own row's QC, not the run -- but these two
# commands are broken for EVERY sample, so the all-rows-failed guard still aborts
# them. See the partial-failure run above for the row-level behaviour.
run_fail "error 7: a command referencing a missing JSON key fails every row, so the run aborts" \
    limsport --input "$INPUTS/input_multi_format.tsv" --config "$CONFIGS/config_multi_format_bad_command.yaml" \
        --output "$TMP/out.tsv" --allow-file-parsing

run_fail "error 8: a command whose result contains a newline fails every row, so the run aborts" \
    limsport --input "$INPUTS/input_multi_format.tsv" --config "$CONFIGS/config_multi_format_newline.yaml" \
        --output "$TMP/out.tsv" --allow-file-parsing

run_fail "error 9: main config's file_parsing used without --allow-file-parsing" \
    limsport --input "$INPUTS/theiaprok_illumina_pe.tsv" --config "$CONFIGS/config.yaml" --samples "$INPUTS/samples.txt" --output "$TMP/out.tsv"

echo "$pass ok, $fail unexpected, $skipped skipped"
[ "$fail" -eq 0 ]
