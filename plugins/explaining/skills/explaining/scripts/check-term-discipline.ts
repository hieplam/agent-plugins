// Term-discipline check for the `explaining` skill's Rule 1 / the Todd way style's B2
// ("define before use").
//
// Oracle — the spec in this comment is the contract; no external standard is:
//
//   For every listed term that the reply USES, its FIRST use in prose must be
//   INTRODUCED. A first use is introduced when the sentence it sits in carries a
//   definitional cue attached to the term:
//     - directly after the term: a colon ("WAL: the append-only file...");
//     - within one word after: "="; within two words after: a dash, an appositive comma
//       or an opening parenthesis ("a separate B-tree on disk (a sorted tree of pages)");
//       (", which", ", the", ", a", ", i.e.", ", meaning", ", where", ", also known
//       as", ", short for" ...);
//     - within AFTER_WINDOW words after: a copula ("is", "are", "means", "refers to",
//       "stands for", "denotes", "describes");
//     - within one word after: a does-verb that says what a setting, tool or mechanism
//       does ("caps", "controls", "rewrites", "lets", "logs", ...) — "MaxDeliver caps
//       redelivery", "`workqueue` retention deletes a message" introduce the term;
//     - within three words after: a table cell border "|" — a settings table whose columns
//       say what each setting does is a definition list;
//     - within BEFORE_WINDOW words before: "called", "known as", "termed", "dubbed",
//       "so-called", "named", "referred to as", "marked as", or a predicative copula
//       ("A modified page is *dirty* until ...");
//     - the term opens a parenthesis right after at least one word — the acronym-expansion
//       shape "write-ahead log (WAL)", "holds a few back (`setting`, default 3)", "a pooler
//       (PgBouncer in transaction mode)", or a dash pair "the last few — `setting`, default 3 —";
//     - a cap named by "at most" / "up to" / "capped at" before it ("at most
//       `max_connections` connections"), or a dash right before the term naming what the
//       sentence just described ("retried in the same 500 ms beats — a thundering herd");
//     - any extra cue the caller passes with --cues (another language: "là",
//       "tức là", "gọi là" ...), on either side, with the copula window.
//   A term the reply never uses is UNUSED and needs no introduction: avoiding jargon
//   is term discipline too. Fenced code blocks are not prose and are not scanned. A
//   markdown heading or a bold lead-in line is read together with the sentence after
//   it, and a term whose only appearance there is in the title itself is judged at its
//   first PROSE use instead: a title names the section, the definition arrives in the
//   prose ("## The HOT cliff" ... "The HOT optimization (Heap-Only Tuple) avoids that").
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

/** Verbs that introduce a setting, tool or mechanism by saying what it does. Both the
 * base and the third-person form count ("full-page writes log ...", "MaxDeliver caps ..."). */
const DOES_VERBS = ['cap', 'control', 'limit', 'bound', 'govern', 'set', 'specify', 'determine',
  'tell', 'rewrite', 'write', 'log', 'record', 'let', 'allow', 'run', 'keep', 'hold', 'reclaim',
  'track', 'store', 'mark', 'remove', 'defer', 'pin', 'reserve', 'multiplex', 'share', 'issue',
  'sign', 'rotate', 'push', 'deliver', 'replace', 'suspend', 'gate', 'throttle', 'decide',
  'pick', 'choose', 'flush', 'evict', 'compact', 'reject', 'refuse', 'accept', 'delete',
  'wait', 'expire', 'redeliver', 'drop', 'return', 'send', 'stop'];

function thirdPerson(verb: string): string {
  if (/(s|x|z|ch|sh)$/.test(verb)) return `${verb}es`;
  if (/[^aeiou]y$/.test(verb)) return `${verb.slice(0, -1)}ies`;
  return `${verb}s`;
}

// Word cues carry a leading space so "this" never matches " is ".
const AFTER_CUES: AfterCue[] = [
  { cue: ':', gap: 0 },
  { cue: '—', gap: 2 }, { cue: '–', gap: 2 }, { cue: ' - ', gap: 2 }, { cue: '(', gap: 2 }, { cue: '=', gap: 1 },
  { cue: ', which', gap: 2 }, { cue: ', that is', gap: 2 }, { cue: ', i.e', gap: 2 },
  { cue: ', ie ', gap: 2 }, { cue: ', meaning', gap: 2 }, { cue: ', where', gap: 2 },
  { cue: ', a ', gap: 2 }, { cue: ', an ', gap: 2 }, { cue: ', the ', gap: 2 },
  { cue: ', also known as', gap: 2 }, { cue: ', also called', gap: 2 }, { cue: ', aka', gap: 2 },
  { cue: ', short for', gap: 2 }, { cue: ', or ', gap: 2 },
  // A knob or setting is introduced by saying what it does: "MaxDeliver caps redelivery".
  ...DOES_VERBS.flatMap((verb) => [verb, thirdPerson(verb)]).map((verb) => ({ cue: ` ${verb} `, gap: 1 })),
  // A definition table: "| `AckWait` | 30s | How long the server waits ... |".
  { cue: ' | ', gap: 3 },
  { cue: ' is ', gap: AFTER_WINDOW }, { cue: ' are ', gap: AFTER_WINDOW },
  { cue: ' means ', gap: AFTER_WINDOW }, { cue: ' refers to', gap: AFTER_WINDOW },
  { cue: ' stands for', gap: AFTER_WINDOW }, { cue: ' denotes ', gap: AFTER_WINDOW },
  { cue: ' describes ', gap: AFTER_WINDOW },
];

const BEFORE_CUES = ['called', 'known as', 'termed', 'dubbed', 'so-called', 'named',
  'referred to as', 'marked as', 'is', 'are', 'becomes', 'become',
  'at most', 'up to', 'a maximum of', 'a limit of', 'capped at', 'limited to'];

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

export interface Paragraph {
  text: string;
  /** Length of the glued heading prefix (including its trailing space); 0 when none. */
  headingLen: number;
}

export interface Sentence {
  text: string;
  /** Length of the heading prefix inside this sentence; 0 for every sentence but the
   * first of a heading-led paragraph. A term inside that prefix is a title, not prose. */
  headingLen: number;
}

/** Pure: markdown → paragraphs, with a heading or bold lead-in glued onto the
 * paragraph that follows it, and each bullet its own paragraph. */
export function splitParagraphsWithHeadings(text: string): Paragraph[] {
  const paragraphs: Paragraph[] = [];
  let current = '';
  let carry = '';
  let currentHeadingLen = 0;
  const flush = () => {
    if (current.trim()) paragraphs.push({ text: current.trim(), headingLen: currentHeadingLen });
    current = '';
    currentHeadingLen = 0;
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
    if (carry) { current = `${carry} ${current}`; currentHeadingLen = carry.length + 1; carry = ''; }
  }
  if (carry) { current = `${carry} ${current}`; currentHeadingLen = carry.length + 1; }
  flush();
  return paragraphs;
}

export function splitParagraphs(text: string): string[] {
  return splitParagraphsWithHeadings(text).map((p) => p.text);
}

/** Pure: paragraphs → sentences. A split happens after . ! ? when the next
 * sentence opens with a capital, a quote, a bracket, or markdown emphasis. */
export function splitSentencesWithHeadings(text: string): Sentence[] {
  const sentences: Sentence[] = [];
  for (const paragraph of splitParagraphsWithHeadings(text)) {
    let pos = 0; // offset of the next piece inside paragraph.text
    for (const piece of paragraph.text.split(/(?<=[.!?])\s+(?=[\p{Lu}"'*`(])/u)) {
      const s = piece.trim();
      const start = paragraph.text.indexOf(piece, pos);
      pos = start + piece.length;
      if (s.length === 0) continue;
      // The heading prefix may span several "sentences" ("## 2. The HOT cliff" splits at
      // "2."), so each piece keeps whatever part of the prefix falls inside it.
      const headingLen = Math.max(0, Math.min(paragraph.headingLen - start, s.length));
      sentences.push({ text: s, headingLen });
    }
  }
  return sentences;
}

export function splitSentences(text: string): string[] {
  return splitSentencesWithHeadings(text).map((s) => s.text);
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

function asSentence(s: string | Sentence): Sentence {
  return typeof s === 'string' ? { text: s, headingLen: 0 } : s;
}

/** Pure: the first PROSE use of the term, plus the match offsets in its sentence. A use
 * inside a heading title is skipped (a title names the section; the definition belongs to
 * the first prose use), and is fallen back on only when the term never appears in prose. */
export function findTerm(sentences: Array<string | Sentence>, term: string): { index: number; start: number; end: number } | null {
  const pattern = termPattern(term);
  let headingOnly: { index: number; start: number; end: number } | null = null;
  for (let i = 0; i < sentences.length; i++) {
    const { text, headingLen } = asSentence(sentences[i]);
    const re = new RegExp(pattern.source, pattern.flags + 'g');
    let m: RegExpExecArray | null;
    while ((m = re.exec(text)) !== null) {
      const hit = { index: i, start: m.index, end: m.index + m[0].length };
      if (m.index < headingLen) { headingOnly ??= hit; continue; }
      return hit;
    }
  }
  return headingOnly;
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
  // "<what just happened> — a thundering herd ...": the dash names the phenomenon.
  if (/[—–]$/.test(lead)) return true;
  const window = wordsOf(lead).slice(-BEFORE_WINDOW).join(' ');
  for (const raw of [...BEFORE_CUES, ...extraCues]) {
    const cue = raw.trim().toLowerCase();
    if (window === cue || window.endsWith(` ${cue}`)) return true;
  }
  return false;
}

/** Pure: the naming shape — `expansion (TERM)`, `(TERM, default 3)`, or the same with a
 * dash pair, `the last few — TERM, default 3 —` — with at least one word before it. */
export function isParentheticalExpansion(before: string, after: string): boolean {
  // The term opens a parenthesis after a word: "(WAL)", "(`setting`, default 3)", or
  // "a pooler (PgBouncer in transaction mode)" — the parenthesis names the noun before it.
  const opensParen = /\(\s*[`"*]*$/.test(before);
  const opensDash = /[—–]\s*[`"*]*$/.test(before);
  const closesDash = /^[`"*]*\s*(?:,|[—–])/.test(after); // "— `setting`, default 3 —"
  const preceded = wordsOf(before.replace(/[(—–]\s*[`"*]*$/, '')).length >= 1;
  return preceded && (opensParen || (opensDash && closesDash));
}

/** Pure: classify one term against the reply's sentences. */
export function classifyTerm(sentences: Array<string | Sentence>, term: string, extraCues: string[] = []): TermVerdict {
  const hit = findTerm(sentences, term);
  if (hit === null) return { term, status: 'UNUSED', sentence: '' };
  const sentence = asSentence(sentences[hit.index]).text;
  const before = sentence.slice(0, hit.start);
  const after = sentence.slice(hit.end);
  const defined = hasAfterCue(after, extraCues) || hasBeforeCue(before, extraCues)
    || isParentheticalExpansion(before, after);
  return { term, status: defined ? 'DEFINED' : 'UNDEFINED', sentence };
}

/** Pure: the whole decision for one reply. */
export function evaluateReply(reply: string, terms: string[], extraCues: string[] = []): Evaluation {
  const sentences = splitSentencesWithHeadings(stripFences(reply));
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
export function formatReport(evaluation: Evaluation, allowed: number, limit = 160): string {
  const lines = evaluation.verdicts.map((v) => v.status === 'UNUSED'
    ? `UNUSED    ${JSON.stringify(v.term)}`
    : `${v.status.padEnd(9)} ${JSON.stringify(v.term)} — ${excerpt(v.sentence, limit)}`);
  const verdict = evaluation.undefined > allowed ? 'INVALID' : 'VALID';
  lines.push(`${verdict}: ${evaluation.undefined} undefined of ${evaluation.used} used `
    + `(${evaluation.verdicts.length} listed, ${allowed} allowed)`);
  return lines.join('\n');
}

export interface CliArgs {
  reply: string | null;
  terms: string[];
  cues: string[];
  maxUndefined: number;
  maxUndefinedRatio: number;
  error: string | null;
}

/** Pure: how many bare terms the floor tolerates for this reply — the larger of the
 * absolute allowance and the share of USED terms. The floor exists to refuse a mass
 * bare drop (a baseline reply leaves 60–100% of its terms bare); a one-off miss on a
 * long reply is the grader's call, not the script's. */
export function allowedUndefined(used: number, maxUndefined: number, maxUndefinedRatio: number): number {
  return Math.max(maxUndefined, Math.floor(used * maxUndefinedRatio));
}

function splitList(raw: string): string[] {
  return raw.split(',').map((s) => s.trim()).filter((s) => s.length > 0);
}

/** Pure: argv → args, or a named error. */
export function parseArgs(argv: string[]): CliArgs {
  const args: CliArgs = { reply: null, terms: [], cues: [], maxUndefined: 0, maxUndefinedRatio: 0, error: null };
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
    if (flag === '--max-undefined-ratio' && value !== undefined) {
      const r = Number(value);
      if (!Number.isFinite(r) || r < 0 || r > 1) {
        args.error = `--max-undefined-ratio must be a number in [0, 1], got ${JSON.stringify(value)}`;
        return args;
      }
      args.maxUndefinedRatio = r; i++; continue;
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
  const allowed = allowedUndefined(evaluation.used, args.maxUndefined, args.maxUndefinedRatio);
  console.log(formatReport(evaluation, allowed));
  return evaluation.undefined > allowed ? EXIT_CODE.FAIL : EXIT_CODE.PASS;
}

if (import.meta.main) {
  process.exit(await main(Bun.argv.slice(2)));
}
