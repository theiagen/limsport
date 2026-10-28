#!/usr/bin/env bash

echo "=== successful run ==="
echo ""

limsport \
    --input inputs/theiaprok_illumina_pe.tsv \
    --config configs/config.yaml \
    --samples inputs/samples.txt \
    --output-prefix outputs/output \
    --allow-file-parsing

echo ""
echo "=== set qc failure ==="
echo ""

limsport \
    --input inputs/input_ntc_contaminated.tsv \
    --config configs/config.yaml \
    --samples inputs/samples.txt \
    --output-prefix outputs/output_ntc_contaminated \
    --allow-file-parsing
