import { describe, expect, spyOn, test } from 'bun:test';
import { mkdtempSync, mkdirSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { GENERATOR_META_TAG } from './render-illustration';
import {
  EXIT_CODE, bodyHasNonTrivialContent, evaluateAbsent, evaluatePresent, extractHtmlPaths,
  extractThemedBody, hasGeneratorMarker, main, parseArgs, writtenSinceManifest,
} from './check-reply-html';

function sha(text: string): string {
  return createHash('sha256').update(text).digest('hex');
}

// A themed page shaped exactly like render-illustration.ts's own renderPage() output:
// the generator marker in <head>, and the body between </header> and </main>.
function themedPage(body: string): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
${GENERATOR_META_TAG}
<title>t</title>
</head>
<body>
<main class="page">
<header class="page-head">
<h1>t</h1>
</header>
${body}
</main>
</body>
</html>
`;
}

const RICH_BODY = '<figure><div class="grid"><article>cell one</article><article>cell two</article></div>'
  + '<figcaption>caption</figcaption></figure>';
const SHORT_PARAGRAPH_BODY = '<p>Sorry that did not land, here it is again in short.</p>';

describe('parseArgs', () => {
  test('requires --reply, --manifest and --expect', () => {
    expect(parseArgs([]).error).toContain('--reply');
    expect(parseArgs(['--reply', 'r.md']).error).toContain('--manifest');
    expect(parseArgs(['--reply', 'r.md', '--manifest', 'm.json']).error).toContain('--expect');
  });

  test('--expect only accepts present or absent', () => {
    const args = parseArgs(['--reply', 'r.md', '--manifest', 'm.json', '--expect', 'maybe']);
    expect(args.error).toContain('present');
  });

  test('--themed is a bare flag, does not consume the next token', () => {
    const args = parseArgs([
      '--reply', 'r.md', '--manifest', 'm.json', '--expect', 'present', '--themed',
    ]);
    expect(args.themed).toBe(true);
    expect(args.error).toBeNull();
  });

  test('an unknown argument is rejected', () => {
    expect(parseArgs(['--reply', 'r.md', '--manifest', 'm.json', '--expect', 'present', '--bogus']).error)
      .toContain('--bogus');
  });

  test('a value-taking flag with no value errors, not a silent undefined', () => {
    expect(parseArgs(['--reply']).error).toContain('--reply');
  });
});

describe('extractHtmlPaths — the shapes a real reply spells a path (fixtures-mirror-reality)', () => {
  test('an absolute path', () => {
    expect(extractHtmlPaths('See /tmp/session-1/out.html for the page.')).toEqual(['/tmp/session-1/out.html']);
  });

  test('a relative path with a directory', () => {
    expect(extractHtmlPaths('Open session-1/out.html to see it.')).toEqual(['session-1/out.html']);
  });

  test('a bare relative filename', () => {
    expect(extractHtmlPaths('I wrote out.html with the illustration.')).toEqual(['out.html']);
  });

  test('wrapped in backticks and a markdown link, wrapper characters are not part of the path', () => {
    expect(extractHtmlPaths('It is at `out.html`, or see [the page](session/out.html).'))
      .toEqual(['out.html', 'session/out.html']);
  });

  test('no .html mention at all', () => {
    expect(extractHtmlPaths('Here is the answer in plain words.')).toEqual([]);
  });

  test('the same path named twice is reported once', () => {
    expect(extractHtmlPaths('out.html holds it. See out.html again.')).toEqual(['out.html']);
  });
});

describe('writtenSinceManifest', () => {
  const manifest = [{ path: 'out.html', sha256: 'AAA' }];

  test('absent from the manifest — a brand new file — counts as written', () => {
    expect(writtenSinceManifest('new.html', 'ZZZ', manifest)).toBe(true);
  });

  test('present with a changed hash — overwritten this turn — counts as written', () => {
    expect(writtenSinceManifest('out.html', 'BBB', manifest)).toBe(true);
  });

  test('present with the same hash — untouched this turn — does not count as written', () => {
    expect(writtenSinceManifest('out.html', 'AAA', manifest)).toBe(false);
  });

  test('a path outside cwd (relPath null) can never be proven written — fails closed', () => {
    expect(writtenSinceManifest(null, 'ZZZ', manifest)).toBe(false);
  });
});

describe('hasGeneratorMarker / extractThemedBody', () => {
  test('a page render-illustration.ts produced carries the marker', () => {
    expect(hasGeneratorMarker(themedPage(RICH_BODY))).toBe(true);
  });

  test('a hand-written page that merely looks similar does not', () => {
    expect(hasGeneratorMarker('<html><body>' + RICH_BODY + '</body></html>')).toBe(false);
  });

  test('extracts exactly the body between the header and </main>', () => {
    expect(extractThemedBody(themedPage(RICH_BODY))).toBe(RICH_BODY);
  });

  test('a page that is not shaped like renderPage()\'s output has no extractable body', () => {
    expect(extractThemedBody('<html><body>hi</body></html>')).toBeNull();
  });
});

describe('bodyHasNonTrivialContent — the stub floor (A2, threshold documented in check-reply-html.ts)', () => {
  test('an empty body is a stub', () => {
    expect(bodyHasNonTrivialContent('')).toBe(false);
  });

  test('a single short paragraph — the previous prose re-pasted — is a stub', () => {
    expect(bodyHasNonTrivialContent(SHORT_PARAGRAPH_BODY)).toBe(false);
  });

  test('real structure (several elements) with little prose is NOT a stub, whatever form it takes', () => {
    expect(bodyHasNonTrivialContent(RICH_BODY)).toBe(true);
  });

  test('substantial prose with no extra structure is NOT a stub either', () => {
    const longParagraph = '<p>' + Array(45).fill('word').join(' ') + '</p>';
    expect(bodyHasNonTrivialContent(longParagraph)).toBe(true);
  });
});

describe('evaluatePresent — the pure decision', () => {
  const manifest = [{ path: 'stale.html', sha256: sha('stale-content') }];

  test('no candidates at all fails, with a reason', () => {
    const result = evaluatePresent([], manifest, false);
    expect(result.ok).toBe(false);
    expect(result.reasons.length).toBeGreaterThan(0);
  });

  test('a named path that does not exist on disk fails', () => {
    const result = evaluatePresent([
      { token: 'ghost.html', relToCwd: 'ghost.html', exists: false, sha256: null, content: null },
    ], manifest, false);
    expect(result.ok).toBe(false);
  });

  // The Oracle's other stub shape by a different name: a reply naming a path that
  // existed BEFORE this turn (unchanged in the manifest) never counts as "present" —
  // it was not actually produced this turn, so naming it proves nothing.
  test('a named path that exists but was unchanged since the manifest fails', () => {
    const result = evaluatePresent([
      { token: 'stale.html', relToCwd: 'stale.html', exists: true, sha256: sha('stale-content'), content: null },
    ], manifest, false);
    expect(result.ok).toBe(false);
  });

  test('a genuinely new file, not themed, passes', () => {
    const result = evaluatePresent([
      { token: 'new.html', relToCwd: 'new.html', exists: true, sha256: sha('new-content'), content: null },
    ], manifest, false);
    expect(result.ok).toBe(true);
  });

  // RED-proof: the literal bug the Oracle names — "an HTML file named in the reply
  // that was not produced by render-illustration.ts" — must fail --themed.
  test('themed=true: a new file with no generator marker is a BUG, must fail', () => {
    const html = '<html><body>' + RICH_BODY + '</body></html>';
    const result = evaluatePresent([
      { token: 'new.html', relToCwd: 'new.html', exists: true, sha256: sha(html), content: html },
    ], manifest, true);
    expect(result.ok).toBe(false);
  });

  // RED-proof: the Oracle's other literal bug — "a themed page whose body is
  // empty/one short paragraph" — must fail --themed even with the real marker.
  test('themed=true: a marked page with an empty body is a BUG, must fail', () => {
    const html = themedPage('');
    const result = evaluatePresent([
      { token: 'new.html', relToCwd: 'new.html', exists: true, sha256: sha(html), content: html },
    ], manifest, true);
    expect(result.ok).toBe(false);
  });

  test('themed=true: a marked page with one short paragraph is a BUG, must fail', () => {
    const html = themedPage(SHORT_PARAGRAPH_BODY);
    const result = evaluatePresent([
      { token: 'new.html', relToCwd: 'new.html', exists: true, sha256: sha(html), content: html },
    ], manifest, true);
    expect(result.ok).toBe(false);
  });

  test('themed=true: a marked page with real content passes — the empty-implementation floor', () => {
    const html = themedPage(RICH_BODY);
    const result = evaluatePresent([
      { token: 'new.html', relToCwd: 'new.html', exists: true, sha256: sha(html), content: html },
    ], manifest, true);
    expect(result.ok).toBe(true);
  });

  test('one stub candidate and one real candidate: at least one qualifying candidate is enough', () => {
    const stub = themedPage('');
    const real = themedPage(RICH_BODY);
    const result = evaluatePresent([
      { token: 'stub.html', relToCwd: 'stub.html', exists: true, sha256: sha(stub), content: stub },
      { token: 'real.html', relToCwd: 'real.html', exists: true, sha256: sha(real), content: real },
    ], manifest, true);
    expect(result.ok).toBe(true);
  });
});

describe('evaluateAbsent — the pure decision', () => {
  const manifest = [{ path: 'old.html', sha256: sha('old-content') }];

  test('no .html files at all passes', () => {
    expect(evaluateAbsent([], manifest).ok).toBe(true);
  });

  test('an .html file that already existed, unchanged, passes', () => {
    expect(evaluateAbsent([{ relPath: 'old.html', sha256: sha('old-content') }], manifest).ok).toBe(true);
  });

  test('a brand new .html file fails, and is named', () => {
    const result = evaluateAbsent([{ relPath: 'new.html', sha256: sha('x') }], manifest);
    expect(result.ok).toBe(false);
    expect(result.violations).toEqual(['new.html']);
  });

  test('an existing .html file whose content changed this turn fails', () => {
    const result = evaluateAbsent([{ relPath: 'old.html', sha256: sha('edited') }], manifest);
    expect(result.ok).toBe(false);
    expect(result.violations).toEqual(['old.html']);
  });
});

describe('main() CLI — end to end, both path shapes (fixtures-mirror-reality)', () => {
  function withScratch<T>(run: (scratch: string) => Promise<T>): Promise<T> {
    const scratch = mkdtempSync(join(tmpdir(), 'check-reply-html-'));
    return run(scratch).finally(() => rmSync(scratch, { recursive: true, force: true }));
  }

  function writeManifest(dir: string, entries: Array<{ path: string; sha256: string }>): string {
    const manifestPath = join(dir, 'manifest.json');
    writeFileSync(manifestPath, JSON.stringify(entries));
    return manifestPath;
  }

  test('--expect present, absolute path named in the reply, exists and was written this turn', async () => {
    await withScratch(async (scratch) => {
      const html = themedPage(RICH_BODY);
      const outPath = join(scratch, 'out.html');
      writeFileSync(outPath, html);
      const replyPath = join(scratch, 'reply.md');
      writeFileSync(replyPath, `Here it is: ${outPath}`);
      const manifestPath = writeManifest(scratch, []); // out.html absent from the manifest -> new this turn
      const logSpy = spyOn(console, 'log').mockImplementation(() => {});
      try {
        const exitCode = await main([
          '--reply', replyPath, '--manifest', manifestPath, '--expect', 'present', '--themed',
        ], scratch);
        expect(exitCode).toBe(EXIT_CODE.PASS);
      } finally {
        logSpy.mockRestore();
      }
    });
  });

  test('--expect present, relative path (typed the way a person types it) named in the reply', async () => {
    await withScratch(async (scratch) => {
      const html = themedPage(RICH_BODY);
      writeFileSync(join(scratch, 'out.html'), html);
      const replyPath = join(scratch, 'reply.md');
      writeFileSync(replyPath, 'Here it is: out.html');
      const manifestPath = writeManifest(scratch, []);
      const logSpy = spyOn(console, 'log').mockImplementation(() => {});
      try {
        const exitCode = await main([
          '--reply', replyPath, '--manifest', manifestPath, '--expect', 'present', '--themed',
        ], scratch);
        expect(exitCode).toBe(EXIT_CODE.PASS);
      } finally {
        logSpy.mockRestore();
      }
    });
  });

  // RED-proof at the CLI edge, not just the pure core: the literal bug named in the
  // Oracle — a stub HTML file named in the reply, never produced by render-illustration.ts.
  test('--expect present --themed FAILS a stub HTML file (Oracle: this passing is a BUG)', async () => {
    await withScratch(async (scratch) => {
      writeFileSync(join(scratch, 'out.html'), '<html><body>' + RICH_BODY + '</body></html>');
      const replyPath = join(scratch, 'reply.md');
      writeFileSync(replyPath, 'Here it is: out.html');
      const manifestPath = writeManifest(scratch, []);
      const logSpy = spyOn(console, 'log').mockImplementation(() => {});
      try {
        const exitCode = await main([
          '--reply', replyPath, '--manifest', manifestPath, '--expect', 'present', '--themed',
        ], scratch);
        expect(exitCode).toBe(EXIT_CODE.FAIL);
      } finally {
        logSpy.mockRestore();
      }
    });
  });

  test('--expect absent PASSES when the final turn wrote no .html at all', async () => {
    await withScratch(async (scratch) => {
      const replyPath = join(scratch, 'reply.md');
      writeFileSync(replyPath, 'Common implementations use FNV or MurmurHash.');
      const manifestPath = writeManifest(scratch, []);
      const logSpy = spyOn(console, 'log').mockImplementation(() => {});
      try {
        const exitCode = await main(['--reply', replyPath, '--manifest', manifestPath, '--expect', 'absent'], scratch);
        expect(exitCode).toBe(EXIT_CODE.PASS);
      } finally {
        logSpy.mockRestore();
      }
    });
  });

  test('--expect absent FAILS when the final turn wrote a new .html anywhere under cwd', async () => {
    await withScratch(async (scratch) => {
      mkdirSync(join(scratch, 'nested'));
      writeFileSync(join(scratch, 'nested', 'surprise.html'), themedPage(RICH_BODY));
      const replyPath = join(scratch, 'reply.md');
      writeFileSync(replyPath, 'Common implementations use FNV or MurmurHash.');
      const manifestPath = writeManifest(scratch, []);
      const logSpy = spyOn(console, 'log').mockImplementation(() => {});
      try {
        const exitCode = await main(['--reply', replyPath, '--manifest', manifestPath, '--expect', 'absent'], scratch);
        expect(exitCode).toBe(EXIT_CODE.FAIL);
      } finally {
        logSpy.mockRestore();
      }
    });
  });

  test('an unreadable --reply file is CANNOT_RUN, not a crash', async () => {
    await withScratch(async (scratch) => {
      const manifestPath = writeManifest(scratch, []);
      const errorSpy = spyOn(console, 'error').mockImplementation(() => {});
      try {
        const exitCode = await main([
          '--reply', join(scratch, 'does-not-exist.md'), '--manifest', manifestPath, '--expect', 'absent',
        ], scratch);
        expect(exitCode).toBe(EXIT_CODE.CANNOT_RUN);
        expect(String(errorSpy.mock.calls[0]?.[0])).toContain('CANNOT-RUN');
      } finally {
        errorSpy.mockRestore();
      }
    });
  });

  test('a malformed --manifest file is CANNOT_RUN, not a crash', async () => {
    await withScratch(async (scratch) => {
      const replyPath = join(scratch, 'reply.md');
      writeFileSync(replyPath, 'hello');
      const manifestPath = join(scratch, 'manifest.json');
      writeFileSync(manifestPath, 'not valid json {{{');
      const errorSpy = spyOn(console, 'error').mockImplementation(() => {});
      try {
        const exitCode = await main(['--reply', replyPath, '--manifest', manifestPath, '--expect', 'absent'], scratch);
        expect(exitCode).toBe(EXIT_CODE.CANNOT_RUN);
      } finally {
        errorSpy.mockRestore();
      }
    });
  });

  test('a missing required flag is CANNOT_RUN', async () => {
    const errorSpy = spyOn(console, 'error').mockImplementation(() => {});
    try {
      const exitCode = await main(['--reply', 'r.md']);
      expect(exitCode).toBe(EXIT_CODE.CANNOT_RUN);
    } finally {
      errorSpy.mockRestore();
    }
  });
});

// A real, filesystem-level symlinked ANCESTOR directory (fixtures-mirror-reality: not
// mocked) — the shape of macOS's /var -> /private/var. Two directories, `real` and
// `aliased`, name the exact same files on disk under two different absolute spellings.
// This reproduces run-3 of eval-13-reask-gets-html (see task brief): the executor's
// scratch dir was created by Python's tempfile.mkdtemp() under macOS's unresolved
// `/var/folders/...`, but Bun's process.cwd() (what check-reply-html.ts actually sees
// as its cwd once the OS chdir()s into it) reports the kernel-resolved
// `/private/var/folders/...` — so a reply that names its own output file by the
// `/var/...` spelling was wrongly judged to be "outside the scratch tree".
describe('a symlinked ancestor directory (macOS /var vs /private/var shape) — fixtures-mirror-reality', () => {
  async function withSymlinkedAncestor<T>(
    run: (real: string, aliased: string) => Promise<T>,
  ): Promise<T> {
    const base = mkdtempSync(join(tmpdir(), 'check-reply-html-symlink-'));
    const real = join(base, 'real');
    mkdirSync(real);
    const aliased = join(base, 'aliased');
    symlinkSync(real, aliased); // aliased -> real: a real symlinked ancestor, not a mock
    try {
      return await run(realpathSync(real), aliased);
    } finally {
      rmSync(base, { recursive: true, force: true });
    }
  }

  test('--expect present, the reply names its own output through the ALIASED spelling while cwd is the RESOLVED spelling: same file, must still pass', async () => {
    await withSymlinkedAncestor(async (real, aliased) => {
      const html = themedPage(RICH_BODY);
      writeFileSync(join(real, 'out.html'), html);
      const replyPath = join(real, 'reply.md');
      // The model names the file via the symlinked (unresolved) ancestor spelling —
      // exactly the /var/... path a real run-3 reply used.
      writeFileSync(replyPath, `Here it is: ${join(aliased, 'out.html')}`);
      const manifestPath = join(real, 'manifest.json');
      writeFileSync(manifestPath, JSON.stringify([])); // out.html absent from manifest -> new this turn
      const logSpy = spyOn(console, 'log').mockImplementation(() => {});
      try {
        // cwd is the RESOLVED spelling, mirroring what process.cwd() reports once the
        // OS has chdir()'d into a dir reached through a symlinked ancestor.
        const exitCode = await main([
          '--reply', replyPath, '--manifest', manifestPath, '--expect', 'present', '--themed',
        ], real);
        expect(exitCode).toBe(EXIT_CODE.PASS);
      } finally {
        logSpy.mockRestore();
      }
    });
  });

  test('containment still holds: a symlink INSIDE the scratch tree pointing OUTSIDE it is still rejected', async () => {
    await withSymlinkedAncestor(async (real) => {
      const outsideDir = mkdtempSync(join(tmpdir(), 'check-reply-html-outside-'));
      try {
        const html = themedPage(RICH_BODY);
        writeFileSync(join(outsideDir, 'secret.html'), html);
        // A symlink that LIVES inside scratch but points somewhere scratch does not own.
        const escapeLink = join(real, 'escape.html');
        symlinkSync(join(outsideDir, 'secret.html'), escapeLink);
        const replyPath = join(real, 'reply.md');
        writeFileSync(replyPath, `Here it is: ${escapeLink}`);
        const manifestPath = join(real, 'manifest.json');
        writeFileSync(manifestPath, JSON.stringify([]));
        const logSpy = spyOn(console, 'log').mockImplementation(() => {});
        try {
          const exitCode = await main([
            '--reply', replyPath, '--manifest', manifestPath, '--expect', 'present', '--themed',
          ], real);
          expect(exitCode).toBe(EXIT_CODE.FAIL);
        } finally {
          logSpy.mockRestore();
        }
      } finally {
        rmSync(outsideDir, { recursive: true, force: true });
      }
    });
  });

  test('--expect absent is unaffected: the scan globs cwd directly and never compares two spellings of the reply', async () => {
    await withSymlinkedAncestor(async (real, aliased) => {
      mkdirSync(join(real, 'nested'));
      writeFileSync(join(real, 'nested', 'surprise.html'), themedPage(RICH_BODY));
      const replyPath = join(real, 'reply.md');
      writeFileSync(replyPath, 'Common implementations use FNV or MurmurHash.');
      const manifestPath = join(real, 'manifest.json');
      writeFileSync(manifestPath, JSON.stringify([]));
      const logSpy = spyOn(console, 'log').mockImplementation(() => {});
      try {
        const viaReal = await main(['--reply', replyPath, '--manifest', manifestPath, '--expect', 'absent'], real);
        const viaAliased = await main(['--reply', replyPath, '--manifest', manifestPath, '--expect', 'absent'], aliased);
        expect(viaReal).toBe(EXIT_CODE.FAIL);
        expect(viaAliased).toBe(EXIT_CODE.FAIL);
      } finally {
        logSpy.mockRestore();
      }
    });
  });
});
