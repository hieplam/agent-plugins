import { describe, expect, spyOn, test } from 'bun:test';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  DESIGN_SYSTEM_DIR, MERMAID_CDN_URL, embedFonts, escapeHtml, fontRefs, loadTheme, main,
  renderIllustrationHtml, renderPage,
} from './render-illustration';
import { extractMermaidSources } from './validate-mermaid';

const DIAGRAM = 'flowchart TD\n  A["shaman (what & why)"] --> B["warchief"]\n  B -.-x C';

// The real design system, loaded the way the CLI loads it: every test below renders
// what a user actually gets, not a stand-in theme.
const THEME = await loadTheme();

describe('renderIllustrationHtml', () => {
  const html = renderIllustrationHtml({
    title: 'Tribe flow', diagram: DIAGRAM, caption: 'Who dispatches whom.',
  }, THEME);

  test('is one self-contained document', () => {
    expect(html.startsWith('<!DOCTYPE html>')).toBe(true);
    expect(html.trimEnd().endsWith('</html>')).toBe(true);
  });

  test('loads mermaid from the CDN and nothing else remote', () => {
    const remote = [...html.matchAll(/https?:\/\/[^\s'")]+/g)].map((m) => m[0]);
    expect(remote).toEqual([MERMAID_CDN_URL]);
  });

  test('carries every bundled font inside the page', () => {
    expect(html).not.toContain("url('fonts/");
    expect(html).toContain('url(data:font/woff2;base64,');
    expect(html).toContain('"Libre Caslon Text"');
    expect(html).toContain('"Reading Vietnamese"');
  });

  test('supports light, dark, and an explicit data-theme override', () => {
    expect(html).toContain('prefers-color-scheme: dark');
    expect(html).toContain(':root[data-theme="dark"]');
  });

  test('renders diagrams through the theme script, which turns mermaid auto-start off first', () => {
    expect(html).toContain('await renderDiagrams(mermaid);');
    const script = html.slice(html.indexOf('<script type="module">'));
    const autoStartOff = script.indexOf('mermaid.initialize({ startOnLoad: false });');
    expect(autoStartOff).toBeGreaterThan(-1);
    expect(autoStartOff).toBeLessThan(script.indexOf('await Promise.race'));
  });

  test('has no fixed page width: the page fills the screen', () => {
    expect(html).not.toMatch(/\.page\s*\{[^}]*max-width/);
  });

  test('escapes the title and caption', () => {
    const evil = renderIllustrationHtml({
      title: '<script>x</script>', diagram: DIAGRAM, caption: 'a & b',
    }, THEME);
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

describe('renderPage', () => {
  const theme = { css: '.page {}', script: 'async function renderDiagrams() {}' };

  test('places the body verbatim after the header, and escapes only the title and lede', () => {
    const html = renderPage({ title: 'A & B', lede: '<i>', body: '<div class="grid">x</div>' }, theme);
    expect(html).toContain('<h1>A &amp; B</h1>\n<p class="lede">&lt;i&gt;</p>');
    expect(html).toContain('</header>\n<div class="grid">x</div>\n</main>');
  });

  test('omits the lede when there is none', () => {
    expect(renderPage({ title: 't', lede: '', body: '' }, theme)).not.toContain('class="lede"');
  });
});

describe('fontRefs and embedFonts', () => {
  const css = "a { src: url('fonts/a.woff2'); } b { src: url('fonts/b.woff2'); } c { src: url('fonts/a.woff2'); }";

  test('lists each referenced font once, in order', () => {
    expect(fontRefs(css)).toEqual(['a.woff2', 'b.woff2']);
  });

  test('a reference that tries to leave fonts/ is not a font reference at all', () => {
    expect(fontRefs("x { src: url('fonts/../../secret.woff2'); }")).toEqual([]);
  });

  test('swaps every reference for a data: URI', () => {
    const out = embedFonts(css, { 'a.woff2': 'QQ==', 'b.woff2': 'Qg==' });
    expect(out).not.toContain("url('fonts/");
    expect(out.match(/data:font\/woff2;base64,QQ==/g)?.length).toBe(2);
  });

  test('refuses a stylesheet that names a font it was not given', () => {
    expect(() => embedFonts(css, { 'a.woff2': 'QQ==' })).toThrow('fonts/b.woff2');
  });

  test('every font the shipped stylesheet names ships beside it', () => {
    const shipped = readFileSync(join(DESIGN_SYSTEM_DIR, 'reading.css'), 'utf8');
    expect(fontRefs(shipped).length).toBeGreaterThan(0);
    for (const name of fontRefs(shipped)) expect(existsSync(join(DESIGN_SYSTEM_DIR, 'fonts', name))).toBe(true);
  });
});

describe('reading.js — diagram sizing', () => {
  // reading.js carries no import or export (the renderer supplies mermaid), so its pure
  // helpers are reached by evaluating the file's text.
  const source = readFileSync(join(DESIGN_SYSTEM_DIR, 'reading.js'), 'utf8');
  const { fitScale, isDarkGround } = new Function(`${source}\nreturn { fitScale, isDarkGround };`)();

  // textScale 1.3625 is the body text at 2560px wide (21.8px) over mermaid's 16px.
  const TEXT = 21.8 / 16;
  const MIN = 14 / 16;

  test('a wide diagram grows to fill the page width', () => {
    // 1293px natural, 2406px of room, plenty of height: 1.86x, the flowchart measured at 2560.
    expect(fitScale({ width: 1293, height: 250 }, { width: 2406, height: 1123 }, 2, TEXT, MIN)).toBeCloseTo(1.861, 2);
  });

  test('a small diagram stops growing at the cap', () => {
    expect(fitScale({ width: 400, height: 100 }, { width: 2400, height: 1100 }, 2, TEXT, MIN)).toBe(2);
  });

  test('a diagram that fits the screen at readable size is held to the screen height', () => {
    expect(fitScale({ width: 1161, height: 700 }, { width: 2400, height: 1123 }, 2, TEXT, MIN)).toBeCloseTo(1.604, 2);
  });

  test('a tall diagram keeps body-size labels and scrolls, rather than shrinking to fit', () => {
    // The 1141x2808 flowchart a fresh session drew: held at 1x it left 16px labels and
    // ~700px of empty paper each side of a 2560px screen.
    expect(fitScale({ width: 1141, height: 2808 }, { width: 2406, height: 1123 }, 2, TEXT, MIN)).toBeCloseTo(TEXT, 3);
  });

  test('a diagram a little wider than the room shrinks to fit it', () => {
    expect(fitScale({ width: 2600, height: 400 }, { width: 2406, height: 1123 }, 2, TEXT, MIN)).toBeCloseTo(0.925, 3);
  });

  test('a far wider diagram stops shrinking at 14px labels and scrolls sideways', () => {
    // The same tribe flow laid out left to right: 4600px natural would shrink to 8px labels.
    expect(fitScale({ width: 4600, height: 780 }, { width: 2406, height: 1123 }, 2, TEXT, MIN)).toBe(MIN);
  });

  test('isDarkGround tells the paper from the night paper', () => {
    expect(isDarkGround('#f6efdf')).toBe(false);
    expect(isDarkGround('#1c1915')).toBe(true);
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

  test('--diagram and --body together is refused', async () => {
    await withDiagramFile(async (dir, diagramPath) => {
      const errorSpy = spyOn(console, 'error').mockImplementation(() => {});
      const outPath = join(dir, 'out.html');
      try {
        const exitCode = await main(['--diagram', diagramPath, '--body', diagramPath, '--out', outPath]);
        expect(exitCode).toBe(1);
        expect(String(errorSpy.mock.calls[0]?.[0])).toContain('not both');
        expect(existsSync(outPath)).toBe(false);
      } finally {
        errorSpy.mockRestore();
      }
    });
  });

  test('a missing design system errors out cleanly and writes nothing', async () => {
    await withDiagramFile(async (dir, diagramPath) => {
      const errorSpy = spyOn(console, 'error').mockImplementation(() => {});
      const outPath = join(dir, 'out.html');
      try {
        const exitCode = await main(['--diagram', diagramPath, '--out', outPath], join(dir, 'no-such-dir'));
        expect(exitCode).toBe(1);
        expect(String(errorSpy.mock.calls[0]?.[0])).toContain('could not read the design system');
        expect(existsSync(outPath)).toBe(false);
      } finally {
        errorSpy.mockRestore();
      }
    });
  });
});

describe('main() CLI — --body, typed the way a person types it', () => {
  test('relative --body and --out paths resolve against the current directory', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'render-body-'));
    const prev = process.cwd();
    const logSpy = spyOn(console, 'log').mockImplementation(() => {});
    try {
      process.chdir(dir);
      writeFileSync('fragment.html', '<div class="grid"><article class="card">x</article></div>');
      const exitCode = await main(['--title', 'Options', '--caption', 'Three ways.', '--body', 'fragment.html', '--out', 'page.html']);
      expect(exitCode).toBe(0);
      const html = readFileSync(join(dir, 'page.html'), 'utf8');
      expect(html).toContain('<p class="lede">Three ways.</p>');
      expect(html).toContain('<article class="card">x</article>');
      expect(html).toContain('url(data:font/woff2;base64,');
    } finally {
      process.chdir(prev);
      logSpy.mockRestore();
      rmSync(dir, { recursive: true, force: true });
    }
  });

  test('the shipped specimen renders, and its diagrams pass the extractor', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'render-specimen-'));
    const logSpy = spyOn(console, 'log').mockImplementation(() => {});
    try {
      const outPath = join(dir, 'specimen-page.html');
      const exitCode = await main(['--title', 'Specimen', '--body', join(DESIGN_SYSTEM_DIR, 'specimen.html'), '--out', outPath]);
      expect(exitCode).toBe(0);
      expect(extractMermaidSources(readFileSync(outPath, 'utf8')).length).toBe(2);
    } finally {
      logSpy.mockRestore();
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
