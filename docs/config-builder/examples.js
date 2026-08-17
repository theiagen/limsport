/**
 * Wizard-state builders for the three scenarios in ../examples/. Shared by
 * app.js (the "load example" buttons) and tests/schema.test.mjs (which
 * proves each one, once run through buildConfig()+serializeYAML(), is
 * semantically identical to the real example/config.yaml fixture).
 */

import { newColumn, newCondition, newRule, newFileParsingOutput } from "./schema.js";

export function condition(operator, value, tolerancePercent) {
  const c = newCondition();
  c.operator = operator;
  c.value = String(value);
  if (tolerancePercent !== undefined) c.tolerancePercent = String(tolerancePercent);
  return c;
}

export function ruleFor(key, ...conditions) {
  const rule = newRule();
  rule.key = key;
  rule.conditions = conditions;
  return rule;
}

export function basicExampleColumns() {
  const columns = [];

  const sampleId = newColumn();
  sampleId.name = "sample_id";
  columns.push(sampleId);

  const readCount = newColumn();
  readCount.name = "read_count";
  readCount.rename = "total_reads";
  readCount.qc = { kind: "list", conditions: [condition(">=", 1000), condition("<=", 1000000)] };
  columns.push(readCount);

  const qualityScore = newColumn();
  qualityScore.name = "quality_score";
  qualityScore.rename = "qc_score";
  qualityScore.qc = { kind: "list", conditions: [condition(">", 30)] };
  columns.push(qualityScore);

  const errorRate = newColumn();
  errorRate.name = "error_rate";
  errorRate.qc = { kind: "list", conditions: [condition("<", 0.05)] };
  columns.push(errorRate);

  const status = newColumn();
  status.name = "status";
  status.qc = { kind: "list", conditions: [condition("=", "PASS")] };
  columns.push(status);

  const length = newColumn();
  length.name = "length";
  length.qc = { kind: "list", conditions: [condition("~=", 1000000, 5)] };
  columns.push(length);

  const lotNumber = newColumn();
  lotNumber.name = "lot_number";
  lotNumber.rename = "lot";
  columns.push(lotNumber);

  return columns;
}

export function fileParsingExampleColumns() {
  const columns = [];

  const sampleId = newColumn();
  sampleId.name = "sample_id";
  columns.push(sampleId);

  const metadataJson = newColumn();
  metadataJson.name = "metadata_json";
  metadataJson.isFileParsing = true;
  const meanDepth = newFileParsingOutput();
  meanDepth.name = "mean_depth";
  meanDepth.command = `python3 -c "import json; print(json.load(open('$LIMSPORT_FILE'))['run']['metrics']['mean_depth'])"`;
  meanDepth.qc = { kind: "list", conditions: [condition(">=", 30)] };
  metadataJson.fileParsing = [meanDepth];
  columns.push(metadataJson);

  const coverageTsv = newColumn();
  coverageTsv.name = "coverage_tsv";
  coverageTsv.isFileParsing = true;
  const chr1MeanDepth = newFileParsingOutput();
  chr1MeanDepth.name = "chr1_meandepth";
  chr1MeanDepth.command = `awk -F'\\t' '$1 == "chr1" {print $7}' "$LIMSPORT_FILE"`;
  chr1MeanDepth.qc = { kind: "list", conditions: [condition(">=", 30)] };
  const chr1CoveragePct = newFileParsingOutput();
  chr1CoveragePct.name = "chr1_coverage_pct";
  chr1CoveragePct.command = `awk -F'\\t' '$1 == "chr1" {print $6}' "$LIMSPORT_FILE"`;
  chr1CoveragePct.qc = { kind: "list", conditions: [condition(">=", 95)] };
  coverageTsv.fileParsing = [chr1MeanDepth, chr1CoveragePct];
  columns.push(coverageTsv);

  const qcReport = newColumn();
  qcReport.name = "qc_report";
  qcReport.isFileParsing = true;
  const errorRateOut = newFileParsingOutput();
  errorRateOut.name = "error_rate";
  errorRateOut.command = `grep '^error_rate ::' "$LIMSPORT_FILE" | cut -d: -f3 | tr -d ' '`;
  errorRateOut.qc = { kind: "list", conditions: [condition("<", 0.01)] };
  qcReport.fileParsing = [errorRateOut];
  columns.push(qcReport);

  return columns;
}

export function theiaprokExampleColumns() {
  const columns = [];

  const id = newColumn();
  id.name = "entity:theiaprok_illumina_pe_v4-2-0_id";
  id.rename = "sample_id";
  columns.push(id);

  const assembler = newColumn();
  assembler.name = "assembler";
  columns.push(assembler);

  const taxon = newColumn();
  taxon.name = "gambit_predicted_taxon";
  taxon.rename = "predicted_taxon";
  columns.push(taxon);

  const assemblyLength = newColumn();
  assemblyLength.name = "assembly_length";
  assemblyLength.qc = {
    kind: "conditional",
    match: "gambit_predicted_taxon",
    useDefault: false,
    default: [],
    rules: [
      ruleFor("Escherichia coli", condition(">=", 4600000), condition("<=", 5900000)),
      ruleFor("Klebsiella pneumoniae", condition(">=", 5200000), condition("<=", 5900000)),
      ruleFor("Pseudomonas aeruginosa", condition(">=", 5500000), condition("<=", 7100000)),
    ],
  };
  columns.push(assemblyLength);

  const n50 = newColumn();
  n50.name = "n50_value";
  n50.rename = "n50";
  n50.qc = { kind: "list", conditions: [condition(">", 15000)] };
  columns.push(n50);

  const contigCount = newColumn();
  contigCount.name = "number_contigs";
  contigCount.rename = "contig_count";
  contigCount.qc = { kind: "list", conditions: [condition("<", 300)] };
  columns.push(contigCount);

  const meanQuality = newColumn();
  meanQuality.name = "combined_mean_q_clean";
  meanQuality.rename = "mean_quality";
  meanQuality.qc = { kind: "list", conditions: [condition("~=", 36, 5)] };
  columns.push(meanQuality);

  const coverage = newColumn();
  coverage.name = "est_coverage_clean";
  coverage.rename = "coverage";
  coverage.qc = { kind: "list", conditions: [condition(">=", 30)] };
  columns.push(coverage);

  const readPairs = newColumn();
  readPairs.name = "fastq_scan_num_reads_clean_pairs";
  readPairs.rename = "read_pairs";
  readPairs.qc = { kind: "list", conditions: [condition(">=", 250000)] };
  columns.push(readPairs);

  const quastReport = newColumn();
  quastReport.name = "quast_report";
  quastReport.isFileParsing = true;
  const quastN50 = newFileParsingOutput();
  quastN50.name = "quast_n50";
  quastN50.command = `awk -F'\\t' '$1 == "N50" {print $2}' "$LIMSPORT_FILE"`;
  quastN50.qc = {
    kind: "conditional",
    match: "gambit_predicted_taxon",
    useDefault: false,
    default: [],
    rules: [
      ruleFor("Escherichia coli", condition(">=", 50000)),
      ruleFor("Klebsiella pneumoniae", condition(">=", 80000)),
      ruleFor("Pseudomonas aeruginosa", condition(">=", 100000)),
    ],
  };
  const quastGcPct = newFileParsingOutput();
  quastGcPct.name = "quast_gc_pct";
  quastGcPct.command = `awk -F'\\t' '$1 == "GC (%)" {print $2}' "$LIMSPORT_FILE"`;
  const quastTotalLength = newFileParsingOutput();
  quastTotalLength.name = "quast_total_length";
  quastTotalLength.command = `awk -F'\\t' '$1 == "Total length" {print $2}' "$LIMSPORT_FILE"`;
  quastReport.fileParsing = [quastN50, quastGcPct, quastTotalLength];
  columns.push(quastReport);

  return columns;
}
