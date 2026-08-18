/**
 * Wizard-state builders for the two scenarios in ../examples/configs/
 * (config.yaml and config_multi_format.yaml). Shared by app.js (the "load
 * example" buttons) and tests/schema.test.mjs (which proves each one, once
 * run through buildConfig()+serializeYAML(), is semantically identical to
 * the real examples/configs/*.yaml fixture).
 */

import {
  newColumn,
  newCondition,
  newRule,
  newFileParsingOutput,
  newSetQCRule,
  newSetQCCheck,
} from "./schema.js";

export function condition(operator, value, tolerancePercent) {
  const c = newCondition();
  c.operator = operator;
  c.value = String(value);
  if (tolerancePercent !== undefined) c.tolerancePercent = String(tolerancePercent);
  return c;
}

export function caseInsensitiveCondition(operator, value) {
  const c = condition(operator, value);
  c.caseInsensitive = true;
  return c;
}

export function noValueCondition(operator) {
  const c = newCondition();
  c.operator = operator;
  return c;
}

export function ruleFor(key, ...conditions) {
  const rule = newRule();
  rule.key = key;
  rule.conditions = conditions;
  return rule;
}

export function setQCCheckFor(column, ...conditions) {
  const check = newSetQCCheck();
  check.column = column;
  check.conditions = conditions;
  return check;
}

/** columns: for examples/configs/config.yaml -- the main consolidated scenario. */
export function fullExampleColumns() {
  const columns = [];

  const id = newColumn();
  id.name = "entity:theiaprok_illumina_pe_v4-2-0_id";
  id.rename = "sample_id";
  columns.push(id);

  const assembler = newColumn();
  assembler.name = "assembler";
  columns.push(assembler);

  const platform = newColumn();
  platform.name = "sequencing_platform";
  platform.rename = "platform";
  columns.push(platform);

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
  contigCount.qc = { kind: "list", conditions: [condition(">=", 10), condition("<=", 300)] };
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

  const qcStatus = newColumn();
  qcStatus.name = "qc_status";
  qcStatus.qc = { kind: "list", conditions: [caseInsensitiveCondition("=", "PASS")] };
  columns.push(qcStatus);

  const screeningNotes = newColumn();
  screeningNotes.name = "screening_notes";
  columns.push(screeningNotes);

  const notes = newColumn();
  notes.name = "notes";
  columns.push(notes);

  const rawReadCount = newColumn();
  rawReadCount.name = "raw_read_count";
  columns.push(rawReadCount);

  const quastReport = newColumn();
  quastReport.name = "quast_report";
  quastReport.isFileParsing = true;
  const quastN50 = newFileParsingOutput();
  quastN50.name = "quast_n50";
  quastN50.command = `awk -F'\\t' '$1 == "N50" {print $2}' "$FILE"`;
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
  quastGcPct.command = `awk -F'\\t' '$1 == "GC (%)" {print $2}' "$FILE"`;
  const quastTotalLength = newFileParsingOutput();
  quastTotalLength.name = "quast_total_length";
  quastTotalLength.command = `awk -F'\\t' '$1 == "Total length" {print $2}' "$FILE"`;
  quastReport.fileParsing = [quastN50, quastGcPct, quastTotalLength];
  columns.push(quastReport);

  return columns;
}

/** set_qc: for examples/configs/config.yaml -- pairs with fullExampleColumns(). */
export function fullExampleSetQCRules() {
  const ntcRule = newSetQCRule();
  ntcRule.name = "NTC has no organism flagged and low raw read count";
  ntcRule.match.kind = "pattern";
  ntcRule.match.samplePattern = "NTC";
  ntcRule.columns = [
    setQCCheckFor("screening_notes", noValueCondition("is_empty")),
    setQCCheckFor("raw_read_count", condition("<=", 1000)),
  ];

  const pcRule = newSetQCRule();
  pcRule.name = "Positive control organism identity confirmed";
  pcRule.match.kind = "regex";
  pcRule.match.sampleRegex = "^PC-?\\d*$";
  pcRule.columns = [
    setQCCheckFor("screening_notes", caseInsensitiveCondition("contains", "Escherichia coli")),
    setQCCheckFor("notes", noValueCondition("is_not_empty")),
  ];

  const contaminationRule = newSetQCRule();
  contaminationRule.name = "No cross-contamination flagged in real samples";
  contaminationRule.match.kind = "samples";
  contaminationRule.match.samples = "19050801924, 461023, CL2021-00283104";
  contaminationRule.columns = [
    setQCCheckFor("screening_notes", caseInsensitiveCondition("does_not_contain", "contaminant")),
  ];

  return [ntcRule, pcRule, contaminationRule];
}

/** columns: for examples/configs/config_multi_format.yaml -- the file_parsing-format adjunct scenario. */
export function multiFormatExampleColumns() {
  const columns = [];

  const sampleId = newColumn();
  sampleId.name = "sample_id";
  columns.push(sampleId);

  const metadataJson = newColumn();
  metadataJson.name = "metadata_json";
  metadataJson.isFileParsing = true;
  const meanDepth = newFileParsingOutput();
  meanDepth.name = "mean_depth";
  meanDepth.command = `python3 -c "import json; print(json.load(open('$FILE'))['run']['metrics']['mean_depth'])"`;
  meanDepth.qc = { kind: "list", conditions: [condition(">=", 30)] };
  metadataJson.fileParsing = [meanDepth];
  columns.push(metadataJson);

  const coverageTsv = newColumn();
  coverageTsv.name = "coverage_tsv";
  coverageTsv.isFileParsing = true;
  const chr1MeanDepth = newFileParsingOutput();
  chr1MeanDepth.name = "chr1_meandepth";
  chr1MeanDepth.command = `awk -F'\\t' '$1 == "chr1" {print $7}' "$FILE"`;
  chr1MeanDepth.qc = { kind: "list", conditions: [condition(">=", 30)] };
  const chr1CoveragePct = newFileParsingOutput();
  chr1CoveragePct.name = "chr1_coverage_pct";
  chr1CoveragePct.command = `awk -F'\\t' '$1 == "chr1" {print $6}' "$FILE"`;
  chr1CoveragePct.qc = { kind: "list", conditions: [condition(">=", 95)] };
  coverageTsv.fileParsing = [chr1MeanDepth, chr1CoveragePct];
  columns.push(coverageTsv);

  const qcReport = newColumn();
  qcReport.name = "qc_report";
  qcReport.isFileParsing = true;
  const errorRateOut = newFileParsingOutput();
  errorRateOut.name = "error_rate";
  errorRateOut.command = `grep '^error_rate ::' "$FILE" | cut -d: -f3 | tr -d ' '`;
  errorRateOut.qc = { kind: "list", conditions: [condition("<", 0.01)] };
  qcReport.fileParsing = [errorRateOut];
  columns.push(qcReport);

  return columns;
}
