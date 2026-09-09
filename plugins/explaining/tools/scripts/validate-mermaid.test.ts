import { describe, expect, spyOn, test } from 'bun:test';
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  classifyOutcome,
  decodeHtmlEntities,
  EXIT_CODE,
  extractMermaidSources,
  hintFor,
  killStalledInstall,
  main,
  raceExitAgainstTimeout,
  validateSources,
} from './validate-mermaid';

describe('extractMermaidSources', () => {
  test('pulls the diagram out of a div.mermaid and decodes entities', () => {
    const html = '<html><body><div class="mermaid">flowchart TD\n  A[&quot;a &amp; b&quot;] --&gt; B</div></body></html>';
    expect(extractMermaidSources(html)).toEqual(['flowchart TD\n  A["a & b"] --> B']);
  });

  test('accepts pre.mermaid and extra classes', () => {
    expect(extractMermaidSources('<pre class="diagram mermaid">graph LR\n A-->B</pre>'))
      .toEqual(['graph LR\n A-->B']);
  });

  test('returns nothing when the page carries no mermaid container', () => {
    expect(extractMermaidSources('<html><body><p>just prose</p></body></html>')).toEqual([]);
  });

  test('decodes &amp; last so &amp;lt; does not become a tag', () => {
    expect(decodeHtmlEntities('&amp;lt;')).toBe('&lt;');
  });

  // F10: a legal single-quoted class attribute must be recognized, not silently dropped.
  test('accepts a single-quoted class attribute (F10)', () => {
    expect(extractMermaidSources("<div class='mermaid'>flowchart TD\n A-->B</div>"))
      .toEqual(['flowchart TD\n A-->B']);
  });

  // F11: a fake `class="..."` substring inside an unrelated attribute value must not
  // beat the real `class="mermaid"` attribute — extraction must be attribute-aware.
  test('the real class attribute wins over a fake class="..." inside another attribute (F11)', () => {
    const html = '<div class="mermaid" note=\'see class="other" here\'>flowchart TD\n A-->B</div>';
    expect(extractMermaidSources(html)).toEqual(['flowchart TD\n A-->B']);
  });

  // F13: a nested same-tag element inside the mermaid container must not truncate
  // extraction at the first inner closing tag. Guarantee: for well-formed HTML (every
  // opening tag has a matching closing tag), depth-tracked scanning finds the TRUE
  // matching closing tag of the container, so nothing after a nested inner element is
  // dropped.
  test('a nested same-tag element does not truncate extraction (F13)', () => {
    const html = '<div class="mermaid">flowchart TD\n A --> B\n <div>note</div>\n C --> D</div>';
    expect(extractMermaidSources(html))
      .toEqual(['flowchart TD\n A --> B\n <div>note</div>\n C --> D']);
  });

  // FB2: a `>` inside a quoted attribute value on the SAME container element must not
  // truncate the open-tag scan early — the scan must be attribute-value-aware.
  test('a ">" inside a double-quoted attribute does not truncate the open tag (FB2)', () => {
    const html = '<div class="mermaid" title="a>b">graph TD; A-->B;</div>';
    expect(extractMermaidSources(html)).toEqual(['graph TD; A-->B;']);
  });

  test('a ">" inside a single-quoted attribute does not truncate the open tag (FB2)', () => {
    const html = "<div class=\"mermaid\" title='a>b'>graph TD; A-->B;</div>";
    expect(extractMermaidSources(html)).toEqual(['graph TD; A-->B;']);
  });

  // F28: a nested tag's quoted attribute value containing a `</div>`-shaped substring
  // must not be mistaken by findMatchingClose's tag scan for a real closing tag — the
  // scan must be attribute-value-aware, the same way the open-tag scan already is (FB2).
  test('a nested tag carrying a `</div>`-shaped quoted attribute value does not truncate the close scan (F28)', () => {
    const html = '<div class="mermaid">graph TD; A-->B;' +
      '<span title="fake </div> inside">x</span></div>';
    expect(extractMermaidSources(html))
      .toEqual(['graph TD; A-->B;<span title="fake </div> inside">x</span>']);
  });
});

describe('hintFor', () => {
  test('maps an unquoted paren label to the quoting rule', () => {
    expect(hintFor("Parse error on line 2:\nExpecting 'SQE', got 'PS'")).toMatch(/wrap .* in double quotes/i);
  });

  test('maps a pipe in a label to the quoting rule', () => {
    expect(hintFor("Expecting 'SQE', got 'PIPE'")).toMatch(/wrap .* in double quotes/i);
  });

  test('maps an unrecognized leading slash to the quoting rule', () => {
    expect(hintFor('Lexical error on line 2. Unrecognized text.')).toMatch(/double quotes/i);
  });

  test('maps an invented diagram type to the diagram-type rule', () => {
    expect(hintFor('No diagram type detected matching given configuration for text: sparklegraph LR'))
      .toMatch(/diagram type/i);
  });

  test('maps an abbreviated dotted link to the full-form rule', () => {
    expect(hintFor("Expecting 'LINK', 'UNICODE_TEXT', 'EDGE_TEXT', got '1'")).toMatch(/-\.-x/);
  });

  test('always returns actionable advice, even for an unknown error', () => {
    expect(hintFor('something nobody has seen before').length).toBeGreaterThan(20);
  });
});

describe('classifyOutcome and its exit codes', () => {
  test('no artifact found is INVALID, not COULD_NOT_VALIDATE', () => {
    expect(classifyOutcome({ artifacts: 0, sources: 0, parser: 'ready', errors: [] }))
      .toBe('INVALID');
  });

  test('artifact without a mermaid container is INVALID', () => {
    expect(classifyOutcome({ artifacts: 1, sources: 0, parser: 'ready', errors: [] }))
      .toBe('INVALID');
  });

  test('an unavailable parser is COULD_NOT_VALIDATE, never INVALID', () => {
    expect(classifyOutcome({ artifacts: 1, sources: 1, parser: 'unavailable', errors: [] }))
      .toBe('COULD_NOT_VALIDATE');
  });

  test('a parse error is INVALID', () => {
    expect(classifyOutcome({ artifacts: 1, sources: 1, parser: 'ready', errors: ['boom'] }))
      .toBe('INVALID');
  });

  test('everything parsed is VALID', () => {
    expect(classifyOutcome({ artifacts: 1, sources: 1, parser: 'ready', errors: [] }))
      .toBe('VALID');
  });

  test('exit codes are 0 valid, 1 invalid, 2 could-not-validate', () => {
    expect(EXIT_CODE).toEqual({ VALID: 0, INVALID: 1, COULD_NOT_VALIDATE: 2 });
  });

  // F12: no artifact must be INVALID even when the parser is unavailable — an absent
  // deliverable is the agent's failure and must never be excused as UNGRADED.
  test('no artifact is INVALID even when the parser is unavailable (F12)', () => {
    expect(classifyOutcome({ artifacts: 0, sources: 0, parser: 'unavailable', errors: [] }))
      .toBe('INVALID');
  });

  // F12 guard: an artifact that DOES exist but cannot be checked must still be
  // COULD_NOT_VALIDATE — this must not regress while fixing the no-artifact case above.
  test('an existing artifact with an unavailable parser is still COULD_NOT_VALIDATE (F12 guard)', () => {
    expect(classifyOutcome({ artifacts: 1, sources: 1, parser: 'unavailable', errors: [] }))
      .toBe('COULD_NOT_VALIDATE');
  });
});

describe('main() CLI — clean verdicts on bad input (F14)', () => {
  test('a nonexistent --file folds into a clean INVALID verdict, not a crash', async () => {
    const errorSpy = spyOn(console, 'error').mockImplementation(() => {});
    const logSpy = spyOn(console, 'log').mockImplementation(() => {});
    try {
      const exitCode = await main(['--file', '/tmp/validate-mermaid-does-not-exist-12345.html']);
      expect(exitCode).toBe(EXIT_CODE.INVALID);
    } finally {
      errorSpy.mockRestore();
      logSpy.mockRestore();
    }
  });

  test('--file with no following value errors out cleanly with a nonzero exit', async () => {
    const errorSpy = spyOn(console, 'error').mockImplementation(() => {});
    try {
      const exitCode = await main(['--file']);
      expect(exitCode).not.toBe(0);
      expect(errorSpy).toHaveBeenCalled();
    } finally {
      errorSpy.mockRestore();
    }
  });

  test('--html-glob with no following value errors out cleanly with a nonzero exit', async () => {
    const errorSpy = spyOn(console, 'error').mockImplementation(() => {});
    try {
      const exitCode = await main(['--html-glob']);
      expect(exitCode).not.toBe(0);
      expect(errorSpy).toHaveBeenCalled();
    } finally {
      errorSpy.mockRestore();
    }
  });
});

// S1: --help/-h previously fell through to the default `*.html` glob and silently
// scanned the current working directory instead of printing usage. Proven here by
// running from a temp cwd that DOES contain a valid mermaid artifact and asserting the
// output is usage text, not a "VALID: N diagram(s) found" verdict line — a verdict line
// would mean the filesystem was scanned despite --help.
describe('main() --help/-h prints usage without touching the filesystem (S1)', () => {
  const HTML = '<!DOCTYPE html><html><body><div class="mermaid">flowchart TD\n' +
    '  A --> B</div></body></html>';

  function withArtifactCwd<T>(run: (dir: string) => Promise<T>): Promise<T> {
    const dir = mkdtempSync(join(tmpdir(), 'validate-mermaid-s1-'));
    writeFileSync(join(dir, 'out.html'), HTML);
    return run(dir).finally(() => rmSync(dir, { recursive: true, force: true }));
  }

  test('--help prints usage and exits VALID, not a scan verdict', async () => {
    await withArtifactCwd(async (dir) => {
      const logSpy = spyOn(console, 'log').mockImplementation(() => {});
      const originalCwd = process.cwd();
      process.chdir(dir);
      try {
        const exitCode = await main(['--help']);
        expect(exitCode).toBe(EXIT_CODE.VALID);
        const loggedText = logSpy.mock.calls.map((call) => String(call[0])).join('\n');
        expect(loggedText).toMatch(/Usage:/);
        expect(loggedText).not.toMatch(/diagram\(s\) found/);
      } finally {
        process.chdir(originalCwd);
        logSpy.mockRestore();
      }
    });
  });

  test('-h prints usage and exits VALID, not a scan verdict', async () => {
    await withArtifactCwd(async (dir) => {
      const logSpy = spyOn(console, 'log').mockImplementation(() => {});
      const originalCwd = process.cwd();
      process.chdir(dir);
      try {
        const exitCode = await main(['-h']);
        expect(exitCode).toBe(EXIT_CODE.VALID);
        const loggedText = logSpy.mock.calls.map((call) => String(call[0])).join('\n');
        expect(loggedText).toMatch(/Usage:/);
        expect(loggedText).not.toMatch(/diagram\(s\) found/);
      } finally {
        process.chdir(originalCwd);
        logSpy.mockRestore();
      }
    });
  });
});

// F15: --html-glob is fed straight into Bun's Glob.scan(), which walks the filesystem
// relative to cwd — so an absolute-path pattern never matches and the CLI falsely
// reports "0 diagrams found" (INVALID) for a real, valid artifact.
describe('--html-glob finds artifacts regardless of pattern form (F15)', () => {
  const HTML = '<!DOCTYPE html><html><body><div class="mermaid">flowchart TD\n' +
    '  A --> B</div></body></html>';

  function withArtifact<T>(run: (dir: string) => Promise<T>): Promise<T> {
    const dir = mkdtempSync(join(tmpdir(), 'validate-mermaid-f15-'));
    mkdirSync(join(dir, 'sub'));
    writeFileSync(join(dir, 'out.html'), HTML);
    writeFileSync(join(dir, 'sub', 'out.html'), HTML);
    return run(dir).finally(() => rmSync(dir, { recursive: true, force: true }));
  }

  async function runMain(cwd: string, argv: string[]): Promise<number> {
    const logSpy = spyOn(console, 'log').mockImplementation(() => {});
    const originalCwd = process.cwd();
    process.chdir(cwd);
    try {
      return await main(argv);
    } finally {
      process.chdir(originalCwd);
      logSpy.mockRestore();
    }
  }

  test('form 1: an absolute path to a specific file', async () => {
    await withArtifact(async (dir) => {
      const exitCode = await runMain(dir, ['--html-glob', join(dir, 'out.html')]);
      expect(exitCode).toBe(EXIT_CODE.VALID);
    });
  });

  test('form 2: an absolute glob with a wildcard', async () => {
    await withArtifact(async (dir) => {
      const exitCode = await runMain(dir, ['--html-glob', join(dir, '*.html')]);
      expect(exitCode).toBe(EXIT_CODE.VALID);
    });
  });

  test('form 3: a relative path with a directory component', async () => {
    await withArtifact(async (dir) => {
      const exitCode = await runMain(dir, ['--html-glob', 'sub/out.html']);
      expect(exitCode).toBe(EXIT_CODE.VALID);
    });
  });

  test('form 4: a bare relative filename (must still work)', async () => {
    await withArtifact(async (dir) => {
      const exitCode = await runMain(dir, ['--html-glob', 'out.html']);
      expect(exitCode).toBe(EXIT_CODE.VALID);
    });
  });

  test('form 5: a relative wildcard, the eval harness usage (must still work)', async () => {
    await withArtifact(async (dir) => {
      const exitCode = await runMain(dir, ['--html-glob', '*.html']);
      expect(exitCode).toBe(EXIT_CODE.VALID);
    });
  });

  test('an absolute pattern that matches no file is still the no-artifact INVALID path', async () => {
    await withArtifact(async (dir) => {
      const exitCode = await runMain(dir, ['--html-glob', join(dir, 'nope-*.html')]);
      expect(exitCode).toBe(EXIT_CODE.INVALID);
    });
  });

  // F16: form 3 above (join(dir, '*.html')) only exercises a wildcard in the FINAL path
  // component. A wildcard in a DIRECTORY component (e.g. /abs/*/out.html) must also
  // resolve — a prior fix (scanCwd = dirname(htmlGlob)) only stripped the basename, so
  // this form crashed with an uncaught ENOENT rather than matching.
  test('form 6: wildcard in a directory component of an absolute pattern (F16)', async () => {
    await withArtifact(async (dir) => {
      const exitCode = await runMain(dir, ['--html-glob', join(dir, '*', 'out.html')]);
      expect(exitCode).toBe(EXIT_CODE.VALID);
    });
  });

  // F16: an absolute pattern whose DIRECTORY does not exist must fold into the ordinary
  // no-artifact INVALID path (clean exit 1), never an uncaught ENOENT crash. This is the
  // normal shape of a first-run --out path that has not been created yet.
  test('an absolute pattern whose directory does not exist is a clean INVALID, not a crash (F16)', async () => {
    await withArtifact(async (dir) => {
      const exitCode = await runMain(dir, [
        '--html-glob',
        join(dir, 'nonexistent-dir-xyz', 'out.html'),
      ]);
      expect(exitCode).toBe(EXIT_CODE.INVALID);
    });
  });

  // F16: the same missing-directory shape but with a wildcard in the missing directory
  // component (/abs/*/out.html where /abs itself does not exist) — the OTHER crash
  // trigger the prior fix introduced.
  test('a wildcard pattern whose root directory does not exist is a clean INVALID, not a crash (F16)', async () => {
    const exitCode = await runMain(process.cwd(), [
      '--html-glob',
      join('/tmp', `validate-mermaid-f16-missing-root-${Date.now()}`, '*', 'out.html'),
    ]);
    expect(exitCode).toBe(EXIT_CODE.INVALID);
  });
});

// FB3: an on-demand `bun install` that stalls (a dead/slow network) must degrade to the
// documented could-not-validate path instead of hanging the process forever. The bound
// is expressed as a pure race between the spawned process's `exited` promise and a
// timeout, so a test can inject a never-resolving promise and a tiny timeout to prove
// the timeout branch deterministically, without waiting out a real multi-minute stall.
describe('raceExitAgainstTimeout (FB3)', () => {
  test('resolves with the real exit code when the process finishes before the timeout', async () => {
    const result = await raceExitAgainstTimeout(Promise.resolve(0), 5_000);
    expect(result).toEqual({ kind: 'exited', code: 0 });
  });

  test('resolves timeout when the process never finishes within the bound (FB3)', async () => {
    const neverResolves = new Promise<number>(() => {}); // simulates a stalled install
    const result = await raceExitAgainstTimeout(neverResolves, 10);
    expect(result).toEqual({ kind: 'timeout' });
  });

  // F30: when `exited` wins the race, the timeout's setTimeout handle must be cleared —
  // otherwise it leaks a pending timer for the full `timeoutMs` duration on every winning
  // exit (masked today only because the real CLI calls process.exit() immediately after).
  test('clears the timeout timer when the process exits before the timeout (F30)', async () => {
    const clearSpy = spyOn(globalThis, 'clearTimeout');
    try {
      const result = await raceExitAgainstTimeout(Promise.resolve(0), 5_000);
      expect(result).toEqual({ kind: 'exited', code: 0 });
      expect(clearSpy).toHaveBeenCalled();
    } finally {
      clearSpy.mockRestore();
    }
  });
});

// FA1: the F31 "fix" above was a fire-and-forget async grace-period escalation, awaited
// nowhere, immediately followed by `loadParserUncached` returning and the CLI's
// top-level `process.exit()` running milliseconds later — which tears down the event
// loop and cancels the pending grace-period timer before SIGKILL could ever be sent,
// leaving a SIGTERM-ignoring stalled child orphaned exactly as before. killStalledInstall
// replaces it with a direct, synchronous, unignorable SIGKILL that cannot be raced
// against process.exit — proven here by asserting the signal is sent with no `await` in
// between call and assertion (a promise-returning escalation could not make that
// assertion pass).
describe('killStalledInstall sends SIGKILL directly and synchronously (FA1)', () => {
  test('SIGKILL is sent synchronously, before any further event-loop turn', () => {
    const signalsSent: string[] = [];
    killStalledInstall((signal) => signalsSent.push(signal));
    // No `await` above: if this assertion passes, the signal was already sent by the
    // time killStalledInstall() returned — there is no pending timer or promise for a
    // subsequent process.exit() to race against and cancel.
    expect(signalsSent).toEqual(['SIGKILL']);
  });
});

describe('validateSources against the real parser', () => {
  test('the safe-syntax forms the skill mandates all parse', async () => {
    const errors = await validateSources([
      'flowchart TD\n  A["do (this)"] --> B[end]',
      'flowchart TD\n  A -.-x B\n  A -.-o C',
      'flowchart TD\n  A["say #quot;hi#quot;"] --> B',
      'sequenceDiagram\n  participant A\n  participant B\n  loop every minute\n    A--xB: ping\n  end',
    ]);
    expect(errors).toEqual([]);
  });

  test('the unsafe counterparts are rejected by a real parse', async () => {
    const errors = await validateSources([
      'flowchart TD\n  A[do (this)] --> B[end]',
      'sparklegraph LR\n  A --> B',
    ]);
    expect(errors.length).toBe(2);
  });
});
