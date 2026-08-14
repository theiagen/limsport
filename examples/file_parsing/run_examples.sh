#!/usr/bin/env bash
# Runs every command documented in examples/README.md's "file_parsing/"
# section, in order: the main successful run, then the three deliberate
# hard-error scenarios (missing --allow-file-parsing, a failing command,
# an embedded newline). Safe to re-run -- it only writes back over this
# folder's own committed output.tsv/qc_report.tsv (which should come out
# byte-identical) or into a scratch dir under /tmp.
#
# Runs from the repo root (like every command in examples/README.md), not
# from this script's own directory -- input.tsv's file_parsing columns
# hold paths relative to the repo root (e.g. "examples/file_parsing/
# SAMPLE_A_metadata.json"), so `limsport` itself must run from there too.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
DIR=examples/file_parsing

if ! command -v limsport >/dev/null 2>&1; then
    echo "error: 'limsport' not found on PATH -- run 'pip install -e .' from the repo root first" >&2
    exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0
fail=0

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

run_ok "main run: JSON/TSV/report file_parsing, single- and multi-output" \
    limsport --input "$DIR/input.tsv" --config "$DIR/config.yaml" \
        --output "$DIR/output.tsv" --qc-report "$DIR/qc_report.tsv" --allow-file-parsing

run_fail "error 1: file_parsing used without --allow-file-parsing" \
    limsport --input "$DIR/input.tsv" --config "$DIR/config.yaml" --output "$TMP/out.tsv"

run_fail "error 2: the command references a JSON key that doesn't exist" \
    limsport --input "$DIR/input.tsv" --config "$DIR/config_bad_command.yaml" \
        --output "$TMP/out.tsv" --allow-file-parsing

run_fail "error 3: the command's result contains a newline" \
    limsport --input "$DIR/input.tsv" --config "$DIR/config_newline.yaml" \
        --output "$TMP/out.tsv" --allow-file-parsing

echo "$pass ok, $fail unexpected"
[ "$fail" -eq 0 ]
