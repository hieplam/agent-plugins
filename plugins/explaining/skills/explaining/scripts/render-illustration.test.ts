import { describe, expect, spyOn, test } from 'bun:test';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { escapeHtml, main, renderIllustrationHtml } from './render-illustration';
import { extractMermaidSources } from './validate-mermaid';

const DIAGRAM = 'flowchart TD\n  A["shaman (what & why)"] --> B["warchief"]\n  B -.-x C';

describe('renderIllustrationHtml', () => {
  const html = renderIllustrationHtml({
    title: 'Tribe flow', diagram: DIAGRAM, caption: 'Who dispatches whom.',
  });

  test('is one self-contained document', () => {
    expect(html.startsWith('<!DOCTYPE html>')).toBe(true);
    expect(html.trimEnd().endsWith('</html>')).toBe(true);
  });

  test('loads mermaid from the CDN and nothing else remote', () => {
    expect(html).toContain('https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs');
  });

  test('supports light and dark', () => {
    expect(html).toContain('prefers-color-scheme: dark');
    expect(html).toContain('matchMedia');
  });

  test('escapes the title and caption', () => {
    const evil = renderIllustrationHtml({
      title: '<script>x</script>', diagram: DIAGRAM, caption: 'a & b',
    });
    expect(evil).not.toContain('<script>x</script>');
    expect(evil).toContain('a &amp; b');
  });

  test('round-trips the diagram back out through the validator extractor', () => {
    expect(extractMermaidSources(html)).toEqual([DIAGRAM]);
  });

  test('escapeHtml handles the five characters that matter', () => {
    expect(escapeHtml(`<&>"'`)).toBe('&lt;&amp;&gt;&quot;&#39;');
  });
});

describe('main() CLI — clean verdicts on bad input (F17, F18)', () => {
  function withDiagramFile<T>(run: (dir: string, diagramPath: string) => Promise<T>): Promise<T> {
    const dir = mkdtempSync(join(tmpdir(), 'render-illustration-'));
    const diagramPath = join(dir, 'diagram.mmd');
    writeFileSync(diagramPath, 'flowchart TD\n  A --> B');
    return run(dir, diagramPath).finally(() => rmSync(dir, { recursive: true, force: true }));
  }

  // F17: writeFile() to a nonexistent --out directory (the normal first-run shape) must
  // fold into the same clean `render-illustration: ...` error convention every other
  // fallible step in main() already uses — never an uncaught ENOENT stack trace.
  test('a nonexistent --out directory errors out cleanly, no crash (F17)', async () => {
    await withDiagramFile(async (dir, diagramPath) => {
      const errorSpy = spyOn(console, 'error').mockImplementation(() => {});
      const outPath = join(dir, 'does-not-exist-subdir', 'out.html');
      try {
        const exitCode = await main(['--title', 't', '--caption', 'c', '--diagram', diagramPath, '--out', outPath]);
        expect(exitCode).not.toBe(0);
        expect(errorSpy).toHaveBeenCalled();
        expect(String(errorSpy.mock.calls[0]?.[0])).toContain('render-illustration:');
        expect(existsSync(outPath)).toBe(false);
      } finally {
        errorSpy.mockRestore();
      }
    });
  });

  // F18: parseArgs's doc comment promises rejection when a value-taking flag is
  // "immediately followed by another flag" — but the code only checked for
  // `undefined`, so `--title --caption x ...` silently swallowed `--caption` as the
  // title text instead of erroring.
  test('a value flag immediately followed by another flag errors out cleanly (F18)', async () => {
    await withDiagramFile(async (dir, diagramPath) => {
      const errorSpy = spyOn(console, 'error').mockImplementation(() => {});
      const outPath = join(dir, 'out.html');
      try {
        const exitCode = await main([
          '--title', '--caption', 'x', '--diagram', diagramPath, '--out', outPath,
        ]);
        expect(exitCode).not.toBe(0);
        expect(errorSpy).toHaveBeenCalled();
        expect(existsSync(outPath)).toBe(false);
      } finally {
        errorSpy.mockRestore();
      }
    });
  });

  // F18 sanity: a legitimate value that merely starts with `-` but is not itself a
  // recognized flag must still be accepted, not rejected.
  test('a value starting with - that is not a known flag is still accepted (F18)', async () => {
    await withDiagramFile(async (dir, diagramPath) => {
      const outPath = join(dir, 'out.html');
      const logSpy = spyOn(console, 'log').mockImplementation(() => {});
      try {
        const exitCode = await main([
          '--title', '-not-a-flag', '--diagram', diagramPath, '--out', outPath,
        ]);
        expect(exitCode).toBe(0);
        expect(readFileSync(outPath, 'utf8')).toContain('<h1>-not-a-flag</h1>');
      } finally {
        logSpy.mockRestore();
      }
    });
  });
});
