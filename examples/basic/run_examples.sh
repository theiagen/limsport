#!/usr/bin/env bash
# Runs every command documented in examples/README.md's "basic/" section,
# in order: the main successful run, the extra fast-path/delimiter demos,
# and the three deliberate hard-error scenarios. Safe to re-run -- it only
# writes back over this folder's own committed output.tsv/qc_report.tsv
# (which should come out byte-identical) or into a scratch dir under /tmp.
#
# Runs from the repo root (like every command in examples/README.md),
# not from this script's own directory.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
DIR=examples/basic

if ! command -v limsport >/dev/null 2>&1; then
    echo "error: 'limsport' not found on PATH -- run 'pip install -e .' from the repo root first" >&2
    exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0
fail=0

# run_ok NAME CMD...  -- run CMD, expecting it to succeed (exit 0)
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

# run_fail NAME CMD...  -- run CMD, expecting a non-zero exit (one of the
# documented hard-error scenarios)
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

run_ok "main run: every operator, rename, allow-list, sample filtering, QC report" \
    limsport --input "$DIR/input.tsv" --config "$DIR/config.yaml" --samples "$DIR/samples.txt" \
        --output "$DIR/output.tsv" --qc-report "$DIR/qc_report.tsv"

run_ok "nothing-to-do path (no --config, no --samples, no --delimiter)" \
    limsport --input "$DIR/input.tsv" --output "$TMP/copy.tsv"
if [ -e "$TMP/copy.tsv" ]; then
    echo "(WARNING: copy.tsv was written even though nothing would have changed)" >&2
else
    echo "(confirmed: nothing was written)"
fi
echo

run_ok "delimiter conversion (same config, written as CSV)" \
    limsport --input "$DIR/input.tsv" --config "$DIR/config.yaml" --samples "$DIR/samples.txt" \
        --output "$TMP/output.csv" --delimiter ,

run_ok "no --config: --qc-report is skipped, not written empty" \
    limsport --input "$DIR/input.tsv" --output "$TMP/copy.tsv" --qc-report "$TMP/qc_report.tsv"
if [ -e "$TMP/qc_report.tsv" ]; then
    echo "(WARNING: qc_report.tsv was written even though no QC ran)" >&2
else
    echo "(confirmed: no qc_report.tsv written)"
fi
echo

run_fail "error 1: config references a column that doesn't exist" \
    limsport --input "$DIR/input.tsv" --config "$DIR/config_bad_column.yaml" --output "$TMP/out.tsv"

run_fail "error 2: config isn't valid YAML" \
    limsport --input "$DIR/input.tsv" --config "$DIR/config_malformed.yaml" --output "$TMP/out.tsv"

run_fail "error 3: a data row has more fields than the header" \
    limsport --input "$DIR/input_ragged_too_long.tsv" --samples "$DIR/samples.txt" --output "$TMP/out.tsv"

echo "$pass ok, $fail unexpected"
[ "$fail" -eq 0 ]
