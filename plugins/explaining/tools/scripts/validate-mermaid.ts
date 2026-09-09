// Embedded mermaid validator for the `explaining` skill.
//
// Pure core (decodeHtmlEntities, extractMermaidSources, hintFor, classifyOutcome,
// EXIT_CODE) is exported for direct unit testing and has no side effects. The impure
// edge (loadParser, validateSources, the CLI in main()) constructs the jsdom shim,
// imports mermaid, reads files, and installs the dependency on demand.

import { existsSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { dirname, isAbsolute, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Glob } from 'bun';

// ---------------------------------------------------------------------------
// Pure core
// ---------------------------------------------------------------------------

/** Decode the four HTML entities the extractor needs, `&amp;` LAST so a doubly
 * escaped entity like `&amp;lt;` becomes the literal text `&lt;` rather than a tag. */
export function decodeHtmlEntities(text: string): string {
  return text
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, '&');
}

/** Parse an HTML opening tag's attribute text (single- or double-quoted values) and
 * return the whitespace-split words of its `class` attribute, or `[]` if it has none.
 * Attribute-aware — a `class="..."`-shaped substring inside a DIFFERENT attribute's
 * value (e.g. `note='see class="other" here'`) is never mistaken for the real one. */
function classWordsOf(attrText: string): string[] {
  const attrPattern = /([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;
  let attrMatch: RegExpExecArray | null;
  while ((attrMatch = attrPattern.exec(attrText)) !== null) {
    if (attrMatch[1].toLowerCase() === 'class') {
      const value = attrMatch[2] !== undefined ? attrMatch[2] : attrMatch[3];
      return value.split(/\s+/).filter((word) => word.length > 0);
    }
  }
  return [];
}

/** Matches ANY HTML tag (open or close, any tag name), attribute-value-aware: the
 * alternation group consumes quoted attribute runs (which may legally contain a `>` or
 * even a `</tag>`-shaped substring, e.g. `title="fake </div> inside"`) as part of the
 * SAME tag, so a delimiter-looking character sequence embedded inside another tag's
 * quoted attribute value is never split out as its own match. `findMatchingClose` scans
 * with this generic pattern (rather than one hard-coded to `tagName`) specifically so an
 * unrelated tag's quoted attribute is fully consumed — and thus never independently
 * inspected — instead of only being protected when it happens to belong to `tagName`
 * itself. (F28) */
const ANY_TAG_PATTERN = /<(\/?)([a-zA-Z][-a-zA-Z0-9]*)\b(?:"[^"]*"|'[^']*'|[^'">])*>/g;

/** Find the closing tag that matches an already-consumed opening tag of `tagName`,
 * tracking nesting depth so a nested element of the SAME tag name (e.g. a `<div>`
 * inside a `.mermaid` `<div>`) does not truncate the match at its own closing tag.
 * `from` is the index right after the opening tag's `>`. Returns the index of the
 * matching `</tagName>` and the index right after it, or `null` if unmatched. */
function findMatchingClose(
  html: string,
  from: number,
  tagName: string,
): { bodyEnd: number; afterClose: number } | null {
  const tagPattern = new RegExp(ANY_TAG_PATTERN.source, 'gi');
  tagPattern.lastIndex = from;
  let depth = 0;
  let tagMatch: RegExpExecArray | null;
  while ((tagMatch = tagPattern.exec(html)) !== null) {
    if (tagMatch[2].toLowerCase() !== tagName) continue;
    if (tagMatch[1] === '/') {
      if (depth === 0) return { bodyEnd: tagMatch.index, afterClose: tagPattern.lastIndex };
      depth--;
    } else {
      depth++;
    }
  }
  return null;
}

/** Pull mermaid source text out of every `div`/`pre` whose `class` attribute
 * contains the word `mermaid` (extra classes allowed, single- or double-quoted),
 * decoding entities and dropping anything that trims to empty.
 *
 * Nesting guarantee: a nested element of the SAME tag name inside the container
 * (e.g. `<div class="mermaid">...<div>note</div>...</div>`) is depth-tracked, so for
 * well-formed HTML (every opening tag has a matching closing tag) the TRUE matching
 * closing tag is found and nothing after the nested element is dropped. Malformed HTML
 * (an inner tag left unclosed) may leave the container unmatched, in which case it is
 * skipped rather than truncated. */
export function extractMermaidSources(html: string): string[] {
  // Attribute-value-aware: the attribute-text group alternates quoted runs
  // (`"[^"]*"` / `'[^']*'`, which may legally contain a `>`, e.g. `title="a>b"`) with
  // unquoted characters that are not a quote or the tag-closing `>` itself. A naive
  // `[^>]*` (FB2) stops at the FIRST `>`, even one embedded inside a quoted value,
  // corrupting the extracted body with the tag's own tail.
  const openTagPattern = /<(div|pre)\b((?:"[^"]*"|'[^']*'|[^'">])*)>/gi;
  const sources: string[] = [];
  let match: RegExpExecArray | null;
  while ((match = openTagPattern.exec(html)) !== null) {
    const tagName = match[1].toLowerCase();
    if (!classWordsOf(match[2]).includes('mermaid')) continue;
    const bodyStart = openTagPattern.lastIndex;
    const closeInfo = findMatchingClose(html, bodyStart, tagName);
    if (closeInfo === null) continue;
    const decoded = decodeHtmlEntities(html.slice(bodyStart, closeInfo.bodyEnd)).trim();
    if (decoded.length > 0) sources.push(decoded);
    openTagPattern.lastIndex = closeInfo.afterClose;
  }
  return sources;
}

const FALLBACK_HINT =
  'Use only mermaid safe syntax: write dotted link ends in full (-.-x, -.-o); wrap a ' +
  'label in double quotes when it contains ( ) [ ] { } | or ", or when it starts with ' +
  '/ or \\; write a literal double quote inside a quoted label as #quot;.';

/** Map a real mermaid error string to remediation advice. Falls back to naming the
 * safe-syntax rules wholesale when the error text does not match a known signature. */
export function hintFor(errorText: string): string {
  if (/got 'PS'/.test(errorText) || /got 'PIPE'/.test(errorText)) {
    return 'Wrap the label in double quotes — it contains a character ' +
      '( ) [ ] { } | or " that mermaid cannot parse unquoted.';
  }
  if (/Lexical error on line \d+\. Unrecognized text\./.test(errorText)) {
    return 'A label starting with / or \\ is unrecognized text — wrap the label in ' +
      'double quotes.';
  }
  if (/No diagram type detected/.test(errorText)) {
    return 'The diagram type on the first line is not a real mermaid diagram type — ' +
      'use a supported one (e.g. flowchart, sequenceDiagram, classDiagram).';
  }
  if (/Expecting 'LINK', 'UNICODE_TEXT', 'EDGE_TEXT'/.test(errorText)) {
    return 'Write the dotted link end in full — -.-x or -.-o, never an abbreviated form.';
  }
  return FALLBACK_HINT;
}

export const EXIT_CODE = { VALID: 0, INVALID: 1, COULD_NOT_VALIDATE: 2 } as const;

export type Outcome = 'VALID' | 'INVALID' | 'COULD_NOT_VALIDATE';

/** Decide the overall outcome. No artifact is checked FIRST and is always INVALID,
 * even when the parser is unavailable — an absent deliverable is the agent's failure
 * and must never be excused as COULD_NOT_VALIDATE. Parser availability is checked
 * next — an unavailable parser (with an artifact present) is COULD_NOT_VALIDATE
 * regardless of anything else. Otherwise, no sources or any error is INVALID;
 * everything else is VALID. */
export function classifyOutcome(input: {
  artifacts: number;
  sources: number;
  parser: 'ready' | 'unavailable';
  errors: unknown[];
}): Outcome {
  if (input.artifacts === 0) return 'INVALID';
  if (input.parser === 'unavailable') return 'COULD_NOT_VALIDATE';
  if (input.sources === 0 || input.errors.length > 0) return 'INVALID';
  return 'VALID';
}

// ---------------------------------------------------------------------------
// Impure edge
// ---------------------------------------------------------------------------

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

type MermaidModule = { parse: (text: string) => Promise<unknown> };
type ParserHandle = { mermaid: MermaidModule } | 'unavailable';

async function buildJsdomShimAndImportMermaid(): Promise<MermaidModule> {
  const { JSDOM } = await import('jsdom');
  const dom = new JSDOM('<!DOCTYPE html><body></body>', { pretendToBeVisual: true });
  const g = globalThis as Record<string, unknown>;
  g.window = dom.window;
  g.document = dom.window.document;
  g.navigator = dom.window.navigator;
  g.Element = dom.window.Element;
  g.SVGElement = dom.window.SVGElement;
  g.HTMLElement = dom.window.HTMLElement;
  g.Node = dom.window.Node;
  g.DOMParser = dom.window.DOMParser;
  g.NodeFilter = dom.window.NodeFilter;
  g.getComputedStyle = dom.window.getComputedStyle;

  const mermaidModule = (await import('mermaid')) as unknown as {
    default: MermaidModule & { initialize: (opts: unknown) => void };
  };
  const mermaid = mermaidModule.default;
  mermaid.initialize({ startOnLoad: false, securityLevel: 'loose' });
  return mermaid;
}

/** How long an on-demand `bun install` may run before it is treated as stalled (a dead
 * or very slow network) rather than awaited forever. 120s is generous for a small
 * dependency set on a healthy connection while still bounding the worst case. */
const BUN_INSTALL_TIMEOUT_MS = 120_000;

/** Race a spawned process's `exited` promise against a timeout. Takes both as plain
 * arguments (no direct `Bun.spawn`/`setTimeout` reach-in from the caller's decision
 * logic) so a test can inject a promise that never resolves alongside a tiny
 * `timeoutMs` and observe the timeout branch deterministically, without waiting out a
 * real multi-minute stall. Never rejects — a process that exits abnormally is reported
 * via its exit code, not a thrown error. */
export async function raceExitAgainstTimeout(
  exited: Promise<number>,
  timeoutMs: number,
): Promise<{ kind: 'exited'; code: number } | { kind: 'timeout' }> {
  let timer: ReturnType<typeof setTimeout>;
  const timeout = new Promise<{ kind: 'timeout' }>((resolve) => {
    timer = setTimeout(() => resolve({ kind: 'timeout' }), timeoutMs);
  });
  try {
    return await Promise.race([
      exited.then((code) => ({ kind: 'exited' as const, code })),
      timeout,
    ]);
  } finally {
    // Clear the timer on EVERY exit from the race — including the exited-wins path —
    // so a winning exit never leaks a pending timer for the rest of timeoutMs (F30).
    // Harmless (a no-op) when the timeout itself won, since it has already fired.
    clearTimeout(timer!);
  }
}

/** Terminate a stalled `bun install` child with an unignorable SIGKILL, sent directly
 * and synchronously — never a SIGTERM followed by a later, awaited escalation. The CLI's
 * imminent `process.exit()` (see the bottom of this file) tears down the event loop
 * within milliseconds of `loadParserUncached` returning, which cancels any pending timer
 * or unresolved promise — including a grace-period escalation — before it can run. An
 * async escalation therefore never survives to fire (FA1: this replaces the F31 fix,
 * which was fire-and-forget dead code for exactly that reason). SIGKILL has no such
 * race: it is unignorable and takes effect immediately, with no grace period to lose.
 * Takes `kill` as a plain argument (no direct `proc` reach-in) so a test can verify the
 * signal sent without spawning a real process. */
export function killStalledInstall(kill: (signal: 'SIGKILL') => void): void {
  kill('SIGKILL');
}

async function loadParserUncached(): Promise<ParserHandle> {
  try {
    return { mermaid: await buildJsdomShimAndImportMermaid() };
  } catch {
    // On-demand install, once, then retry. Bounded by BUN_INSTALL_TIMEOUT_MS: a
    // stalled network must degrade to 'unavailable' (COULD_NOT_VALIDATE, exit 2)
    // rather than hang the process — "a validator that cannot run is not a failing
    // diagram" (SKILL.md Rule 4).
    try {
      const proc = Bun.spawn(['bun', 'install', '--cwd', SCRIPT_DIR], {
        stdout: 'ignore',
        stderr: 'ignore',
      });
      const result = await raceExitAgainstTimeout(proc.exited, BUN_INSTALL_TIMEOUT_MS);
      if (result.kind === 'timeout') {
        // Direct, synchronous SIGKILL — not SIGTERM plus an async escalation (FA1): the
        // CLI's process.exit() runs milliseconds after this branch returns 'unavailable'
        // and would cancel any pending grace-period timer before it could fire, leaving
        // a SIGTERM-ignoring stalled child orphaned exactly as before the F31 "fix".
        killStalledInstall((signal) => proc.kill(signal));
        return 'unavailable';
      }
      if (result.code !== 0) return 'unavailable';
      return { mermaid: await buildJsdomShimAndImportMermaid() };
    } catch {
      return 'unavailable';
    }
  }
}

let cachedParser: Promise<ParserHandle> | null = null;

/** Build the jsdom shim and dynamic-import mermaid. On ANY failure — including a
 * failed on-demand `bun install` — resolves to 'unavailable' rather than throwing,
 * so a missing dependency or missing network never surfaces as a parse failure.
 * Memoized: the shim/import/install sequence runs at most once per process. */
export async function loadParser(): Promise<ParserHandle> {
  if (cachedParser === null) cachedParser = loadParserUncached();
  return cachedParser;
}

export type SourceError = { source: string; message: string; hint: string };

/** Validate each source against the real parser. Resolves `[]` when every source
 * parses (including when the parser itself is unavailable — callers that need to
 * distinguish "could not check" from "all clear" call loadParser() themselves and
 * feed its status into classifyOutcome). Otherwise returns the failures with a
 * remediation hint attached. */
export async function validateSources(sources: string[]): Promise<SourceError[]> {
  const parser = await loadParser();
  if (parser === 'unavailable') return [];
  const failures: SourceError[] = [];
  for (const source of sources) {
    try {
      await parser.mermaid.parse(source);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      failures.push({ source, message, hint: hintFor(message) });
    }
  }
  return failures;
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

/** Thrown when a flag that expects a value (`--html-glob`, `--file`) is the last
 * argument, or is immediately followed by another flag. Caught by `main()` and
 * reported as a clean error message, never an uncaught stack trace. */
class CliArgError extends Error {}

function parseArgs(argv: string[]): { htmlGlob: string; file: string | null } {
  let htmlGlob = '*.html';
  let file: string | null = null;
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--html-glob') {
      const value = argv[++i];
      if (value === undefined) throw new CliArgError('--html-glob requires a value');
      htmlGlob = value;
    } else if (argv[i] === '--file') {
      const value = argv[++i];
      if (value === undefined) throw new CliArgError('--file requires a value');
      file = value;
    }
  }
  return { htmlGlob, file };
}

const GLOB_WILDCARD_CHARS = /[*?[{]/;

/** Split an absolute glob pattern into the longest leading wildcard-free directory
 * prefix (used as `Glob.scan`'s `cwd`) and the remaining sub-pattern scanned from
 * there. Handles a wildcard anywhere in the pattern — the final component
 * (`/abs/dir/*.html`) or a directory component in the middle (`/abs`, then a
 * wildcard segment, then `out.html`) — not just the basename, which a prior fix
 * (scanCwd = dirname(htmlGlob)) missed. */
function splitAbsoluteGlobRoot(pattern: string): { root: string; subPattern: string } {
  const segments = pattern.split('/');
  let splitIndex = segments.length - 1; // default: last segment is the sub-pattern
  for (let i = 0; i < segments.length - 1; i++) {
    if (GLOB_WILDCARD_CHARS.test(segments[i])) {
      splitIndex = i;
      break;
    }
  }
  const root = segments.slice(0, splitIndex).join('/') || '/';
  const subPattern = segments.slice(splitIndex).join('/');
  return { root, subPattern };
}

async function collectSources(htmlGlob: string, file: string | null): Promise<{
  artifacts: number;
  sources: string[];
}> {
  if (file !== null) {
    let text: string;
    try {
      text = await readFile(resolve(file), 'utf8');
    } catch {
      // A missing/unreadable --file folds into the same no-artifact path a
      // zero-match --html-glob already produces — never a crash.
      return { artifacts: 0, sources: [] };
    }
    const trimmed = text.trim();
    return { artifacts: 1, sources: trimmed.length > 0 ? [trimmed] : [] };
  }

  // Glob.scan() always walks relative to `cwd`. An absolute pattern with NO wildcard
  // characters (e.g. `/tmp/f15/out.html`) never matches under that scheme — Bun does
  // not special-case "the pattern is already an absolute path". Normalize by scanning
  // from the pattern's longest wildcard-free leading directory prefix; a relative
  // pattern (bare filename, a relative path with a directory component, or a relative
  // wildcard) is untouched, scanned from process.cwd() as before.
  const { root: scanCwd, subPattern: scanPattern } = isAbsolute(htmlGlob)
    ? splitAbsoluteGlobRoot(htmlGlob)
    : { root: process.cwd(), subPattern: htmlGlob };

  // A scan root that does not exist on disk can never produce a match — fold into the
  // ordinary no-artifact/zero-match path instead of letting Glob.scan() throw an
  // uncaught ENOENT (its normal behavior against a missing directory).
  const matches: string[] = [];
  if (existsSync(scanCwd)) {
    try {
      const glob = new Glob(scanPattern);
      for await (const match of glob.scan({ cwd: scanCwd, absolute: true })) {
        matches.push(match);
      }
    } catch {
      // No filesystem error scanning the glob may escape as a crash — a permission
      // error or a path that vanishes mid-scan folds into "no matches found".
    }
  }

  let sources: string[] = [];
  for (const path of matches) {
    try {
      const html = await readFile(path, 'utf8');
      sources = sources.concat(extractMermaidSources(html));
    } catch {
      // A matched file that vanished (or became unreadable) between scan and read
      // must not crash the process — just skip it.
    }
  }
  return { artifacts: matches.length, sources };
}

/** Usage text for `--help`/`-h`. Printed BEFORE any filesystem work (S1): without this,
 * both flags fell through to the default `*.html` glob and silently scanned the current
 * working directory instead of describing the CLI. */
const USAGE = `validate-mermaid — extract and parse every mermaid diagram in HTML artifacts.

Usage: validate-mermaid.ts [--html-glob <pattern>] [--file <path>]

  --html-glob <pattern>  Glob to scan for HTML artifacts (default: *.html).
  --file <path>          Validate a single file's raw mermaid source instead of scanning.

Exit codes:
  ${EXIT_CODE.VALID}  VALID              every extracted diagram parsed cleanly.
  ${EXIT_CODE.INVALID}  INVALID            no artifact found, or a diagram failed to parse.
  ${EXIT_CODE.COULD_NOT_VALIDATE}  COULD_NOT_VALIDATE the mermaid parser is unavailable (ship unvalidated).`;

export async function main(argv: string[]): Promise<number> {
  if (argv.includes('--help') || argv.includes('-h')) {
    console.log(USAGE);
    return EXIT_CODE.VALID;
  }
  let htmlGlob: string;
  let file: string | null;
  try {
    ({ htmlGlob, file } = parseArgs(argv));
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`validate-mermaid: ${message}`);
    return EXIT_CODE.INVALID;
  }
  const { artifacts, sources } = await collectSources(htmlGlob, file);

  const parser = await loadParser();
  let errors: SourceError[] = [];
  if (parser !== 'unavailable' && sources.length > 0) {
    errors = await validateSources(sources);
  }

  const outcome = classifyOutcome({
    artifacts,
    sources: sources.length,
    parser: parser === 'unavailable' ? 'unavailable' : 'ready',
    errors,
  });

  console.log(`${outcome}: ${sources.length} diagram(s) found, ${errors.length} error(s)`);
  for (const failure of errors) {
    console.log(`  - ${failure.message}`);
    console.log(`    hint: ${failure.hint}`);
  }
  if (outcome === 'COULD_NOT_VALIDATE') {
    console.log('  (mermaid parser unavailable — ship the file anyway, unvalidated)');
  }

  return EXIT_CODE[outcome];
}

if (import.meta.main) {
  const exitCode = await main(process.argv.slice(2));
  process.exit(exitCode);
}
