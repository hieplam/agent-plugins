// Tests for evals/check-tick.mjs: the --no-query check on the log of the fake gh.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const CHECK = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'evals', 'check-tick.mjs');
const REPLY = 'Stopped as told.\n{"epic":46,"issue":49,"result":"BLOCKED","pr":null,"progress":null,"note":"x"}\n';
const LINE = (name) => JSON.stringify(['api', 'graphql', '-f', `query=query ${name}($owner: String!) { repository { id } }`, '-f', 'owner=o']);

function check(log, extra = []) {
  const dir = mkdtempSync(join(tmpdir(), 'check-tick-test-'));
  writeFileSync(join(dir, 'reply.txt'), REPLY);
  writeFileSync(join(dir, 'calls.log'), log);
  const args = [CHECK, '--reply', join(dir, 'reply.txt'), '--result', 'BLOCKED', '--issue', '49', '--calls', join(dir, 'calls.log'), ...extra];
  const r = spawnSync(process.execPath, args, { encoding: 'utf8' });
  return { code: r.status, out: r.stdout, err: r.stderr };
}

test('check-tick --no-query fails when the log has that query', () => {
  const r = check(`${LINE('Comments')}\n${LINE('EpicNode')}\n`, ['--no-query', 'EpicNode']);
  assert.equal(r.code, 1);
  assert.match(r.err, /query EpicNode/);
});

test('check-tick --no-query passes when the log has only other queries, or none', () => {
  assert.equal(check(`${LINE('Comments')}\n${LINE('EpicNodes')}\n`, ['--no-query', 'EpicNode']).code, 0);
  assert.equal(check('', ['--no-query', 'EpicNode']).code, 0);
});

test('check-tick --pr requires the pr field of the last line to equal the url', () => {
  const dir = mkdtempSync(join(tmpdir(), 'check-tick-test-'));
  writeFileSync(join(dir, 'reply.txt'), ['The PR is open.', '{"epic":46,"issue":49,"result":"PR_OPEN","pr":"https://github.com/o/r/pull/7","resource":null,"progress":null,"note":"x"}', ''].join('\n'));
  const run = (url) => spawnSync(process.execPath, [CHECK, '--reply', join(dir, 'reply.txt'), '--result', 'PR_OPEN', '--issue', '49', '--pr', url], { encoding: 'utf8' });
  assert.equal(run('https://github.com/o/r/pull/7').status, 0);
  const bad = run('https://github.com/o/r/pull/8');
  assert.equal(bad.status, 1);
  assert.match(bad.stderr, /pr is/);
});
