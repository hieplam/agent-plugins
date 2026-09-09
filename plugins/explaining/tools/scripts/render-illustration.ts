// Self-contained mermaid illustration renderer for the `explaining` skill.
//
// Pure core (escapeHtml, renderIllustrationHtml) is exported for direct unit testing
// and has no side effects, and is dependency-free so it works offline: mermaid itself
// is loaded from the CDN inside the rendered document, not bundled here. The impure
// edge (the CLI in main()) reads flags/files, writes the output file, and prints its
// absolute path.

import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

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

const MERMAID_CDN_URL = 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';

/** Render one self-contained HTML document: title, mermaid diagram inside
 * `<div class="mermaid">`, and a caption. Mermaid itself is loaded from the CDN at
 * view time (major @11, the same major the validator parses with, deliberately —
 * what validates is what renders); nothing else is fetched remotely. Light/dark is
 * driven by CSS custom properties plus a `prefers-color-scheme: dark` override, and
 * the module script initializes mermaid with a matching theme. */
export function renderIllustrationHtml({ title, diagram, caption }: Illustration): string {
  const safeTitle = escapeHtml(title);
  const safeCaption = escapeHtml(caption);
  const safeDiagram = escapeHtml(diagram);

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${safeTitle}</title>
<style>
  :root {
    --bg: #ffffff;
    --fg: #1a1a1a;
    --caption-fg: #555555;
    --border: #dddddd;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #1a1a1a;
      --fg: #f0f0f0;
      --caption-fg: #aaaaaa;
      --border: #444444;
    }
  }
  body {
    margin: 0;
    padding: 2rem;
    background: var(--bg);
    color: var(--fg);
    font-family: system-ui, sans-serif;
  }
  h1 {
    font-size: 1.25rem;
    margin: 0 0 1rem 0;
  }
  .diagram-wrap {
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1rem;
    overflow: auto;
  }
  figcaption {
    margin-top: 0.75rem;
    color: var(--caption-fg);
    font-size: 0.9rem;
  }
</style>
</head>
<body>
<h1>${safeTitle}</h1>
<figure class="diagram-wrap">
<div class="mermaid">${safeDiagram}</div>
<figcaption>${safeCaption}</figcaption>
</figure>
<script type="module">
  import mermaid from '${MERMAID_CDN_URL}';
  mermaid.initialize({
    startOnLoad: true,
    theme: window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'default',
  });
</script>
</body>
</html>
`;
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

/** Thrown when a flag that expects a value (`--title`, `--caption`, `--diagram`,
 * `--out`) is the last argument, or is immediately followed by another flag. Caught by
 * `main()` and reported as a clean error message, never an uncaught stack trace. */
class CliArgError extends Error {}

const KNOWN_FLAGS = new Set(['--title', '--caption', '--diagram', '--out']);

function parseArgs(argv: string[]): {
  title: string;
  caption: string;
  diagram: string | null;
  out: string | null;
} {
  let title = '';
  let caption = '';
  let diagram: string | null = null;
  let out: string | null = null;
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === '--title' || flag === '--caption' || flag === '--diagram' || flag === '--out') {
      const value = argv[++i];
      if (value === undefined) throw new CliArgError(`${flag} requires a value`);
      // A value that is itself a recognized flag name means the actual value was
      // omitted — e.g. `--title --caption x` must not silently take `--caption` as
      // the title. A value that merely starts with `-` but isn't a KNOWN flag (a
      // legitimate title/caption beginning with a hyphen) is still accepted.
      if (KNOWN_FLAGS.has(value)) {
        throw new CliArgError(`${flag} requires a value, got the flag ${value} instead`);
      }
      if (flag === '--title') title = value;
      else if (flag === '--caption') caption = value;
      else if (flag === '--diagram') diagram = value;
      else out = value;
    }
  }
  return { title, caption, diagram, out };
}

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString('utf8');
}

export async function main(argv: string[]): Promise<number> {
  let title: string, caption: string, diagram: string | null, out: string | null;
  try {
    ({ title, caption, diagram, out } = parseArgs(argv));
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`render-illustration: ${message}`);
    return 1;
  }
  if (out === null) {
    console.error('render-illustration: --out is required');
    return 1;
  }

  let diagramText: string;
  try {
    diagramText = diagram !== null ? await readFile(resolve(diagram), 'utf8') : await readStdin();
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`render-illustration: could not read diagram: ${message}`);
    return 1;
  }

  const html = renderIllustrationHtml({ title, diagram: diagramText.trim(), caption });
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
