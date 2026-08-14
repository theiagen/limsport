#!/usr/bin/env bash
# Runs every command documented in examples/README.md's
# "theiaprok_illumina_pe/" section: the main run (real gs:// file_parsing
# against a real 491-column Terra data table), the localization-failure
# demo, the conditional-qc demo, and the two hard-error scenarios.
#
# The main run, the localization-failure demo, and the conditional-qc run
# all shell out to real `gcloud storage cp` calls against real gs:// paths
# (two real, public/workspace buckets, plus one deliberately nonexistent
# one) -- they need `gcloud` installed and authenticated with read access
# to reproduce in full. If `gcloud` isn't on PATH at all, those three are
# skipped (not counted as failures) rather than run against a tool that
# can't possibly work; if it's present but unauthenticated or offline,
# they'll fail for real, which itself is expected -- see examples/README.md's
# "Confirming a localization failure surfaces Google's own error" section.
#
# Safe to re-run -- it only writes back over this folder's own committed
# output*.tsv/qc_report*.tsv (which should come out byte-identical) or
# into a scratch dir under /tmp.
#
# Runs from the repo root (like every command in examples/README.md), not
# from this script's own directory.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
DIR=examples/theiaprok_illumina_pe

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
    run_ok "main run: real gs:// file_parsing (single- and multi-output) against a 491-column table" \
        limsport --input "$DIR/theiaprok_illumina_pe.tsv" --config "$DIR/config.yaml" --samples "$DIR/samples.txt" \
            --output "$DIR/output.tsv" --qc-report "$DIR/qc_report.tsv" --allow-file-parsing

    run_fail "localization failure: a nonexistent/inaccessible gs:// bucket" \
        limsport --input "$DIR/input_forbidden_bucket.tsv" --config "$DIR/config_forbidden_bucket.yaml" \
            --output "$TMP/out.tsv" --allow-file-parsing

    run_ok "conditional qc: organism-specific thresholds on a plain column and a file_parsing output" \
        limsport --input "$DIR/theiaprok_illumina_pe.tsv" --config "$DIR/config_conditional_qc.yaml" \
            --samples "$DIR/samples_conditional_qc.txt" --output "$DIR/output_conditional_qc.tsv" \
            --qc-report "$DIR/qc_report_conditional_qc.tsv" --allow-file-parsing
else
    skip "main run: real gs:// file_parsing against a 491-column table"
    skip "localization failure: a nonexistent/inaccessible gs:// bucket"
    skip "conditional qc: organism-specific thresholds on a plain column and a file_parsing output"
fi

run_fail "error 1: a config column name typo (\"assembly_lenght\")" \
    limsport --input "$DIR/theiaprok_illumina_pe.tsv" --config "$DIR/config_bad_column.yaml" --output "$TMP/out.tsv"

run_fail "error 2: file_parsing used without --allow-file-parsing" \
    limsport --input "$DIR/theiaprok_illumina_pe.tsv" --config "$DIR/config.yaml" --samples "$DIR/samples.txt" --output "$TMP/out.tsv"

echo "$pass ok, $fail unexpected, $skipped skipped"
[ "$fail" -eq 0 ]
