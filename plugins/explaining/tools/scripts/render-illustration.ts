// Self-contained page renderer for the `explaining` output style: a mermaid illustration
// (`--diagram`) or any other HTML visualization (`--body`), dressed in the Reading design
// system that ships beside the style in output-styles/design-system/.
//
// Pure core (escapeHtml, fontRefs, embedFonts, renderPage, renderIllustrationHtml) is
// exported for direct unit testing, has no side effects, and takes the design system as
// an argument. The impure edge (loadTheme and the CLI in main()) reads the design system,
// flags and files, writes the output file, and prints its absolute path. The design
// system's fonts are embedded in every page; mermaid alone is loaded from its CDN inside
// the rendered document, not bundled here.

import { readFile, writeFile } from 'node:fs/promises';
import { realpathSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// ---------------------------------------------------------------------------
// Pure core
// ---------------------------------------------------------------------------

/** Escape the five HTML-significant characters. `&` MUST be escaped first — escaping
 * it after the others would double-escape the `&` just introduced by, e.g., `&quot;`. */
export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export type Illustration = { title: string; diagram: string; caption: string };
export type Page = { title: string; lede: string; body: string };

/** The design system, as text: reading.css (its fonts already embedded) is inlined into
 * `<style>`, reading.js into the module script after the mermaid import. */
export type Theme = { css: string; script: string };

export const MERMAID_CDN_URL = 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';

/** A font the stylesheet bundles: `url('fonts/<name>.woff2')`. The name pattern admits no
 * `/` or `..`, so a reference can never reach outside the design system's fonts/ dir. */
const FONT_REF = /url\('fonts\/([A-Za-z0-9_.-]+\.woff2)'\)/g;

/** Every font file name `css` references, each once, in order of first use. */
export function fontRefs(css: string): string[] {
  return [...new Set([...css.matchAll(FONT_REF)].map((m) => m[1]))];
}

/** Replace each bundled-font url() in `css` with the file's bytes as a data: URI, so the
 * rendered page carries its fonts inside it. `fonts` maps file name to base64. Throws
 * when `css` references a font `fonts` does not carry. */
export function embedFonts(css: string, fonts: Record<string, string>): string {
  return css.replace(FONT_REF, (_, name: string) => {
    const base64 = fonts[name];
    if (base64 === undefined) throw new Error(`the stylesheet references fonts/${name}, which is missing`);
    return `url(data:font/woff2;base64,${base64})`;
  });
}

/** Render one self-contained HTML document: the title (and an optional lede) in the
 * page header, then `body` verbatim. `body` is trusted, author-written HTML; the title
 * and lede are escaped. Mermaid is loaded from the CDN at view time (major @11, the same
 * major the validator parses with, deliberately — what validates is what renders), and
 * the theme's script renders and sizes every `.mermaid` block in the body. */
export function renderPage({ title, lede, body }: Page, theme: Theme): string {
  const safeTitle = escapeHtml(title);
  const ledeHtml = lede ? `\n<p class="lede">${escapeHtml(lede)}</p>` : '';

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${safeTitle}</title>
<style>
${theme.css.trim()}
</style>
</head>
<body>
<main class="page">
<header class="page-head">
<h1>${safeTitle}</h1>${ledeHtml}
</header>
${body.trim()}
</main>
<script type="module">
import mermaid from '${MERMAID_CDN_URL}';
${theme.script.trim()}
await renderDiagrams(mermaid);
</script>
</body>
</html>
`;
}

/** One diagram as a full-width figure, with the caption beneath it. */
export function renderIllustrationHtml({ title, diagram, caption }: Illustration, theme: Theme): string {
  const body = `<figure class="diagram">
<div class="mermaid">${escapeHtml(diagram)}</div>
<figcaption>${escapeHtml(caption)}</figcaption>
</figure>`;
  return renderPage({ title, lede: '', body }, theme);
}

// ---------------------------------------------------------------------------
// Impure edge
// ---------------------------------------------------------------------------

/** Where the design system ships: beside the output style, two levels up from this
 * script. The script's own path is resolved through symlinks first, because install.sh
 * links tools/ and output-styles/ into ~/.claude separately — only the real path in the
 * plugin keeps the two as siblings. */
export const DESIGN_SYSTEM_DIR = resolve(
  dirname(realpathSync(fileURLToPath(import.meta.url))),
  '../../output-styles/design-system',
);

/** Thrown when the design system cannot be read. Caught by `main()`. */
export class ThemeError extends Error {}

export async function loadTheme(dir: string = DESIGN_SYSTEM_DIR): Promise<Theme> {
  try {
    const [css, script] = await Promise.all([
      readFile(join(dir, 'reading.css'), 'utf8'),
      readFile(join(dir, 'reading.js'), 'utf8'),
    ]);
    const fonts: Record<string, string> = {};
    await Promise.all(fontRefs(css).map(async (name) => {
      fonts[name] = (await readFile(join(dir, 'fonts', name))).toString('base64');
    }));
    return { css: embedFonts(css, fonts), script };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    throw new ThemeError(`could not read the design system at ${dir}: ${message}`);
  }
}

/** Thrown when a flag that expects a value (`--title`, `--caption`, `--diagram`,
 * `--body`, `--out`) is the last argument, or is immediately followed by another flag,
 * or when `--diagram` and `--body` are both given. Caught by `main()` and reported as a
 * clean error message, never an uncaught stack trace. */
class CliArgError extends Error {}

const KNOWN_FLAGS = new Set(['--title', '--caption', '--diagram', '--body', '--out']);

type Args = {
  title: string;
  caption: string;
  diagram: string | null;
  body: string | null;
  out: string | null;
};

function parseArgs(argv: string[]): Args {
  const args: Args = { title: '', caption: '', diagram: null, body: null, out: null };
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (KNOWN_FLAGS.has(flag)) {
      const value = argv[++i];
      if (value === undefined) throw new CliArgError(`${flag} requires a value`);
      // A value that is itself a recognized flag name means the actual value was
      // omitted — e.g. `--title --caption x` must not silently take `--caption` as
      // the title. A value that merely starts with `-` but isn't a KNOWN flag (a
      // legitimate title/caption beginning with a hyphen) is still accepted.
      if (KNOWN_FLAGS.has(value)) {
        throw new CliArgError(`${flag} requires a value, got the flag ${value} instead`);
      }
      args[flag.slice(2) as keyof Args] = value;
    }
  }
  if (args.diagram !== null && args.body !== null) {
    throw new CliArgError('give --diagram or --body, not both');
  }
  return args;
}

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString('utf8');
}

/** Flags: `--title`, `--caption`, `--out` (required), and the content — `--diagram
 * <file.mmd>` for one diagram (stdin when neither content flag is given), or `--body
 * <fragment.html>` for any other visualization, whose `--caption` becomes the lede. */
export async function main(argv: string[], themeDir: string = DESIGN_SYSTEM_DIR): Promise<number> {
  let args: Args;
  try {
    args = parseArgs(argv);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`render-illustration: ${message}`);
    return 1;
  }
  const { title, caption, diagram, body, out } = args;
  if (out === null) {
    console.error('render-illustration: --out is required');
    return 1;
  }

  let theme: Theme;
  try {
    theme = await loadTheme(themeDir);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`render-illustration: ${message}`);
    return 1;
  }

  let content: string;
  try {
    if (body !== null) content = await readFile(resolve(body), 'utf8');
    else content = diagram !== null ? await readFile(resolve(diagram), 'utf8') : await readStdin();
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`render-illustration: could not read ${body !== null ? 'body' : 'diagram'}: ${message}`);
    return 1;
  }

  const html = body !== null
    ? renderPage({ title, lede: caption, body: content }, theme)
    : renderIllustrationHtml({ title, diagram: content.trim(), caption }, theme);
  const outPath = resolve(out);
  try {
    await writeFile(outPath, html, 'utf8');
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`render-illustration: could not write --out: ${message}`);
    return 1;
  }
  console.log(outPath);
  return 0;
}

if (import.meta.main) {
  const exitCode = await main(process.argv.slice(2));
  process.exit(exitCode);
}
