// Term-discipline check for the `explaining` skill's Rule 1 / the Todd way style's B2
// ("define before use").
//
// Oracle — the spec in this comment is the contract; no external standard is:
//
//   For every listed term that the reply USES, its FIRST use in prose must be
//   INTRODUCED. A first use is introduced when the sentence it sits in carries a
//   definitional cue attached to the term:
//     - directly after the term: a colon ("WAL: the append-only file...");
//     - within one word after: a dash, an opening parenthesis, "=", or an appositive comma
//       (", which", ", the", ", a", ", i.e.", ", meaning", ", where", ", also known
//       as", ", short for" ...);
//     - within AFTER_WINDOW words after: a copula ("is", "are", "means", "refers to",
//       "stands for", "denotes", "describes");
//     - directly after: a knob verb that says what a setting does ("caps",
//       "controls", "limits", "bounds", "governs", "sets", "specifies", "determines",
//       "tells") — "MaxDeliver caps redelivery" introduces MaxDeliver;
//     - within BEFORE_WINDOW words before: "called", "known as", "termed", "dubbed",
//       "so-called", "named";
//     - the term sits alone inside parentheses right after at least one word — the
//       acronym-expansion shape, "write-ahead log (WAL)";
//     - any extra cue the caller passes with --cues (another language: "là",
//       "tức là", "gọi là" ...), on either side, with the copula window.
//   A term the reply never uses is UNUSED and needs no introduction: avoiding jargon
//   is term discipline too. Fenced code blocks are not prose and are not scanned. A
//   markdown heading or a bold lead-in line is read together with the sentence after
//   it, because a heading names a section and the definition arrives in the section's
//   first sentence.
//
//   Direction of error: UNDER-flagging (a cue that is not really a definition, e.g.
//   "vacuum is expensive" passing) is BY DESIGN — this check is a floor, the LLM
//   grader is the ceiling. OVER-flagging (a term the reader was given a definition
//   for, reported UNDEFINED) is a BUG in this file.
//
// Pure core (stripFences, splitSentences, termPattern, findTerm, hasAfterCue,
// hasBeforeCue, isParentheticalExpansion, classifyTerm, evaluateReply, formatReport,
// parseArgs, EXIT_CODE) is exported for direct unit testing and does no I/O. The
// impure edge (reading the reply, the exit code) lives in main().

import { readFile } from 'node:fs/promises';

export const EXIT_CODE = { PASS: 0, FAIL: 1, CANNOT_RUN: 2 } as const;

/** Words allowed between the term and a copula cue ("The WAL in Postgres is ..."). */
export const AFTER_WINDOW = 3;
/** Words allowed between a "called"/"known as" cue and the term. */
export const BEFORE_WINDOW = 4;

interface AfterCue {
  cue: string;
  /** Maximum number of words allowed between the term and the cue. */
  gap: number;
}

// Word cues carry a leading space so "this" never matches " is ".
const AFTER_CUES: AfterCue[] = [
  { cue: ':', gap: 0 },
  { cue: '—', gap: 1 }, { cue: '–', gap: 1 }, { cue: ' - ', gap: 1 }, { cue: '(', gap: 1 }, { cue: '=', gap: 1 },
  { cue: ', which', gap: 1 }, { cue: ', that is', gap: 1 }, { cue: ', i.e', gap: 1 },
  { cue: ', ie ', gap: 1 }, { cue: ', meaning', gap: 1 }, { cue: ', where', gap: 1 },
  { cue: ', a ', gap: 1 }, { cue: ', an ', gap: 1 }, { cue: ', the ', gap: 1 },
  { cue: ', also known as', gap: 1 }, { cue: ', also called', gap: 1 }, { cue: ', aka', gap: 1 },
  { cue: ', short for', gap: 1 }, { cue: ', or ', gap: 1 },
  // A knob or setting is introduced by saying what it does: "MaxDeliver caps redelivery".
  { cue: ' caps ', gap: 0 }, { cue: ' controls ', gap: 0 }, { cue: ' limits ', gap: 0 },
  { cue: ' bounds ', gap: 0 }, { cue: ' governs ', gap: 0 }, { cue: ' sets ', gap: 0 },
  { cue: ' specifies ', gap: 0 }, { cue: ' determines ', gap: 0 }, { cue: ' tells ', gap: 0 },
  { cue: ' is ', gap: AFTER_WINDOW }, { cue: ' are ', gap: AFTER_WINDOW },
  { cue: ' means ', gap: AFTER_WINDOW }, { cue: ' refers to', gap: AFTER_WINDOW },
  { cue: ' stands for', gap: AFTER_WINDOW }, { cue: ' denotes ', gap: AFTER_WINDOW },
  { cue: ' describes ', gap: AFTER_WINDOW },
];

const BEFORE_CUES = ['called', 'known as', 'termed', 'dubbed', 'so-called', 'named'];

export type Status = 'DEFINED' | 'UNDEFINED' | 'UNUSED';

export interface TermVerdict {
  term: string;
  status: Status;
  /** The sentence holding the first use, trimmed; empty when UNUSED. */
  sentence: string;
}

export interface Evaluation {
  verdicts: TermVerdict[];
  used: number;
  undefined: number;
}

/** Pure: drop fenced code blocks; code is not prose and a term there is not a use. */
export function stripFences(text: string): string {
  return text.replace(/```[\s\S]*?```/g, ' ');
}

const HEADING = /^(?:#{1,6}\s+.*|\*\*[^*\n]+\*\*:?)\s*$/;
const BULLET = /^\s*(?:[-*+]|\d+[.)])\s+/;

/** Pure: markdown → paragraphs, with a heading or bold lead-in glued onto the
 * paragraph that follows it, and each bullet its own paragraph. */
export function splitParagraphs(text: string): string[] {
  const paragraphs: string[] = [];
  let current = '';
  let carry = '';
  const flush = () => {
    if (current.trim()) paragraphs.push(current.trim());
    current = '';
  };
  for (const raw of text.split('\n')) {
    const line = raw.trim();
    if (line.length === 0) { flush(); continue; }
    if (HEADING.test(line)) {
      flush();
      const title = line.replace(/^#{1,6}\s+/, '').replace(/^\*\*([^*]+)\*\*:?$/, '$1');
      carry = `${carry} ${title}`.trim();
      continue;
    }
    if (BULLET.test(line)) {
      flush();
      current = line.replace(BULLET, '');
    } else {
      current = current ? `${current} ${line}` : line;
    }
    if (carry) { current = `${carry} ${current}`; carry = ''; }
  }
  if (carry) current = `${carry} ${current}`;
  flush();
  return paragraphs;
}

/** Pure: paragraphs → sentences. A split happens after . ! ? when the next
 * sentence opens with a capital, a quote, a bracket, or markdown emphasis. */
export function splitSentences(text: string): string[] {
  const sentences: string[] = [];
  for (const paragraph of splitParagraphs(text)) {
    for (const piece of paragraph.split(/(?<=[.!?])\s+(?=[\p{Lu}"'*`(])/u)) {
      const s = piece.trim();
      if (s.length > 0) sentences.push(s);
    }
  }
  return sentences;
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** Pure: a regex matching the term as whole words. Multi-word terms accept any run of
 * whitespace or hyphens between words; a trailing plural is accepted. An all-uppercase
 * term (an acronym) matches case-sensitively, so `HOT` never matches "hot pages". */
export function termPattern(term: string): RegExp {
  const words = term.trim().split(/[\s-]+/).map(escapeRegExp);
  const body = words.join('[\\s-]+');
  const acronym = /^[A-Z0-9_./-]+$/.test(term.trim()) && /[A-Z]/.test(term);
  return new RegExp(`(?<![\\p{L}\\p{N}_])${body}(?:s|es)?(?![\\p{L}\\p{N}_])`, acronym ? 'u' : 'iu');
}

/** Pure: the first sentence using the term, plus the match offsets in it. */
export function findTerm(sentences: string[], term: string): { index: number; start: number; end: number } | null {
  const pattern = termPattern(term);
  for (let i = 0; i < sentences.length; i++) {
    const m = pattern.exec(sentences[i]);
    if (m && m.index !== undefined) return { index: i, start: m.index, end: m.index + m[0].length };
  }
  return null;
}

function wordsOf(s: string): string[] {
  return s.split(/\s+/).filter((w) => w.length > 0);
}

/** Pure: does the text right after the term carry a definitional cue within its gap? */
export function hasAfterCue(after: string, extraCues: string[] = []): boolean {
  const tail = after.replace(/^[`*_"'’”]+/, ''); // closing decoration of the term itself
  const probe = ` ${tail.toLowerCase()} `;
  const cues: AfterCue[] = [...AFTER_CUES, ...extraCues.map((c) => ({ cue: ` ${c.toLowerCase()} `, gap: AFTER_WINDOW }))];
  for (const { cue, gap } of cues) {
    const idx = probe.indexOf(cue);
    if (idx < 0) continue;
    const between = probe.slice(1, idx);
    if (wordsOf(between).length <= gap) return true;
  }
  return false;
}

/** Pure: does a "called"/"known as"-style cue sit just before the term? Trailing
 * articles and opening decoration ("known as the \"hash ring\"") are ignored. */
export function hasBeforeCue(before: string, extraCues: string[] = []): boolean {
  let lead = before.toLowerCase().replace(/[\s`*_"'“‘(]+$/, '');
  lead = lead.replace(/\s+(?:the|a|an)$/, '');
  const window = wordsOf(lead).slice(-BEFORE_WINDOW).join(' ');
  for (const raw of [...BEFORE_CUES, ...extraCues]) {
    const cue = raw.trim().toLowerCase();
    if (window === cue || window.endsWith(` ${cue}`)) return true;
  }
  return false;
}

/** Pure: the acronym-expansion shape — `expansion (TERM)` with a word before the paren. */
export function isParentheticalExpansion(before: string, after: string): boolean {
  const opens = /\(\s*[`"*]*$/.test(before);
  const closes = /^[`"*]*\s*\)/.test(after);
  const preceded = wordsOf(before.replace(/\(\s*[`"*]*$/, '')).length >= 1;
  return opens && closes && preceded;
}

/** Pure: classify one term against the reply's sentences. */
export function classifyTerm(sentences: string[], term: string, extraCues: string[] = []): TermVerdict {
  const hit = findTerm(sentences, term);
  if (hit === null) return { term, status: 'UNUSED', sentence: '' };
  const sentence = sentences[hit.index];
  const before = sentence.slice(0, hit.start);
  const after = sentence.slice(hit.end);
  const defined = hasAfterCue(after, extraCues) || hasBeforeCue(before, extraCues)
    || isParentheticalExpansion(before, after);
  return { term, status: defined ? 'DEFINED' : 'UNDEFINED', sentence };
}

/** Pure: the whole decision for one reply. */
export function evaluateReply(reply: string, terms: string[], extraCues: string[] = []): Evaluation {
  const sentences = splitSentences(stripFences(reply));
  const verdicts = terms.map((t) => classifyTerm(sentences, t, extraCues));
  const used = verdicts.filter((v) => v.status !== 'UNUSED').length;
  const undefinedCount = verdicts.filter((v) => v.status === 'UNDEFINED').length;
  return { verdicts, used, undefined: undefinedCount };
}

function excerpt(sentence: string, limit: number): string {
  const s = sentence.replace(/\s+/g, ' ');
  return s.length <= limit ? s : `${s.slice(0, limit - 1)}…`;
}

/** Pure: one line per term, then the summary line the harness's evidence tail shows. */
export function formatReport(evaluation: Evaluation, maxUndefined: number, limit = 160): string {
  const lines = evaluation.verdicts.map((v) => v.status === 'UNUSED'
    ? `UNUSED    ${JSON.stringify(v.term)}`
    : `${v.status.padEnd(9)} ${JSON.stringify(v.term)} — ${excerpt(v.sentence, limit)}`);
  const verdict = evaluation.undefined > maxUndefined ? 'INVALID' : 'VALID';
  lines.push(`${verdict}: ${evaluation.undefined} undefined of ${evaluation.used} used `
    + `(${evaluation.verdicts.length} listed, max-undefined ${maxUndefined})`);
  return lines.join('\n');
}

export interface CliArgs {
  reply: string | null;
  terms: string[];
  cues: string[];
  maxUndefined: number;
  error: string | null;
}

function splitList(raw: string): string[] {
  return raw.split(',').map((s) => s.trim()).filter((s) => s.length > 0);
}

/** Pure: argv → args, or a named error. */
export function parseArgs(argv: string[]): CliArgs {
  const args: CliArgs = { reply: null, terms: [], cues: [], maxUndefined: 0, error: null };
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    const value = argv[i + 1];
    if (flag === '--reply' && value !== undefined) { args.reply = value; i++; continue; }
    if (flag === '--terms' && value !== undefined) { args.terms.push(...splitList(value)); i++; continue; }
    if (flag === '--cues' && value !== undefined) { args.cues.push(...splitList(value)); i++; continue; }
    if (flag === '--max-undefined' && value !== undefined) {
      const n = Number(value);
      if (!Number.isInteger(n) || n < 0) {
        args.error = `--max-undefined must be a non-negative integer, got ${JSON.stringify(value)}`;
        return args;
      }
      args.maxUndefined = n; i++; continue;
    }
    args.error = `unknown or incomplete argument ${JSON.stringify(flag)}`;
    return args;
  }
  if (args.reply === null) args.error = 'missing required --reply';
  else if (args.terms.length === 0) args.error = 'missing required --terms (comma-separated)';
  return args;
}

/** Impure edge: read the reply, run the pure decision, print, return the exit code. */
export async function main(argv: string[]): Promise<number> {
  const args = parseArgs(argv);
  if (args.error !== null) {
    console.error(`CANNOT-RUN: ${args.error}`);
    return EXIT_CODE.CANNOT_RUN;
  }
  let reply: string;
  try {
    reply = await readFile(args.reply as string, 'utf8');
  } catch (error) {
    console.error(`CANNOT-RUN: cannot read the reply at ${args.reply}: ${String(error)}`);
    return EXIT_CODE.CANNOT_RUN;
  }
  if (reply.trim().length === 0) {
    console.log('INVALID: the reply is empty — nothing was explained');
    return EXIT_CODE.FAIL;
  }
  const evaluation = evaluateReply(reply, args.terms, args.cues);
  console.log(formatReport(evaluation, args.maxUndefined));
  return evaluation.undefined > args.maxUndefined ? EXIT_CODE.FAIL : EXIT_CODE.PASS;
}

if (import.meta.main) {
  process.exit(await main(Bun.argv.slice(2)));
}
