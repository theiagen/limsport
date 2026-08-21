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
  newCase,
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

export function caseFor(key, ...conditions) {
  const qcCase = newCase();
  qcCase.key = key;
  qcCase.conditions = conditions;
  return qcCase;
}

export function setQCCheckFor(inputColumn, ...conditions) {
  const check = newSetQCCheck();
  check.inputColumn = inputColumn;
  check.conditions = conditions;
  return check;
}

/** columns: for examples/configs/config.yaml -- the main consolidated scenario. */
export function fullExampleColumns() {
  const columns = [];

  const id = newColumn();
  id.inputColumn = "entity:theiaprok_illumina_pe_v4-2-0_id";
  id.outputColumn = "sample_id";
  columns.push(id);

  const assembler = newColumn();
  assembler.inputColumn = "assembler";
  columns.push(assembler);

  const platform = newColumn();
  platform.inputColumn = "sequencing_platform";
  platform.outputColumn = "platform";
  columns.push(platform);

  const taxon = newColumn();
  taxon.inputColumn = "gambit_predicted_taxon";
  taxon.outputColumn = "predicted_taxon";
  columns.push(taxon);

  const assemblyLength = newColumn();
  assemblyLength.inputColumn = "assembly_length";
  assemblyLength.qc = {
    kind: "conditional",
    matchColumn: "gambit_predicted_taxon",
    useDefault: false,
    default: [],
    cases: [
      caseFor("Escherichia coli", condition(">=", 4600000), condition("<=", 5900000)),
      caseFor("Klebsiella pneumoniae", condition(">=", 5200000), condition("<=", 5900000)),
      caseFor("Pseudomonas aeruginosa", condition(">=", 5500000), condition("<=", 7100000)),
    ],
  };
  columns.push(assemblyLength);

  const n50 = newColumn();
  n50.inputColumn = "n50_value";
  n50.outputColumn = "n50";
  n50.qc = { kind: "list", conditions: [condition(">", 15000)] };
  columns.push(n50);

  const contigCount = newColumn();
  contigCount.inputColumn = "number_contigs";
  contigCount.outputColumn = "contig_count";
  contigCount.qc = { kind: "list", conditions: [condition(">=", 10), condition("<=", 300)] };
  columns.push(contigCount);

  const meanQuality = newColumn();
  meanQuality.inputColumn = "combined_mean_q_clean";
  meanQuality.outputColumn = "mean_quality";
  meanQuality.qc = { kind: "list", conditions: [condition("~=", 36, 5)] };
  columns.push(meanQuality);

  const coverage = newColumn();
  coverage.inputColumn = "est_coverage_clean";
  coverage.outputColumn = "coverage";
  coverage.qc = { kind: "list", conditions: [condition(">=", 30)] };
  columns.push(coverage);

  const readPairs = newColumn();
  readPairs.inputColumn = "fastq_scan_num_reads_clean_pairs";
  readPairs.outputColumn = "read_pairs";
  readPairs.qc = { kind: "list", conditions: [condition(">=", 250000)] };
  columns.push(readPairs);

  const qcStatus = newColumn();
  qcStatus.inputColumn = "qc_status";
  qcStatus.qc = { kind: "list", conditions: [caseInsensitiveCondition("=", "PASS")] };
  columns.push(qcStatus);

  const screeningNotes = newColumn();
  screeningNotes.inputColumn = "screening_notes";
  columns.push(screeningNotes);

  const notes = newColumn();
  notes.inputColumn = "notes";
  columns.push(notes);

  const rawReadCount = newColumn();
  rawReadCount.inputColumn = "raw_read_count";
  columns.push(rawReadCount);

  const quastReport = newColumn();
  quastReport.inputColumn = "quast_report";
  quastReport.isFileParsing = true;
  const quastN50 = newFileParsingOutput();
  quastN50.outputColumn = "quast_n50";
  quastN50.command = `awk -F'\\t' '$1 == "N50" {print $2}' "$FILE"`;
  quastN50.qc = {
    kind: "conditional",
    matchColumn: "gambit_predicted_taxon",
    useDefault: false,
    default: [],
    cases: [
      caseFor("Escherichia coli", condition(">=", 50000)),
      caseFor("Klebsiella pneumoniae", condition(">=", 80000)),
      caseFor("Pseudomonas aeruginosa", condition(">=", 100000)),
    ],
  };
  const quastGcPct = newFileParsingOutput();
  quastGcPct.outputColumn = "quast_gc_pct";
  quastGcPct.command = `awk -F'\\t' '$1 == "GC (%)" {print $2}' "$FILE"`;
  const quastTotalLength = newFileParsingOutput();
  quastTotalLength.outputColumn = "quast_total_length";
  quastTotalLength.command = `awk -F'\\t' '$1 == "Total length" {print $2}' "$FILE"`;
  quastReport.fileParsing = [quastN50, quastGcPct, quastTotalLength];
  columns.push(quastReport);

  return columns;
}

/** set_qc: for examples/configs/config.yaml -- pairs with fullExampleColumns(). */
export function fullExampleSetQCRules() {
  const ntcRule = newSetQCRule();
  ntcRule.ruleName = "NTC has no organism flagged and low raw read count";
  ntcRule.matchSamples.kind = "pattern";
  ntcRule.matchSamples.samplePattern = "NTC";
  ntcRule.checks = [
    setQCCheckFor("screening_notes", noValueCondition("is_empty")),
    setQCCheckFor("raw_read_count", condition("<=", 1000)),
  ];

  const pcRule = newSetQCRule();
  pcRule.ruleName = "Positive control organism identity confirmed";
  pcRule.matchSamples.kind = "regex";
  pcRule.matchSamples.sampleRegex = "^PC-?\\d*$";
  pcRule.checks = [
    setQCCheckFor("screening_notes", caseInsensitiveCondition("contains", "Escherichia coli")),
    setQCCheckFor("notes", noValueCondition("is_not_empty")),
  ];

  const contaminationRule = newSetQCRule();
  contaminationRule.ruleName = "No cross-contamination flagged in real samples";
  contaminationRule.matchSamples.kind = "samples";
  contaminationRule.matchSamples.samples = "19050801924, 461023, CL2021-00283104";
  contaminationRule.checks = [
    setQCCheckFor("screening_notes", caseInsensitiveCondition("does_not_contain", "contaminant")),
  ];

  return [ntcRule, pcRule, contaminationRule];
}

/** columns: for examples/configs/config_multi_format.yaml -- the file_parsing-format adjunct scenario. */
export function multiFormatExampleColumns() {
  const columns = [];

  const sampleId = newColumn();
  sampleId.inputColumn = "sample_id";
  columns.push(sampleId);

  const metadataJson = newColumn();
  metadataJson.inputColumn = "metadata_json";
  metadataJson.isFileParsing = true;
  const meanDepth = newFileParsingOutput();
  meanDepth.outputColumn = "mean_depth";
  meanDepth.command = `python3 -c "import json; print(json.load(open('$FILE'))['run']['metrics']['mean_depth'])"`;
  meanDepth.qc = { kind: "list", conditions: [condition(">=", 30)] };
  metadataJson.fileParsing = [meanDepth];
  columns.push(metadataJson);

  const coverageTsv = newColumn();
  coverageTsv.inputColumn = "coverage_tsv";
  coverageTsv.isFileParsing = true;
  const chr1MeanDepth = newFileParsingOutput();
  chr1MeanDepth.outputColumn = "chr1_meandepth";
  chr1MeanDepth.command = `awk -F'\\t' '$1 == "chr1" {print $7}' "$FILE"`;
  chr1MeanDepth.qc = { kind: "list", conditions: [condition(">=", 30)] };
  const chr1CoveragePct = newFileParsingOutput();
  chr1CoveragePct.outputColumn = "chr1_coverage_pct";
  chr1CoveragePct.command = `awk -F'\\t' '$1 == "chr1" {print $6}' "$FILE"`;
  chr1CoveragePct.qc = { kind: "list", conditions: [condition(">=", 95)] };
  coverageTsv.fileParsing = [chr1MeanDepth, chr1CoveragePct];
  columns.push(coverageTsv);

  const qcReport = newColumn();
  qcReport.inputColumn = "qc_report";
  qcReport.isFileParsing = true;
  const errorRateOut = newFileParsingOutput();
  errorRateOut.outputColumn = "error_rate";
  errorRateOut.command = `grep '^error_rate ::' "$FILE" | cut -d: -f3 | tr -d ' '`;
  errorRateOut.qc = { kind: "list", conditions: [condition("<", 0.01)] };
  qcReport.fileParsing = [errorRateOut];
  columns.push(qcReport);

  return columns;
}
