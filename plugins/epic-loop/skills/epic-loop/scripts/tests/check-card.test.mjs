// Tests for evals/check-card.mjs, and for the shape of evals 8 to 10 (issue 311: a prompt never sends a model to look for an app).
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const CHECK = join(HERE, '..', '..', 'evals', 'check-card.mjs');
const EVALS = join(HERE, '..', '..', 'evals', 'evals.json');
const LAST_LINE = 'Use only the files in the current folder. Do not search the disk. Do not start a service or a container.';

const CARD = {
  v: 1, title: 'Export notes', rows: [{ id: 'G1', outcome: 'A user exports notes', oracle: 'node t.mjs', threshold: 'ok' }],
  size: { planWindows: 0.5 }, door: 'two-way', next: ['Import notes'],
};
const fence = (card) => `\`\`\`json\n${JSON.stringify(card, null, 2)}\n\`\`\`\n`;
const cardIssue = (body = fence(CARD)) => ({ title: 'Goal card: Export notes', body, state: 'OPEN', labels: ['goal-card'], comments: [] });
const issue = (over = {}) => ({ title: 'Old', body: '', state: 'OPEN', labels: [], comments: [], ...over });

function check(orig, now, flags) {
  const dir = mkdtempSync(join(tmpdir(), 'check-card-test-'));
  writeFileSync(join(dir, 'orig.json'), JSON.stringify({ issues: orig }));
  writeFileSync(join(dir, 'state.json'), JSON.stringify({ issues: now }));
  const r = spawnSync(process.execPath, [CHECK, '--state', join(dir, 'state.json'), '--orig', join(dir, 'orig.json'), ...flags], { encoding: 'utf8' });
  return { code: r.status, out: r.stdout, err: r.stderr };
}

test('check-card passes one valid new card with the expected door', () => {
  const r = check({ 7: issue() }, { 7: issue(), 100: cardIssue() }, ['--cards', '1', '--door', 'two-way', '--unchanged', '7']);
  assert.equal(r.code, 0, r.err);
  assert.match(r.out, /^ok: /);
});

test('check-card fails on the wrong door', () => {
  const r = check({}, { 100: cardIssue() }, ['--cards', '1', '--door', 'one-way']);
  assert.equal(r.code, 1);
  assert.match(r.err, /door is two-way, expected one-way/);
});

test('check-card fails on the wrong number of new cards', () => {
  const none = check({}, {}, ['--cards', '1']);
  assert.equal(none.code, 1);
  assert.match(none.err, /0 new goal cards, expected 1/);
  const two = check({}, { 100: cardIssue(), 101: cardIssue() }, ['--cards', '1']);
  assert.equal(two.code, 1);
  assert.match(two.err, /2 new goal cards, expected 1/);
});

test('check-card fails on a body with two blocks and on a card with a bad field', () => {
  const two = check({}, { 100: cardIssue(fence(CARD) + fence(CARD)) }, ['--cards', '1']);
  assert.equal(two.code, 1);
  assert.match(two.err, /2 fenced json blocks/);
  const bad = check({}, { 100: cardIssue(fence({ ...CARD, rows: [] })) }, ['--cards', '1']);
  assert.equal(bad.code, 1);
  assert.match(bad.err, /rows/);
});

test('check-card --closed needs a loop-tidy comment, and --unchanged fails on a changed issue', () => {
  const flags = ['--cards', '0', '--closed', '7'];
  assert.equal(check({ 7: issue() }, { 7: issue({ state: 'CLOSED', comments: ['loop-tidy: node p.mjs exited 0'] }) }, flags).code, 0);
  const open = check({ 7: issue() }, { 7: issue() }, flags);
  assert.equal(open.code, 1);
  assert.match(open.err, /#7 is not closed by tidy/);
  const bare = check({ 7: issue() }, { 7: issue({ state: 'CLOSED', comments: ['done'] }) }, flags);
  assert.equal(bare.code, 1);
  assert.match(bare.err, /#7 is not closed by tidy/);
  const changed = check({ 8: issue({ labels: ['loop:needs-human'] }) }, { 8: issue() }, ['--cards', '0', '--unchanged', '8']);
  assert.equal(changed.code, 1);
  assert.match(changed.err, /#8 changed/);
});

test('evals 8 to 10 exist, and their prompts send no model to look for an app', () => {
  const { evals } = JSON.parse(readFileSync(EVALS, 'utf8'));
  for (const id of [8, 9, 10]) {
    const e = evals.find((x) => x.id === id);
    assert.ok(e, `eval ${id} is missing`);
    assert.doesNotMatch(e.prompt, /\b(app|docker|server|localhost)\b/i);
    assert.ok(e.prompt.endsWith(LAST_LINE), `eval ${id} does not end with the fixed line`);
    assert.ok(e.checks.some((c) => c.command.includes('evals/check-card.mjs')), `eval ${id} has no check-card check`);
  }
});
