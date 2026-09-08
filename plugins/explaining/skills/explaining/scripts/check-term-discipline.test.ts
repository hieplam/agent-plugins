import { describe, expect, spyOn, test } from 'bun:test';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  AFTER_WINDOW,
  EXIT_CODE,
  classifyTerm,
  evaluateReply,
  findTerm,
  formatReport,
  hasAfterCue,
  hasBeforeCue,
  isParentheticalExpansion,
  main,
  parseArgs,
  splitParagraphs,
  splitSentences,
  stripFences,
  termPattern,
} from './check-term-discipline';

// The reply a baseline `claude -p --safe-mode` gave to the seam case ("Why does adding
// an index to a heavily-written table sometimes make the whole system slower?"),
// 2026-09-08, verbatim. The LLM grader passed it as "contextualized in place". It is
// the shape a real caller produces, and the shape this check exists to refuse.
const BASELINE_REPLY = `Every write now has to maintain the index too, and that cost lands on the hottest path in the system.

**Direct write amplification**
- Each \`INSERT\`/\`DELETE\` touches one more B-tree; each \`UPDATE\` touches it if the indexed column changed (or always, in MVCC engines like Postgres that can't do a HOT update when an index covers the changed column).
- Index maintenance is random I/O into the tree's leaf pages, versus the mostly-sequential append of the WAL/heap. On a table with 8 indexes, the index work dominates the write.
- More WAL/redo per transaction — including full-page images after a checkpoint — which slows commits, replication lag, and backups.

**Contention, which is where "the whole system" gets slower**
- Hot leaf pages become a lock/latch bottleneck. A monotonically increasing key (timestamp, auto-increment ID) means every concurrent inserter fights over the same rightmost page. This is the classic case where one index serializes an otherwise parallel workload.
- Buffer pool pressure: index pages evict data pages that other queries depended on, so unrelated read queries get slower.
- Checkpoint/vacuum load rises. In Postgres, more index churn means more dead tuples to vacuum and more index bloat, and autovacuum competes for the same I/O.

**Second-order effects**
- The planner may now choose the new index for a query where a seq scan or a different index was better, especially if statistics are stale or the index is unselective — a regression that looks unrelated to the write path.
- Building it online still costs a lot of I/O and, in some engines, takes locks that stall writers during creation.

**When it's most likely to backfire:** low-selectivity column (an index on a boolean or a status field with 3 values rarely helps reads but always costs writes), a table already carrying many indexes, a monotonic key under high insert concurrency, or a redundant index whose prefix is already covered by an existing composite index.

**What to check before adding one:** is the column selective enough that the planner will actually use it; is it redundant with an existing index's leading columns; what's the write:read ratio on the table; and can you drop an unused index (\`pg_stat_user_indexes.idx_scan = 0\`) to pay for the new one.`;

// The same content with every listed term introduced at first use — what B2 asks for.
const DISCIPLINED_REPLY = `Every write now has to maintain the index too, and that cost lands on the hottest path in the system.

**Direct write amplification** — the extra bytes a single logical write turns into once every index is updated as well.
- Each \`INSERT\`/\`DELETE\` touches one more B-tree (the sorted, page-organised tree every index is stored as). Under MVCC, the multi-version scheme where an UPDATE writes a new row version instead of overwriting, Postgres can skip the indexes only for a HOT update — a heap-only-tuple update, where no indexed column changed and the new version fits on the same page.
- Index maintenance is random I/O into the tree's leaf pages, versus the mostly-sequential append of the write-ahead log (WAL), the journal every change is recorded in before the data pages are touched.
- A page split, which happens when a leaf page is full and must be cut in two, writes several pages, and after a checkpoint (the moment dirty pages are flushed and the WAL can be trimmed) the first touch of each page copies the whole page into the WAL.

**Contention, which is where "the whole system" gets slower**
- Hot leaf pages become a bottleneck on the latch, the short-lived lock protecting one in-memory page. A monotonically increasing key means every concurrent inserter fights over the same rightmost page.
- The buffer pool — the fixed-size cache of pages the database keeps in RAM — now holds index pages that evict data pages other queries depended on.
- Vacuum, the background job that reclaims dead tuples (row versions no transaction can see any more), has more to do, and index bloat, i.e. space held by index pages that are mostly empty, grows.

**Second-order effects**
- The planner, the component that picks how a query runs, may now choose the new index where a seq scan (reading the whole table in order) was better.`;

const INDEX_TERMS = ['B-tree', 'MVCC', 'HOT', 'WAL', 'checkpoint', 'page split', 'latch',
  'buffer pool', 'vacuum', 'dead tuple', 'bloat', 'planner', 'seq scan'];

describe('stripFences', () => {
  test('removes fenced code so a term inside code is not a prose use', () => {
    const text = 'Prose here.\n```sql\nSELECT * FROM pg_stat_wal;\n```\nMore prose.';
    expect(stripFences(text)).not.toContain('pg_stat_wal');
    expect(stripFences(text)).toContain('More prose.');
  });
});

describe('splitParagraphs / splitSentences', () => {
  test('glues a heading onto the paragraph that follows it', () => {
    const paragraphs = splitParagraphs('## Write amplification\nEvery write touches every index.');
    expect(paragraphs).toEqual(['Write amplification Every write touches every index.']);
  });

  test('glues a bold lead-in line onto the paragraph that follows it', () => {
    const paragraphs = splitParagraphs('**Buffer pool**\n\nThe cache of pages in RAM.');
    expect(paragraphs).toEqual(['Buffer pool The cache of pages in RAM.']);
  });

  test('joins wrapped lines of one paragraph so a cue on the next line still counts', () => {
    const sentences = splitSentences('The write-ahead log\nis the journal every change hits first.');
    expect(sentences).toEqual(['The write-ahead log is the journal every change hits first.']);
  });

  test('each bullet is its own paragraph', () => {
    const paragraphs = splitParagraphs('- one thing\n- another thing');
    expect(paragraphs).toEqual(['one thing', 'another thing']);
  });

  test('splits on terminal punctuation only when a new sentence visibly starts', () => {
    const sentences = splitSentences('It uses e.g. the WAL. Then it commits. "Quoted" next.');
    expect(sentences).toEqual(['It uses e.g. the WAL.', 'Then it commits.', '"Quoted" next.']);
  });
});

describe('termPattern / findTerm', () => {
  test('matches whole words only', () => {
    expect(termPattern('WAL').test('a walk in the park')).toBe(false);
    expect(termPattern('WAL').test('the WAL/heap')).toBe(true);
  });

  test('an all-caps term is case-sensitive so HOT never matches hot pages', () => {
    expect(termPattern('HOT').test('hot leaf pages')).toBe(false);
    expect(termPattern('HOT').test('a HOT update')).toBe(true);
  });

  test('a lower-case term is case-insensitive', () => {
    expect(termPattern('buffer pool').test('Buffer pool pressure')).toBe(true);
  });

  test('multi-word terms accept a hyphen or any whitespace between words, and a plural', () => {
    expect(termPattern('page split').test('two page-splits later')).toBe(true);
    expect(termPattern('dead tuple').test('more dead tuples')).toBe(true);
  });

  test('findTerm returns the first sentence that uses the term', () => {
    const hit = findTerm(['No term here.', 'The planner picks.', 'The planner again.'], 'planner');
    expect(hit).toEqual({ index: 1, start: 4, end: 11 });
  });
});

describe('hasAfterCue', () => {
  test('a colon directly after the term, or a dash within one word, is a cue', () => {
    expect(hasAfterCue(': the cache of pages')).toBe(true);
    expect(hasAfterCue(' — the cache of pages')).toBe(true);
    expect(hasAfterCue(' - the cache of pages')).toBe(true);
    expect(hasAfterCue(' update — a heap-only-tuple update')).toBe(true);
    expect(hasAfterCue(' pressure grows — index pages evict')).toBe(false);
  });

  test('a colon one word later is NOT a cue (the bold-lead "Buffer pool pressure:" shape)', () => {
    expect(hasAfterCue(' pressure: index pages evict')).toBe(false);
  });

  test('an opening parenthesis within one word is a cue', () => {
    expect(hasAfterCue(' (write-ahead log) is')).toBe(true);
    expect(hasAfterCue(' update (heap-only tuple)')).toBe(true);
    expect(hasAfterCue(' engines like Postgres (usually)')).toBe(false);
  });

  test('an appositive comma is a cue', () => {
    expect(hasAfterCue(', which happens when a page is full')).toBe(true);
    expect(hasAfterCue(', the background job that')).toBe(true);
    expect(hasAfterCue(', i.e. space held by')).toBe(true);
  });

  test('a copula within the window is a cue; "this"/"his" never trip the " is " cue', () => {
    expect(hasAfterCue(' is random I/O into')).toBe(true);
    expect(hasAfterCue(' in Postgres is a file')).toBe(true);
    expect(hasAfterCue(' in this case with his')).toBe(false);
    expect(hasAfterCue(` ${'w '.repeat(AFTER_WINDOW + 1)}is late`)).toBe(false);
  });

  test('a knob verb right after a setting name says what it does', () => {
    expect(hasAfterCue('` caps redelivery.')).toBe(true);
    expect(hasAfterCue(' controls how long the server waits')).toBe(true);
    expect(hasAfterCue(' pressure caps nothing')).toBe(false);
  });

  test('closing decoration of the term itself is skipped before looking', () => {
    expect(hasAfterCue('` is the catalog view')).toBe(true);
    expect(hasAfterCue('** — the fixed cache')).toBe(true);
    expect(hasAfterCue('" means the row')).toBe(true);
  });

  test('an extra cue word from another language works as a copula', () => {
    expect(hasAfterCue(' là vòng tròn các giá trị băm', ['là'])).toBe(true);
    expect(hasAfterCue(' trong Redis', ['là'])).toBe(false);
  });

  test('a term used bare mid-sentence has no cue', () => {
    expect(hasAfterCue(' engines like Postgres that cannot do')).toBe(false);
    expect(hasAfterCue('/heap. On a table')).toBe(false);
  });
});

describe('hasBeforeCue', () => {
  test('"called", "known as", "so-called" right before the term', () => {
    expect(hasBeforeCue('This is called ')).toBe(true);
    expect(hasBeforeCue('a structure known as the ')).toBe(true);
    expect(hasBeforeCue('the so-called "')).toBe(true);
  });

  test('an opening backtick or article between cue and term is ignored', () => {
    expect(hasBeforeCue('known as a `')).toBe(true);
  });

  test('a before-cue too far back does not count', () => {
    expect(hasBeforeCue('it is called that only when the very busy ')).toBe(false);
  });

  test('an extra cue works on the before side too', () => {
    expect(hasBeforeCue('được gọi là ', ['gọi là'])).toBe(true);
  });
});

describe('isParentheticalExpansion', () => {
  test('recognises "write-ahead log (WAL)"', () => {
    expect(isParentheticalExpansion('the write-ahead log (', ')')).toBe(true);
    expect(isParentheticalExpansion('the write-ahead log (`', '`)')).toBe(true);
  });

  test('a parenthesis with nothing before it, or more inside it, is not an expansion', () => {
    expect(isParentheticalExpansion('(', ')')).toBe(false);
    expect(isParentheticalExpansion('the log (', ', see below)')).toBe(false);
  });
});

describe('classifyTerm', () => {
  test('UNUSED when the reply never says the term', () => {
    expect(classifyTerm(['No jargon at all.'], 'fill factor').status).toBe('UNUSED');
  });

  test('DEFINED through each cue family', () => {
    expect(classifyTerm(['The buffer pool — the cache of pages in RAM — is fixed.'], 'buffer pool').status).toBe('DEFINED');
    expect(classifyTerm(['It writes the write-ahead log (WAL) first.'], 'WAL').status).toBe('DEFINED');
    expect(classifyTerm(['The result is called bloat.'], 'bloat').status).toBe('DEFINED');
    expect(classifyTerm(['Vacuum is the background job that reclaims space.'], 'vacuum').status).toBe('DEFINED');
    expect(classifyTerm(['Nó dùng hash ring là vòng tròn giá trị băm.'], 'hash ring', ['là']).status).toBe('DEFINED');
  });

  test('UNDEFINED when the first use is bare, even if a later use is defined', () => {
    const verdict = classifyTerm(['Vacuum competes for I/O.', 'Vacuum is the job that reclaims space.'], 'vacuum');
    expect(verdict.status).toBe('UNDEFINED');
    expect(verdict.sentence).toBe('Vacuum competes for I/O.');
  });

  test('a heading term is judged with the sentence that follows the heading', () => {
    const sentences = splitSentences('## Write amplification\nIt is the extra bytes one write becomes.');
    expect(classifyTerm(sentences, 'write amplification').status).toBe('DEFINED');
  });
});

describe('evaluateReply on the real replies', () => {
  test('the baseline reply that the LLM grader passed is refused', () => {
    const evaluation = evaluateReply(BASELINE_REPLY, INDEX_TERMS);
    const undefinedTerms = evaluation.verdicts.filter((v) => v.status === 'UNDEFINED').map((v) => v.term);
    for (const term of ['MVCC', 'HOT', 'WAL', 'latch', 'vacuum', 'dead tuple', 'bloat', 'planner', 'seq scan']) {
      expect(undefinedTerms).toContain(term);
    }
    expect(evaluation.undefined).toBeGreaterThanOrEqual(9);
  });

  test('the disciplined rewrite is accepted with zero undefined terms', () => {
    const evaluation = evaluateReply(DISCIPLINED_REPLY, INDEX_TERMS);
    const undefinedTerms = evaluation.verdicts.filter((v) => v.status === 'UNDEFINED').map((v) => v.term);
    expect(undefinedTerms).toEqual([]);
    expect(evaluation.used).toBe(INDEX_TERMS.length);
  });

  test('a term that only appears inside a code fence is UNUSED', () => {
    const reply = 'Run this:\n```sql\nSELECT * FROM pg_stat_wal;\n```\nDone.';
    expect(evaluateReply(reply, ['pg_stat_wal']).verdicts[0].status).toBe('UNUSED');
  });
});

describe('formatReport', () => {
  test('one line per term and a VALID/INVALID summary the harness tail can show', () => {
    const evaluation = evaluateReply('The latch is a short lock. Vacuum runs.', ['latch', 'vacuum', 'WAL']);
    const report = formatReport(evaluation, 0);
    expect(report).toContain('DEFINED   "latch"');
    expect(report).toContain('UNDEFINED "vacuum" — Vacuum runs.');
    expect(report).toContain('UNUSED    "WAL"');
    expect(report.split('\n').at(-1)).toBe('INVALID: 1 undefined of 2 used (3 listed, max-undefined 0)');
    expect(formatReport(evaluation, 1).split('\n').at(-1)).toStartWith('VALID:');
  });
});

describe('parseArgs', () => {
  test('requires --reply and at least one term', () => {
    expect(parseArgs([]).error).toContain('--reply');
    expect(parseArgs(['--reply', 'r.md']).error).toContain('--terms');
  });

  test('accumulates comma-separated terms and cues, and reads --max-undefined', () => {
    const args = parseArgs(['--reply', 'r.md', '--terms', 'a, b', '--terms', 'c', '--cues', 'là,gọi là', '--max-undefined', '2']);
    expect(args.error).toBeNull();
    expect(args.terms).toEqual(['a', 'b', 'c']);
    expect(args.cues).toEqual(['là', 'gọi là']);
    expect(args.maxUndefined).toBe(2);
  });

  test('rejects a non-integer --max-undefined and an unknown flag', () => {
    expect(parseArgs(['--reply', 'r', '--terms', 'a', '--max-undefined', 'x']).error).toContain('--max-undefined');
    expect(parseArgs(['--reply', 'r', '--terms', 'a', '--bogus']).error).toContain('--bogus');
  });
});

describe('main (impure edge)', () => {
  function withReply(content: string): string {
    const dir = mkdtempSync(join(tmpdir(), 'term-discipline-'));
    const path = join(dir, 'reply.md');
    writeFileSync(path, content);
    return path;
  }

  test('exit 2 on a usage error or an unreadable reply — never a verdict', async () => {
    const err = spyOn(console, 'error').mockImplementation(() => {});
    try {
      expect(await main([])).toBe(EXIT_CODE.CANNOT_RUN);
      expect(await main(['--reply', '/nonexistent/reply.md', '--terms', 'a'])).toBe(EXIT_CODE.CANNOT_RUN);
    } finally {
      err.mockRestore();
    }
  });

  test('exit 1 on an empty reply: nothing was explained', async () => {
    const log = spyOn(console, 'log').mockImplementation(() => {});
    try {
      expect(await main(['--reply', withReply('   \n'), '--terms', 'a'])).toBe(EXIT_CODE.FAIL);
    } finally {
      log.mockRestore();
    }
  });

  test('exit 1 for the baseline reply, exit 0 for the disciplined one', async () => {
    const log = spyOn(console, 'log').mockImplementation(() => {});
    try {
      const terms = INDEX_TERMS.join(',');
      expect(await main(['--reply', withReply(BASELINE_REPLY), '--terms', terms])).toBe(EXIT_CODE.FAIL);
      expect(await main(['--reply', withReply(DISCIPLINED_REPLY), '--terms', terms])).toBe(EXIT_CODE.PASS);
    } finally {
      log.mockRestore();
    }
  });
});
