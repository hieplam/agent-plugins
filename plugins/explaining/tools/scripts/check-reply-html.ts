// Reply-HTML check for the `explaining` skill's B6 rule ("when the first explanation
// didn't land, show it") — plan-b6-reask-html.md §T2 + amendment A2.
//
// Oracle (the plan is the contract; this comment states the Oracle it implements):
//
//   --expect present: exit 0 iff the reply names at least one `.html` path (absolute,
//   or relative to the executor's cwd) that (a) exists on disk, (b) was WRITTEN
//   DURING THE FINAL TURN — absent from `--manifest`, or present with a changed
//   sha256 (A2's `files_written_since_manifest` shape, mirrored here) — and, with
//   `--themed`, (c) carries render-illustration.ts's own generator marker (proving
//   the page was actually produced by that renderer, not hand-written to look
//   similar) AND (d) has a non-trivial body (see bodyHasNonTrivialContent below).
//   Passing a stub — an .html file the reply names that was never produced by
//   render-illustration.ts, or a themed page whose body is empty or one short
//   paragraph — is a BUG in this script, not an acceptable pass.
//
//   --expect absent: exit 0 iff no `.html` file anywhere under cwd (recursive) was
//   written during the final turn, by the same written-since-manifest test.
//
// Pure core (extractHtmlPaths, writtenSinceManifest, hasGeneratorMarker,
// extractThemedBody, bodyHasNonTrivialContent, evaluatePresent, evaluateAbsent,
// formatReport, parseArgs) is exported for direct unit testing and has no side
// effects. The impure edge (reading the reply/manifest/candidate files, scanning cwd,
// the exit code) lives in buildCandidate, collectHtmlFiles and main().

import { existsSync, readFileSync, realpathSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { isAbsolute, relative, resolve } from 'node:path';
import { Glob } from 'bun';
import { GENERATOR_META_TAG } from './render-illustration';

export const EXIT_CODE = { PASS: 0, FAIL: 1, CANNOT_RUN: 2 } as const;

export type Expect = 'present' | 'absent';

// ---------------------------------------------------------------------------
// Pure core
// ---------------------------------------------------------------------------

/** Pure: every distinct `.html` path-looking token in free-form prose, in first-seen
 * order — a bare relative name (`out.html`), a relative path with directories
 * (`session/out.html`), or an absolute path (`/tmp/session/out.html`), however it is
 * wrapped in the prose (backticks, a markdown link's parens, a trailing period or
 * comma) — none of those wrapper characters is part of the token this pattern
 * matches, so the wrapping falls away on its own. */
const HTML_PATH_TOKEN = /\/?[A-Za-z0-9_][A-Za-z0-9_\-./]*\.html\b/g;

export function extractHtmlPaths(reply: string): string[] {
  return [...new Set([...reply.matchAll(HTML_PATH_TOKEN)].map((m) => m[0]))];
}

export interface ManifestEntry { path: string; sha256: string }

/** Pure: was the file at `relPath` (already resolved relative to cwd) written during
 * the final turn? Absent from the manifest (brand new) or present with a different
 * sha256 (overwritten) counts; the same hash means the final turn left it untouched.
 * `relPath === null` means the candidate resolved outside cwd, where the
 * scratch-relative manifest can never prove anything either way — an unprovable claim
 * is never treated as written (fail-closed-edges). Mirrors
 * `files_written_since_manifest` in scripts/evals/run_evals.py (A2), independently,
 * since this script runs as its own subprocess and cannot import Python. */
export function writtenSinceManifest(
  relPath: string | null, sha256: string | null, manifest: ManifestEntry[],
): boolean {
  if (relPath === null || sha256 === null) return false;
  const prior = manifest.find((e) => e.path === relPath)?.sha256;
  return prior !== sha256;
}

/** Pure: does this HTML carry the marker only render-illustration.ts's own renderPage()
 * writes? Proves the page came from that renderer rather than being hand-written or
 * copied to merely look similar (A2). */
export function hasGeneratorMarker(html: string): boolean {
  return html.includes(GENERATOR_META_TAG);
}

/** Pure: the fragment renderPage() placed between the header and `</main>` — the
 * "content" half of the page, as opposed to the page shell the design system owns —
 * or `null` when the HTML is not shaped like that renderer's output at all (so a
 * themed check on it fails outright, not on a coincidental content check). */
export function extractThemedBody(html: string): string | null {
  const match = html.match(/<\/header>\n([\s\S]*?)\n<\/main>/);
  return match ? match[1] : null;
}

function plainTextWordCount(fragment: string): number {
  const text = fragment.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
  return text.length === 0 ? 0 : text.split(' ').length;
}

function elementCount(fragment: string): number {
  return (fragment.match(/<[A-Za-z][^>]*>/g) ?? []).length;
}

/** A body under this many plain-text words, with no more real markup than a single
 * wrapper tag, reads as "one short paragraph" — the Oracle's other named stub shape
 * (the first being a missing generator marker). 40 words is comfortably above a short
 * paragraph's usual span (roughly one or two sentences, ~15-30 words) and comfortably
 * below a real explanatory passage, so it separates "a couple of sentences" from
 * "actual content" without being so high that a terse-but-real page trips it. */
export const MIN_BODY_WORDS = 40;

/** A body with at least this many HTML elements has real structure — more than a
 * single wrapper tag around prose — whatever that structure's visual form is (this
 * check names none, per the plan's scope fence): a figure with a caption, a group of
 * cells, a cluster of shapes. 3 is the smallest count that rules out "one tag holding
 * one paragraph" while still admitting a genuinely small but real composition. */
export const MIN_BODY_ELEMENTS = 3;

/** Pure: false for an empty body or a single short paragraph pasted in as-is — the two
 * stub shapes the Oracle names for --themed. True for anything with real prose
 * (>= MIN_BODY_WORDS) or real structure (>= MIN_BODY_ELEMENTS elements), in whatever
 * form the model chose; this check enforces no particular visual form. */
export function bodyHasNonTrivialContent(body: string): boolean {
  return plainTextWordCount(body) >= MIN_BODY_WORDS || elementCount(body) >= MIN_BODY_ELEMENTS;
}

/** A `.html` path extracted from the reply, already resolved and inspected by the
 * impure edge (buildCandidate) — everything evaluatePresent needs to judge it, so the
 * judgment itself stays pure and independently testable. */
export interface Candidate {
  token: string;
  relToCwd: string | null;
  exists: boolean;
  sha256: string | null;
  /** File text, read only when --themed needs it; null otherwise or if unreadable. */
  content: string | null;
}

export interface Verdict { ok: boolean; reasons: string[] }

/** Pure (A2): the whole `--expect present` decision. At least one candidate must
 * qualify — exist, have been written during the final turn, and (with `themed`) carry
 * the generator marker and a non-trivial body. Every disqualified candidate's reason
 * is collected so a failing run's evidence names exactly what was wrong with each
 * path the reply named, not just a bare exit code. */
export function evaluatePresent(candidates: Candidate[], manifest: ManifestEntry[], themed: boolean): Verdict {
  if (candidates.length === 0) {
    return { ok: false, reasons: ['the reply names no .html path'] };
  }
  const reasons: string[] = [];
  for (const c of candidates) {
    if (!c.exists) {
      reasons.push(`${c.token}: no such file`);
      continue;
    }
    if (!writtenSinceManifest(c.relToCwd, c.sha256, manifest)) {
      reasons.push(`${c.token}: not written during the final turn (unchanged since before it, or outside the scratch tree)`);
      continue;
    }
    if (!themed) return { ok: true, reasons: [] };
    if (c.content === null || !hasGeneratorMarker(c.content)) {
      reasons.push(`${c.token}: no render-illustration.ts generator marker — not produced by the renderer`);
      continue;
    }
    const body = extractThemedBody(c.content);
    if (body === null) {
      reasons.push(`${c.token}: not shaped like a render-illustration.ts page`);
      continue;
    }
    if (!bodyHasNonTrivialContent(body)) {
      reasons.push(`${c.token}: stub — body is empty or a single short paragraph`);
      continue;
    }
    return { ok: true, reasons: [] };
  }
  return { ok: false, reasons };
}

export interface HtmlFileEntry { relPath: string; sha256: string }

/** Pure (A2): the whole `--expect absent` decision — every `.html` file under cwd
 * that was written during the final turn is a violation. */
export function evaluateAbsent(files: HtmlFileEntry[], manifest: ManifestEntry[]): { ok: boolean; violations: string[] } {
  const violations = files
    .filter((f) => writtenSinceManifest(f.relPath, f.sha256, manifest))
    .map((f) => f.relPath);
  return { ok: violations.length === 0, violations };
}

export function formatReport(expect: Expect, ok: boolean, notes: string[]): string {
  const verdict = ok ? 'VALID' : 'INVALID';
  const lines = notes.map((n) => `  - ${n}`);
  return [`${verdict}: expect=${expect}`, ...lines].join('\n');
}

export interface CliArgs {
  reply: string | null;
  manifest: string | null;
  expect: Expect | null;
  themed: boolean;
  error: string | null;
}

/** Pure: argv to options, or a named error. A malformed invocation is a SETUP error
 * (CANNOT_RUN), never a verdict on the reply. */
export function parseArgs(argv: string[]): CliArgs {
  const args: CliArgs = { reply: null, manifest: null, expect: null, themed: false, error: null };
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === '--themed') { args.themed = true; continue; }
    const value = argv[i + 1];
    if (flag === '--reply' || flag === '--manifest' || flag === '--expect') {
      if (value === undefined) { args.error = `${flag} requires a value`; return args; }
      i++;
      if (flag === '--reply') args.reply = value;
      else if (flag === '--manifest') args.manifest = value;
      else if (value === 'present' || value === 'absent') args.expect = value;
      else { args.error = `--expect must be "present" or "absent", got ${JSON.stringify(value)}`; return args; }
      continue;
    }
    args.error = `unknown argument: ${flag}`;
    return args;
  }
  if (args.reply === null) args.error = 'missing required --reply';
  else if (args.manifest === null) args.error = 'missing required --manifest';
  else if (args.expect === null) args.error = 'missing required --expect';
  return args;
}

// ---------------------------------------------------------------------------
// Impure edge
// ---------------------------------------------------------------------------

function sha256Of(bytes: Buffer): string {
  return createHash('sha256').update(bytes).digest('hex');
}

/** Impure edge: resolve a path that is known to exist by following every symlink in
 * it — including a symlinked ANCESTOR directory (e.g. macOS's `/var` -> `/private/var`,
 * which every OS tmpdir-based scratch dir sits under) — so two different spellings of
 * the same file compare equal instead of diverging lexically. Falls back to the
 * unresolved path on any realpath failure (permission, a race where the file vanishes
 * between the caller's existence check and this call) so a transient OS error degrades
 * to the pre-fix, lexical comparison rather than crashing the check (fail-closed-edges). */
function canonicalize(path: string): string {
  try {
    return realpathSync(path);
  } catch {
    return path;
  }
}

/** Impure edge: resolve one extracted token against cwd, and — if the file exists —
 * hash it and, when `themed` needs it, read its text. A read failure (permission,
 * vanished between existsSync and readFileSync) folds into "does not exist" rather
 * than crashing the check.
 *
 * Containment (fail-closed-edges obligation 4) is decided on the CANONICAL
 * (symlink-resolved) form of both cwd and the candidate path, not their literal
 * spelling: a real file reached through a symlinked ancestor (the reply's absolute
 * path spelled differently from cwd, but the same file) must be treated as inside;
 * a symlink that lives inside the scratch tree but points outside it must still be
 * treated as outside. Comparing the literal token's path gets both of those wrong. */
function buildCandidate(token: string, cwd: string, themed: boolean): Candidate {
  const absPath = isAbsolute(token) ? token : resolve(cwd, token);
  if (!existsSync(absPath)) {
    const rel = relative(cwd, absPath);
    const relToCwd = rel.startsWith('..') || isAbsolute(rel) ? null : rel;
    return { token, relToCwd, exists: false, sha256: null, content: null };
  }
  const realCwd = canonicalize(cwd);
  const realAbsPath = canonicalize(absPath);
  const rel = relative(realCwd, realAbsPath);
  const relToCwd = rel.startsWith('..') || isAbsolute(rel) ? null : rel;
  try {
    const bytes = readFileSync(realAbsPath);
    return {
      token, relToCwd, exists: true, sha256: sha256Of(bytes),
      content: themed ? bytes.toString('utf8') : null,
    };
  } catch {
    return { token, relToCwd, exists: false, sha256: null, content: null };
  }
}

/** Impure edge: every `.html` file under cwd, hashed, for the `--expect absent` scan.
 * A file that vanishes or becomes unreadable between the scan and the read is skipped,
 * never a crash. */
async function collectHtmlFiles(cwd: string): Promise<HtmlFileEntry[]> {
  const entries: HtmlFileEntry[] = [];
  try {
    for await (const match of new Glob('**/*.html').scan({ cwd, onlyFiles: true })) {
      try {
        entries.push({ relPath: match, sha256: sha256Of(readFileSync(resolve(cwd, match))) });
      } catch {
        // vanished or became unreadable between scan and read — not this check's job.
      }
    }
  } catch {
    // a scan failure (e.g. cwd removed mid-run) folds into "no files found".
  }
  return entries;
}

function readManifest(path: string): ManifestEntry[] {
  const raw: unknown = JSON.parse(readFileSync(path, 'utf8'));
  if (!Array.isArray(raw)) throw new Error('manifest is not a JSON array');
  return raw.map((e) => {
    const entry = e as Record<string, unknown>;
    if (typeof entry.path !== 'string' || typeof entry.sha256 !== 'string') {
      throw new Error('manifest entry missing path/sha256');
    }
    return { path: entry.path, sha256: entry.sha256 };
  });
}

/** Impure edge: read the reply and the manifest, run the pure decision, print, and
 * return the exit code the harness reads. `cwd` defaults to the process's real cwd
 * (the scratch dir the harness runs checks in) but is injectable so tests never
 * depend on process.chdir(). */
export async function main(argv: string[], cwd: string = process.cwd()): Promise<number> {
  const args = parseArgs(argv);
  if (args.error !== null) {
    console.error(`CANNOT-RUN: ${args.error}`);
    return EXIT_CODE.CANNOT_RUN;
  }

  let replyText: string;
  try {
    replyText = readFileSync(args.reply as string, 'utf8');
  } catch (error) {
    console.error(`CANNOT-RUN: cannot read --reply at ${args.reply}: ${String(error)}`);
    return EXIT_CODE.CANNOT_RUN;
  }

  let manifest: ManifestEntry[];
  try {
    manifest = readManifest(args.manifest as string);
  } catch (error) {
    console.error(`CANNOT-RUN: cannot read --manifest at ${args.manifest}: ${String(error)}`);
    return EXIT_CODE.CANNOT_RUN;
  }

  if (args.expect === 'present') {
    const candidates = extractHtmlPaths(replyText).map((token) => buildCandidate(token, cwd, args.themed));
    const result = evaluatePresent(candidates, manifest, args.themed);
    console.log(formatReport('present', result.ok, result.reasons));
    return result.ok ? EXIT_CODE.PASS : EXIT_CODE.FAIL;
  }

  const files = await collectHtmlFiles(cwd);
  const result = evaluateAbsent(files, manifest);
  console.log(formatReport('absent', result.ok, result.violations));
  return result.ok ? EXIT_CODE.PASS : EXIT_CODE.FAIL;
}

if (import.meta.main) {
  process.exit(await main(Bun.argv.slice(2)));
}
