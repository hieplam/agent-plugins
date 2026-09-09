// Review-log check for the `explaining` skill's Rule 5 (blind-reader review).
//
// Pure core (parseReviewLog, normalizeWords, ngrams, findLeakedNgram, templateInvariants,
// missingInvariants, evaluateLog, formatSummary, EXIT_CODE) is exported for direct unit
// testing and performs no I/O. The impure edge (argument parsing against the real argv,
// reading the template and the logs, the exit code) lives in main().

export const EXIT_CODE = { PASS: 0, FAIL: 1, CANNOT_RUN: 2 } as const;

/** Shared run of this many normalized words between a brief and the prompt counts as
 * leakage (spec 2.3). Short overlaps are legitimate: the audience phrase is derived
 * from the prompt on purpose. */
export const LEAK_NGRAM = 12;

/** The hard cap from D106-1. A fourth round is never legal. */
export const MAX_ROUNDS = 3;

export type Severity = 'BLOCK' | 'NIT';

export interface Finding {
  severity: Severity;
  location: string;
  issue: string;
}

export interface RoundRecord {
  round: number;
  reader_model?: string;
  brief: string;
  findings: Finding[];
  block_count: number;
  verdict: 'PASS' | 'FAIL';
  author_action?: string;
}

/** Pure: parse a review log (one JSON object per line) into records plus shape errors.
 * A record that fails validation is reported and dropped, never half-accepted. */
export function parseReviewLog(text: string): { rounds: RoundRecord[]; errors: string[] } {
  const rounds: RoundRecord[] = [];
  const errors: string[] = [];
  const lines = text.split('\n').map((line) => line.trim()).filter((line) => line.length > 0);
  if (lines.length === 0) {
    errors.push('log is empty');
    return { rounds, errors };
  }
  lines.forEach((line, index) => {
    const where = `line ${index + 1}`;
    let value: unknown;
    try {
      value = JSON.parse(line);
    } catch {
      errors.push(`${where}: not valid JSON`);
      return;
    }
    if (typeof value !== 'object' || value === null || Array.isArray(value)) {
      errors.push(`${where}: not a JSON object`);
      return;
    }
    const record = value as Record<string, unknown>;
    if (record.round === 0) {
      errors.push(`${where}: round 0 is the degrade record (the reader dispatch failed) — a review that never dispatched a reader is not a completed review`);
      return;
    }
    if (!Number.isInteger(record.round) || (record.round as number) < 1) {
      errors.push(`${where}: round must be an integer of at least 1`);
      return;
    }
    if (typeof record.brief !== 'string' || record.brief.trim().length === 0) {
      errors.push(`${where}: brief must be a non-empty string`);
      return;
    }
    if (!Array.isArray(record.findings)) {
      errors.push(`${where}: findings must be an array`);
      return;
    }
    const findings: Finding[] = [];
    for (const raw of record.findings) {
      const finding = raw as Record<string, unknown> | null;
      if (
        finding === null || typeof finding !== 'object'
        || (finding.severity !== 'BLOCK' && finding.severity !== 'NIT')
        || typeof finding.location !== 'string' || typeof finding.issue !== 'string'
      ) {
        errors.push(`${where}: every finding needs severity BLOCK or NIT, a location and an issue`);
        return;
      }
      findings.push({
        severity: finding.severity as Severity,
        location: finding.location,
        issue: finding.issue,
      });
    }
    const blocks = findings.filter((finding) => finding.severity === 'BLOCK').length;
    if (record.block_count !== blocks) {
      errors.push(`${where}: block_count ${String(record.block_count)} does not match ${blocks} BLOCK finding(s)`);
      return;
    }
    if (record.verdict !== 'PASS' && record.verdict !== 'FAIL') {
      errors.push(`${where}: verdict must be PASS or FAIL`);
      return;
    }
    if ((record.verdict === 'PASS') !== (blocks === 0)) {
      errors.push(`${where}: verdict ${record.verdict} contradicts ${blocks} BLOCK finding(s)`);
      return;
    }
    rounds.push({
      round: record.round as number,
      reader_model: typeof record.reader_model === 'string' ? record.reader_model : undefined,
      brief: record.brief,
      findings,
      block_count: blocks,
      verdict: record.verdict,
      author_action: typeof record.author_action === 'string' ? record.author_action : undefined,
    });
  });
  return { rounds, errors };
}

/** Pure: lowercase words with punctuation dropped — the unit both leak sides compare in,
 * so re-wrapping or re-punctuating a leaked sentence does not hide it. Splits on Unicode
 * letters and numbers rather than ASCII a-z0-9 only, so a prompt written in a non-Latin
 * script (Cyrillic, Arabic, Hebrew, accented Latin, etc.) still tokenizes instead of
 * collapsing to nothing — {{language}} is one of the three slots this whole feature
 * exists to gate. For pure-ASCII input this is byte-identical to the old a-z0-9 rule. */
export function normalizeWords(text: string): string[] {
  return text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim().split(/\s+/)
    .filter((word) => word.length > 0);
}

/** Pure: true when the prompt normalizes to fewer word tokens than the leak window, so
 * n-gram leak detection can never fire (ruling R2). A script with no whitespace word
 * boundaries — Japanese, Chinese — normalizes a whole prompt to about one token; this is
 * out of the word-based detector's contract, and callers must announce that rather than
 * pass silently. Deliberately does not attempt character-n-gram detection for those
 * scripts — that is a separate follow-up. */
export function isLeakDetectionInapplicable(prompt: string, leakNgram: number = LEAK_NGRAM): boolean {
  return normalizeWords(prompt).length < leakNgram;
}

/** Pure: every sliding window of n words. */
export function ngrams(words: string[], n: number): string[] {
  const out: string[] = [];
  for (let i = 0; i + n <= words.length; i++) out.push(words.slice(i, i + n).join(' '));
  return out;
}

/** Pure: the first n-word run the brief shares with the prompt, or null when clean. */
export function findLeakedNgram(brief: string, prompt: string, n: number): string | null {
  const inBrief = new Set(ngrams(normalizeWords(brief), n));
  for (const gram of ngrams(normalizeWords(prompt), n)) {
    if (inBrief.has(gram)) return gram;
  }
  return null;
}

/** A fixed-text segment left over after splitting a slot-bearing line on its {{slot}}
 * placeholders must carry at least this many words to count as an invariant — this
 * filters out trivial connective fragments (a bare "in" or ".") while keeping the
 * meaningful fixed wording around a slot ("Read the file at", "It was written for"). */
const MIN_INVARIANT_SEGMENT_WORDS = 3;

/** Pure: the template lines a rendered brief must reproduce — every non-empty line
 * between the BRIEF-START and BRIEF-END markers. A line with no slot survives whole, as
 * before. A line WITH a slot is split on its {{slot}} placeholders, and each fixed-text
 * segment substantial enough to be meaningful (see MIN_INVARIANT_SEGMENT_WORDS) is kept
 * as its own invariant — so the fixed wording around a slot is still checked even though
 * the slot's rendered value is not. Everything outside the markers is rendering guidance
 * for the author and must NOT appear in the brief. */
export function templateInvariants(template: string): string[] {
  const lines = template.split('\n');
  const start = lines.findIndex((line) => line.includes('BRIEF-START'));
  const end = lines.findIndex((line) => line.includes('BRIEF-END'));
  if (start < 0 || end < 0 || end <= start) return [];
  const invariants: string[] = [];
  for (const raw of lines.slice(start + 1, end)) {
    const line = raw.trim();
    if (line.length === 0) continue;
    if (!line.includes('{{')) {
      invariants.push(line);
      continue;
    }
    for (const segment of line.split(/\{\{[^}]*\}\}/g)) {
      const fixed = segment.trim();
      if (fixed.length === 0) continue;
      const wordCount = fixed.split(/\s+/).filter((word) => word.length > 0).length;
      if (wordCount >= MIN_INVARIANT_SEGMENT_WORDS) invariants.push(fixed);
    }
  }
  return invariants;
}

/** Pure: which invariant lines the rendered brief dropped. Both sides are compared with
 * whitespace flattened, so a differently wrapped but word-identical brief still matches. */
export function missingInvariants(brief: string, invariants: string[]): string[] {
  const flat = brief.replace(/\s+/g, ' ').trim();
  return invariants.filter((line) => !flat.includes(line.replace(/\s+/g, ' ').trim()));
}

export interface EvaluateOptions {
  invariants: string[];
  prompt: string;
  requireCatch?: boolean;
  leakNgram?: number;
  maxRounds?: number;
}

export interface Evaluation {
  ok: boolean;
  reasons: string[];
}

/** Pure: the whole pass/fail decision for one review log. */
export function evaluateLog(rounds: RoundRecord[], options: EvaluateOptions): Evaluation {
  const reasons: string[] = [];
  const maxRounds = options.maxRounds ?? MAX_ROUNDS;
  const window = options.leakNgram ?? LEAK_NGRAM;

  if (rounds.length === 0) reasons.push('no review round recorded');
  if (rounds.length > maxRounds) {
    reasons.push(`${rounds.length} rounds recorded, cap is ${maxRounds} — Round 3 is the last round. After its verdict, stop — even on FAIL.`);
  }

  rounds.forEach((record, index) => {
    if (record.round !== index + 1) {
      reasons.push(`round numbers are not consecutive from 1 (saw ${record.round} at position ${index + 1})`);
    }
    const missing = missingInvariants(record.brief, options.invariants);
    if (missing.length > 0) {
      reasons.push(`round ${record.round}: brief does not match the template, missing: ${missing[0]}`);
    }
    const leak = findLeakedNgram(record.brief, options.prompt, window);
    if (leak !== null) {
      reasons.push(`round ${record.round}: brief leaks ${window} words of the prompt: ${leak}`);
    }
    if (index < rounds.length - 1 && record.verdict === 'PASS') {
      reasons.push(`round ${record.round}: verdict PASS but the loop continued`);
    }
  });

  const last = rounds[rounds.length - 1];
  if (last !== undefined && last.verdict !== 'PASS' && rounds.length < maxRounds) {
    reasons.push(`review stopped at round ${rounds.length} still failing, before the cap of ${maxRounds}`);
  }

  if (options.requireCatch === true && rounds.length > 0) {
    if (rounds[0].block_count < 1) reasons.push('round 1 found no BLOCK finding');
    if (rounds.length > 1 && rounds[1].block_count >= rounds[0].block_count) {
      reasons.push(`round 2 block count ${rounds[1].block_count} did not fall below round 1 (${rounds[0].block_count})`);
    }
  }

  return { ok: reasons.length === 0, reasons };
}

/** Pure: one greppable line per log, so a preserved artifact can be tallied afterwards. */
export function formatSummary(file: string, rounds: RoundRecord[]): string {
  const blocks = rounds.map((record) => record.block_count).join(',');
  const verdict = rounds.length > 0 ? rounds[rounds.length - 1].verdict : 'NONE';
  return `REVIEW-LOG: file=${file} rounds=${rounds.length} blocks=${blocks === '' ? 'none' : blocks} verdict=${verdict}`;
}

import { Glob } from 'bun';
import { readFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

export interface CliArgs {
  dir: string;
  logGlob: string;
  prompt: string | null;
  template: string | null;
  requireCatch: boolean;
  error: string | null;
}

/** Pure: argv to options. A malformed invocation is a SETUP error (exit 2), never a
 * verdict on the artifact — the harness routes exit 2 to ungraded for exactly this. */
export function parseArgs(argv: string[]): CliArgs {
  const args: CliArgs = {
    dir: '.', logGlob: '*.review.jsonl', prompt: null, template: null,
    requireCatch: false, error: null,
  };
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === '--require-catch') {
      args.requireCatch = true;
      continue;
    }
    const value = argv[i + 1];
    if (flag === '--log-glob' || flag === '--prompt' || flag === '--template' || flag === '--dir') {
      if (value === undefined) {
        args.error = `${flag} needs a value`;
        return args;
      }
      i++;
      if (flag === '--log-glob') args.logGlob = value;
      else if (flag === '--prompt') args.prompt = value;
      else if (flag === '--template') args.template = value;
      else args.dir = value;
      continue;
    }
    args.error = `unknown argument: ${flag}`;
    return args;
  }
  if (args.prompt === null) args.error = 'missing required --prompt';
  return args;
}

/** Impure edge: read the template and every matching log, run the pure decision, print,
 * and return the exit code the harness reads. */
export async function main(argv: string[]): Promise<number> {
  const args = parseArgs(argv);
  if (args.error !== null) {
    console.error(`CANNOT-RUN: ${args.error}`);
    return EXIT_CODE.CANNOT_RUN;
  }
  if (isLeakDetectionInapplicable(args.prompt as string)) {
    console.log(`WARN: prompt-leak detection not applicable (prompt has <${LEAK_NGRAM} word tokens)`);
  }
  const templatePath = args.template
    ?? resolve(SCRIPT_DIR, '..', 'references', 'blind-reader-brief.md');
  let invariants: string[];
  try {
    invariants = templateInvariants(await readFile(templatePath, 'utf8'));
  } catch (error) {
    console.error(`CANNOT-RUN: cannot read the brief template at ${templatePath}: ${String(error)}`);
    return EXIT_CODE.CANNOT_RUN;
  }
  if (invariants.length === 0) {
    console.error(`CANNOT-RUN: the brief template at ${templatePath} carries no invariant lines between its markers`);
    return EXIT_CODE.CANNOT_RUN;
  }

  const files: string[] = [];
  try {
    for await (const file of new Glob(args.logGlob).scan({ cwd: args.dir, onlyFiles: true })) {
      files.push(file);
    }
  } catch (error) {
    console.error(`CANNOT-RUN: cannot scan ${args.dir} for ${args.logGlob}: ${String(error)}`);
    return EXIT_CODE.CANNOT_RUN;
  }
  files.sort();
  if (files.length === 0) {
    console.log(`INVALID: no review log matched ${args.logGlob} in ${args.dir} — the blind-reader review left no record`);
    return EXIT_CODE.FAIL;
  }

  let failed = false;
  for (const file of files) {
    let text: string;
    try {
      text = await readFile(resolve(args.dir, file), 'utf8');
    } catch (error) {
      console.error(`CANNOT-RUN: cannot read ${file}: ${String(error)}`);
      return EXIT_CODE.CANNOT_RUN;
    }
    const { rounds, errors } = parseReviewLog(text);
    const evaluation = evaluateLog(rounds, {
      invariants,
      prompt: args.prompt as string,
      requireCatch: args.requireCatch,
    });
    console.log(formatSummary(file, rounds));
    for (const problem of [...errors, ...evaluation.reasons]) {
      failed = true;
      console.log(`INVALID: ${file}: ${problem}`);
    }
  }
  if (failed) return EXIT_CODE.FAIL;
  console.log(`VALID: ${files.length} review log(s) checked, 0 error(s)`);
  return EXIT_CODE.PASS;
}

if (import.meta.main) {
  process.exit(await main(Bun.argv.slice(2)));
}
