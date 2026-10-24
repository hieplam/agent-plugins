// Tests for epic.mjs against the fake gh in fake-gh.mjs. Run: node --test (from the scripts folder or this folder).
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const EPIC = join(HERE, '..', 'epic.mjs');
const FAKE = join(HERE, 'fake-gh.mjs');
const NOW = '2026-10-21T12:00:00Z';
const hoursAgo = (h) => new Date(Date.parse(NOW) - h * 3600e3).toISOString();
const REPO = 'o/r';

// ---------- fixtures

// issues: [{ n, title, parent, blockedBy, labels, state, comments, labelEvents, closedBy }]; children follow list order.
function world(issues, { labels = ['loop:in-progress', 'loop:needs-human'], files = {} } = {}) {
  const s = { repo: REPO, defaultBranch: 'main', nextNumber: 100, labels: [...labels], files, issues: {} };
  for (const i of issues) {
    s.issues[String(i.n)] = {
      title: i.title || `Issue ${i.n}`, body: i.body || '', state: i.state || 'OPEN', labels: i.labels || [],
      parent: i.parent ?? null, children: [], blockedBy: i.blockedBy || [], comments: i.comments || [],
      labelEvents: i.labelEvents || [], closedBy: i.closedBy || [],
    };
  }
  for (const i of issues) if (i.parent != null) s.issues[String(i.parent)].children.push(i.n);
  return s;
}

// The shape of castraferrum epic #46: two phases, tasks chained by "blocked by", four owner gates.
function network(over = {}) {
  const t = (n, parent, title, prev, extra = {}) => ({ n, parent, title, blockedBy: prev ? [prev] : [], ...extra, ...(over[n] || {}) });
  const human = { labels: ['loop:needs-human'] };
  return world([
    { n: 46, title: 'Epic: network access', ...(over[46] || {}) },
    { n: 47, parent: 46, title: 'Phase 1', ...(over[47] || {}) },
    t(48, 47, 'L0 - Baseline', null), t(49, 47, 'L1 - Flags', 48), t(50, 47, 'L2 - Listen', 49), t(51, 47, 'L3 - Phone preview', 50),
    t(52, 47, 'L4 - Owner approval of the phone preview', 51, human), t(53, 47, 'L5 - Phone layout', 52),
    t(54, 47, 'L6 - Owner acceptance', 53, human),
    { n: 55, parent: 46, title: 'Phase 2', blockedBy: [47], ...(over[55] || {}) },
    t(56, 55, 'R1 - Flags', 54), t(57, 55, 'R2 - Sign-in', 56), t(58, 55, 'R3 - Preview', 57),
    t(59, 55, 'R4 - Owner approval', 58, human), t(60, 55, 'R5 - Screen', 59), t(61, 55, 'R6 - Tunnel', 60),
    t(62, 55, 'R7 - Owner acceptance', 61, human),
  ]);
}
const closed = (...ns) => Object.fromEntries(ns.map((n) => [n, { state: 'CLOSED', labels: [] }]));

const VERIFY_BODY = 'Closes #48\n\n## Verify\n\n- Red: test failed (missing module)\n- Green: 2 passed\n- Stub check: an empty checker fails the 600px case\n';
const mergedPr = (body = VERIFY_BODY) => [{ number: 200, url: 'https://github.com/o/r/pull/200', merged: true, title: 'feat: L0', body }];

// ---------- runner

function run(args, state, { cwd } = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'epic-loop-test-'));
  const stateFile = join(dir, 'state.json');
  const logFile = join(dir, 'calls.log');
  writeFileSync(stateFile, JSON.stringify(state));
  writeFileSync(logFile, '');
  const r = spawnSync(process.execPath, [EPIC, ...args], {
    cwd: cwd || dir, encoding: 'utf8',
    env: { ...process.env, EPIC_LOOP_GH_MODULE: FAKE, EPIC_LOOP_FAKE_STATE: stateFile, EPIC_LOOP_FAKE_LOG: logFile, EPIC_LOOP_NOW: NOW },
  });
  const calls = readFileSync(logFile, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));
  const writes = calls.filter((c) => isWrite(c));
  let json = null;
  try { json = JSON.parse(r.stdout.trim().split('\n').pop()); } catch { /* not JSON */ }
  return { code: r.status, stdout: r.stdout, stderr: r.stderr, json, state: JSON.parse(readFileSync(stateFile, 'utf8')), calls, writes };
}
function isWrite(c) {
  if (c[0] === 'issue' && c[1] !== 'view' && c[1] !== 'list') return true;
  if (c[0] === 'label' && c[1] === 'create') return true;
  return c[0] === 'api' && c.includes('-X') && c[c.indexOf('-X') + 1] !== 'GET';
}
const issueOf = (r, n) => r.state.issues[String(n)];

// ---------- next: the decision of one run

test('next takes the first ready task of a fresh epic', () => {
  const r = run(['next', '46', '-R', REPO], network());
  assert.equal(r.code, 0, r.stderr);
  assert.equal(r.json.action, 'take');
  assert.equal(r.json.issue, 48);
  assert.equal(r.json.attempt, 1);
  assert.deepEqual(r.json.counts, { total: 14, done: 0, inProgress: 0, ready: 1, blocked: 9, needsHuman: 4 });
});

test('next --dry-run changes nothing on GitHub', () => {
  const r = run(['next', '46', '-R', REPO, '--dry-run'], network(closed(48, 49, 50, 51, 52, 53, 54)));
  assert.equal(r.code, 0, r.stderr);
  assert.equal(r.json.issue, 56, 'phase 1 is done, so its blocker on phase 2 no longer counts');
  assert.deepEqual(r.json.closed, []);
  assert.deepEqual(r.writes, []);
});

test('next skips the run while a task is in progress', () => {
  const r = run(['next', '46', '-R', REPO], network({ ...closed(48), 49: { labels: ['loop:in-progress'], labelEvents: [{ label: 'loop:in-progress', createdAt: hoursAgo(1) }] } }));
  assert.equal(r.json.action, 'skip');
  assert.equal(r.json.issue, 49);
  assert.equal(r.json.stale, false);
  assert.equal(r.json.hours, 1);
  assert.deepEqual(r.writes, []);
});

test('next marks a claim older than the stale window as stale', () => {
  const st = network({ ...closed(48), 49: { labels: ['loop:in-progress'], labelEvents: [{ label: 'loop:in-progress', createdAt: hoursAgo(7) }] } });
  assert.equal(run(['next', '46', '-R', REPO], st).json.stale, true);
  assert.equal(run(['next', '46', '-R', REPO, '--stale-hours', '8'], st).json.stale, false);
});

test('next waits for the owner at an owner gate', () => {
  const r = run(['next', '46', '-R', REPO], network(closed(48, 49, 50, 51)));
  assert.equal(r.json.action, 'wait');
  assert.deepEqual(r.json.issues.map((i) => i.issue), [52]);
  assert.match(r.json.issues[0].title, /Owner approval/);
});

test('next moves on once the owner closes the gate', () => {
  const r = run(['next', '46', '-R', REPO], network(closed(48, 49, 50, 51, 52)));
  assert.equal(r.json.action, 'take');
  assert.equal(r.json.issue, 53);
});

test('next closes a finished phase and takes the first task of the next one', () => {
  const r = run(['next', '46', '-R', REPO], network(closed(48, 49, 50, 51, 52, 53, 54)));
  assert.equal(r.json.action, 'take');
  assert.equal(r.json.issue, 56);
  assert.deepEqual(r.json.closed, [47]);
  assert.equal(issueOf(r, 47).state, 'CLOSED');
  assert.equal(issueOf(r, 46).state, 'OPEN');
});

test('next reports done and closes the phases and the epic when every task is closed', () => {
  const all = closed(48, 49, 50, 51, 52, 53, 54, 56, 57, 58, 59, 60, 61, 62);
  const r = run(['next', '46', '-R', REPO], network(all));
  assert.equal(r.json.action, 'done');
  assert.deepEqual(r.json.closed.sort(), [46, 47, 55]);
  assert.equal(issueOf(r, 46).state, 'CLOSED');
});

test('next reports blocked when the open tasks wait for an issue outside the epic', () => {
  const st = world([
    { n: 1, title: 'Epic' }, { n: 2, parent: 1, blockedBy: [99] }, { n: 3, parent: 1, blockedBy: [2] }, { n: 99, title: 'Elsewhere' },
  ]);
  const r = run(['next', '1', '-R', REPO], st);
  assert.equal(r.json.action, 'blocked');
  assert.deepEqual(r.json.issues.map((i) => [i.issue, i.blockedBy]), [[2, [99]], [3, [2]]]);
});

test('next prefers the ready task that unblocks the most open issues, then sub-issue order', () => {
  const st = world([
    { n: 1, title: 'Epic' }, { n: 2, parent: 1, title: 'Unrelated' }, { n: 3, parent: 1, title: 'Tool fix' },
    { n: 4, parent: 1, blockedBy: [3] }, { n: 5, parent: 1, blockedBy: [3] },
  ]);
  assert.equal(run(['next', '1', '-R', REPO], st).json.issue, 3);
  const tie = world([{ n: 1 }, { n: 2, parent: 1 }, { n: 3, parent: 1 }]);
  assert.equal(run(['next', '1', '-R', REPO], tie).json.issue, 2);
});

test('a task inherits the blockers of its phase', () => {
  const st = world([
    { n: 1, title: 'Epic' }, { n: 10, parent: 1, title: 'Phase A' }, { n: 11, parent: 10 },
    { n: 20, parent: 1, title: 'Phase B', blockedBy: [10] }, { n: 21, parent: 20 }, { n: 22, parent: 20 },
  ]);
  const r = run(['status', '1', '-R', REPO, '--json'], st);
  const g = Object.fromEntries(r.json.tasks.map((t) => [t.issue, t.group]));
  assert.deepEqual(g, { 11: 'ready', 21: 'blocked', 22: 'blocked' });
  assert.equal(run(['next', '1', '-R', REPO], st).json.issue, 11);
});

test('a loop:needs-human label on a phase holds every task under it', () => {
  const st = world([{ n: 1 }, { n: 10, parent: 1, labels: ['loop:needs-human'] }, { n: 11, parent: 10 }]);
  const r = run(['next', '1', '-R', REPO], st);
  assert.equal(r.json.action, 'wait');
  assert.deepEqual(r.json.issues.map((i) => i.issue), [11]);
});

test('the older Vietnamese labels loop:dang-lam and loop:can-nguoi are understood', () => {
  const labels = ['loop:dang-lam', 'loop:can-nguoi'];
  const busy = world([{ n: 1 }, { n: 2, parent: 1, labels: ['loop:dang-lam'], labelEvents: [{ label: 'loop:dang-lam', createdAt: hoursAgo(2) }] }, { n: 3, parent: 1 }], { labels });
  assert.equal(run(['next', '1', '-R', REPO], busy).json.action, 'skip');
  const gate = world([{ n: 1 }, { n: 2, parent: 1, labels: ['loop:can-nguoi'] }], { labels });
  assert.equal(run(['next', '1', '-R', REPO], gate).json.action, 'wait');
  const ready = world([{ n: 1 }, { n: 2, parent: 1 }], { labels });
  const r = run(['claim', '2', '-R', REPO], ready);
  assert.equal(r.code, 0, r.stderr);
  assert.deepEqual(issueOf(r, 2).labels, ['loop:dang-lam']);
  assert.ok(!r.state.labels.includes('loop:in-progress'), 'no new label when the repo already has its own');
});

test('other labels that hold a task are respected, but never written', () => {
  const labels = ['in-progress', 'frozen', 'on-hold', 'blocked'];
  const busy = world([{ n: 1 }, { n: 2, parent: 1, labels: ['in-progress'] }, { n: 3, parent: 1 }], { labels });
  assert.equal(run(['next', '1', '-R', REPO], busy).json.action, 'skip');
  for (const hold of ['frozen', 'on-hold', 'blocked']) {
    const st = world([{ n: 1 }, { n: 2, parent: 1, labels: [hold, 'in-progress'] }], { labels });
    const r = run(['next', '1', '-R', REPO], st);
    assert.equal(r.json.action, 'wait', `${hold} holds the task for a human`);
    assert.match(run(['status', '1', '-R', REPO], st).stdout, new RegExp(`#2\\s+needs human .*\\(${hold}\\)`));
    assert.match(run(['claim', '2', '-R', REPO], st).stderr, /#2 needs human/);
  }
  const r = run(['release', '2', '-R', REPO, '--reason', 'x', '--needs-human'], world([{ n: 1 }, { n: 2, parent: 1 }], { labels }));
  assert.deepEqual(issueOf(r, 2).labels, ['loop:needs-human'], 'release writes the loop label, not frozen or blocked');
});

test('next on an epic without sub-issues says so instead of claiming done', () => {
  const r = run(['next', '1', '-R', REPO], world([{ n: 1 }]));
  assert.equal(r.json.action, 'blocked');
  assert.match(r.json.reason, /no sub-issues/);
});

// ---------- claim

test('claim labels a ready task and records the attempt', () => {
  const r = run(['claim', '48', '-R', REPO, '--note', 'baseline: unit=382'], network());
  assert.equal(r.code, 0, r.stderr);
  assert.deepEqual(r.json, { claimed: true, issue: 48, attempt: 1, epic: 46 });
  assert.ok(issueOf(r, 48).labels.includes('loop:in-progress'));
  assert.match(issueOf(r, 48).comments.at(-1), /^loop-attempt: 1\b/);
  assert.match(issueOf(r, 48).comments.at(-1), /baseline: unit=382/);
});

test('claim refuses a task that is blocked, waits for the owner, or is a parent', () => {
  for (const [n, why] of [['49', /#49 is blocked \(by #48\)/], ['52', /#52 needs human/], ['47', /#47 is not a task/]]) {
    const r = run(['claim', n, '-R', REPO], network());
    assert.equal(r.code, 1, `claim ${n} should be refused`);
    assert.match(r.stderr, why);
    assert.deepEqual(r.writes, [], `claim ${n} must not write`);
  }
});

test('claim refuses while another task of the epic is in progress', () => {
  const st = world([{ n: 1 }, { n: 2, parent: 1, labels: ['loop:in-progress'] }, { n: 3, parent: 1 }]);
  const r = run(['claim', '3', '-R', REPO], st);
  assert.equal(r.code, 1);
  assert.match(r.stderr + r.stdout, /#2 is in progress/);
  assert.deepEqual(r.writes, []);
});

test('claim counts earlier attempts, including the older loop-baseline comments', () => {
  const st = world([{ n: 1 }, { n: 2, parent: 1, comments: ['loop-baseline: debt=4', 'some talk', 'loop-attempt: 2'] }]);
  const r = run(['claim', '2', '-R', REPO], st);
  assert.equal(r.json.attempt, 3);
});

test('claim gives up after the maximum number of attempts and hands the task to the owner', () => {
  const st = world([{ n: 1 }, { n: 2, parent: 1, comments: ['loop-attempt: 1', 'loop-attempt: 2', 'loop-attempt: 3'] }]);
  const r = run(['claim', '2', '-R', REPO], st);
  assert.equal(r.code, 1);
  assert.equal(r.json.gaveUp, true);
  assert.deepEqual(issueOf(r, 2).labels, ['loop:needs-human']);
  assert.match(issueOf(r, 2).comments.at(-1), /^loop-gave-up:/);
  assert.equal(run(['claim', '2', '-R', REPO, '--max-attempts', '4'], st).json.attempt, 4);
});

test('claim creates the loop label when the repository has none', () => {
  const r = run(['claim', '2', '-R', REPO], world([{ n: 1 }, { n: 2, parent: 1 }], { labels: [] }));
  assert.equal(r.code, 0, r.stderr);
  assert.ok(r.state.labels.includes('loop:in-progress'));
  assert.deepEqual(issueOf(r, 2).labels, ['loop:in-progress']);
});

// ---------- release

test('release puts the task back in the queue with the reason', () => {
  const st = network({ 48: { labels: ['loop:in-progress'] } });
  const r = run(['release', '48', '-R', REPO, '--reason', 'Green 2 did not match'], st);
  assert.equal(r.code, 0, r.stderr);
  assert.deepEqual(issueOf(r, 48).labels, []);
  assert.match(issueOf(r, 48).comments.at(-1), /^loop-release: Green 2 did not match/);
  assert.equal(run(['next', '46', '-R', REPO], r.state).json.issue, 48);
});

test('release --needs-human hands the task to the owner, and reopens a closed task', () => {
  const st = network({ 48: { state: 'CLOSED', labels: ['loop:in-progress'] } });
  const r = run(['release', '48', '-R', REPO, '--reason', 'needs a decision', '--needs-human'], st);
  assert.equal(issueOf(r, 48).state, 'OPEN');
  assert.deepEqual(issueOf(r, 48).labels, ['loop:needs-human']);
});

test('release without --reason is a usage error', () => {
  const r = run(['release', '48', '-R', REPO], network());
  assert.equal(r.code, 2);
  assert.deepEqual(r.writes, []);
});

// ---------- check

test('check passes a task closed by a merged PR with a Verify section, and tidies up', () => {
  const st = network({ 48: { state: 'CLOSED', labels: ['loop:in-progress'], closedBy: mergedPr() } });
  const r = run(['check', '48', '-R', REPO], st);
  assert.equal(r.code, 0, r.stdout + r.stderr);
  assert.match(r.stdout, /PASS #48 via PR #200/);
  assert.deepEqual(issueOf(r, 48).labels, []);
  assert.match(issueOf(r, 48).comments.at(-1), /^loop-check: PASS via PR #200/);
});

test('check closes the phase when its last task passes', () => {
  const st = network({ ...closed(48, 49, 50, 51, 52, 53), 54: { state: 'CLOSED', closedBy: mergedPr() } });
  const r = run(['check', '54', '-R', REPO], st);
  assert.equal(r.code, 0, r.stdout + r.stderr);
  assert.equal(issueOf(r, 47).state, 'CLOSED');
  assert.equal(issueOf(r, 46).state, 'OPEN');
});

test('check fails, and writes nothing, when the task is not really done', () => {
  const cases = [
    [{ state: 'OPEN', closedBy: mergedPr() }, /not closed/],
    [{ state: 'CLOSED', closedBy: [] }, /no merged pull request/],
    [{ state: 'CLOSED', closedBy: [{ ...mergedPr()[0], merged: false }] }, /no merged pull request/],
    [{ state: 'CLOSED', closedBy: mergedPr('Closes #48\n\nDone.') }, /no "## Verify" section/],
    [{ state: 'CLOSED', closedBy: mergedPr('Closes #48\n\n## Verify\n\nRed: x\nGreen: y\n') }, /Stub/],
  ];
  for (const [over, why] of cases) {
    const r = run(['check', '48', '-R', REPO], network({ 48: over }));
    assert.equal(r.code, 1, `expected FAIL for ${why}`);
    assert.match(r.stdout, why);
    assert.deepEqual(r.writes, []);
  }
});

test('check --evidence requires the heading in the evidence log on the default branch', () => {
  const files = { 'docs/plans/x-evidence.md': '# Evidence\n\n## L0\n\nRed: ...\n' };
  const st = { ...network({ 48: { state: 'CLOSED', closedBy: mergedPr() } }), files };
  assert.equal(run(['check', '48', '-R', REPO, '--dry-run', '--evidence', 'docs/plans/x-evidence.md', '--heading', '## L0'], st).code, 0);
  const r = run(['check', '48', '-R', REPO, '--dry-run', '--evidence', 'docs/plans/x-evidence.md', '--heading', '## L1'], st);
  assert.equal(r.code, 1);
  assert.match(r.stdout, /no "## L1" heading/);
});

// ---------- link

test('link adds an issue found mid-loop to the epic and makes it block the affected tasks', () => {
  const st = network();
  st.issues['70'] = { title: 'Tool fix', body: '', state: 'OPEN', labels: [], parent: null, children: [], blockedBy: [], comments: [], labelEvents: [], closedBy: [] };
  const r = run(['link', '70', '-R', REPO, '--parent', '46', '--blocks', '53,56', '--blocked-by', '48'], st);
  assert.equal(r.code, 0, r.stderr);
  assert.ok(issueOf(r, 46).children.includes(70));
  assert.ok(issueOf(r, 53).blockedBy.includes(70));
  assert.ok(issueOf(r, 56).blockedBy.includes(70));
  assert.ok(issueOf(r, 70).blockedBy.includes(48));
});

// ---------- create

function planRepo() {
  const dir = mkdtempSync(join(tmpdir(), 'epic-loop-repo-'));
  mkdirSync(join(dir, 'docs', 'plans'), { recursive: true });
  mkdirSync(join(dir, 'work'));
  writeFileSync(join(dir, 'docs', 'plans', 'p.md'), [
    '# Plan', '', 'How to read this plan.', '', '---', '',
    '## A0 - Baseline', '', 'Measure the numbers.', '', '**Verify**', '- **Goal:** g0', '',
    '## A1 - Owner approval', '', 'The owner approves. `loop:needs-human`', '',
    '## A2 - Build', '', 'Build it.', '',
  ].join('\n'));
  writeFileSync(join(dir, 'work', 'epic.md'), 'Phases: {{#P1}} then {{#P2}}. First task {{#A0}}.\n\n## Loop prompt\n\nRun one tick of the epic loop on epic {{#epic}}.\n');
  writeFileSync(join(dir, 'work', 'p1.md'), 'Phase 1 of {{#epic}}.\n');
  writeFileSync(join(dir, 'work', 'b0.md'), 'Second phase task, by hand.\n');
  const manifest = {
    repo: REPO, labels: ['topic'],
    epic: { title: 'Epic: thing', body: 'epic.md' },
    phases: [
      { key: 'P1', title: 'Phase 1', body: 'p1.md', spec: 'docs/specs/s.md', plan: 'docs/plans/p.md', tasks: [
        { key: 'A0', title: 'A0 - Baseline', section: 'A0' },
        { key: 'A1', title: 'A1 - Owner approval', section: 'A1', needsHuman: true },
        { key: 'A2', title: 'A2 - Build', section: 'A2' },
      ] },
      { key: 'P2', title: 'Phase 2', tasks: [{ key: 'B0', title: 'B0 - More', body: 'b0.md' }] },
    ],
  };
  writeFileSync(join(dir, 'work', 'manifest.json'), JSON.stringify(manifest, null, 2));
  return { dir, manifest: join(dir, 'work', 'manifest.json') };
}

test('create builds the epic, its phases and tasks, and links them in order', () => {
  const { dir, manifest } = planRepo();
  const r = run(['create', manifest], world([], { labels: [] }), { cwd: dir });
  assert.equal(r.code, 0, r.stderr);
  const m = r.json.issues;
  assert.deepEqual(Object.keys(m), ['epic', 'P1', 'A0', 'A1', 'A2', 'P2', 'B0']);
  const i = (k) => issueOf(r, m[k]);
  assert.deepEqual(i('epic').children, [m.P1, m.P2]);
  assert.deepEqual(i('P1').children, [m.A0, m.A1, m.A2]);
  assert.deepEqual(i('P2').children, [m.B0]);
  assert.deepEqual(i('A0').blockedBy, []);
  assert.deepEqual(i('A1').blockedBy, [m.A0]);
  assert.deepEqual(i('A2').blockedBy, [m.A1]);
  assert.deepEqual(i('B0').blockedBy, [m.A2], 'the chain runs across phases');
  assert.deepEqual(i('P2').blockedBy, [m.P1]);
  assert.deepEqual(i('A1').labels.sort(), ['loop:needs-human', 'topic']);
  assert.deepEqual(i('A0').labels, ['topic']);
  // bodies: plan sections copied verbatim, placeholders resolved
  assert.match(i('A0').body, /## A0 - Baseline[\s\S]*Measure the numbers/);
  assert.doesNotMatch(i('A0').body, /A1 - Owner approval/);
  assert.match(i('A0').body, /plan: \[docs\/plans\/p\.md\]\(https:\/\/github\.com\/o\/r\/blob\/main\/docs\/plans\/p\.md\)/);
  assert.match(i('A1').body, /A loop never works on this issue/);
  assert.equal(i('epic').body, `Phases: #${m.P1} then #${m.P2}. First task #${m.A0}.\n\n## Loop prompt\n\nRun one tick of the epic loop on epic #${m.epic}.\n`);
  assert.equal(i('P1').body, `Phase 1 of #${m.epic}.\n`);
  assert.equal(i('B0').body, 'Second phase task, by hand.\n');
  // the result is a graph a loop can run
  assert.equal(run(['next', String(m.epic), '-R', REPO], r.state).json.issue, m.A0);
});

test('create --dry-run writes nothing and lists what it would do', () => {
  const { dir, manifest } = planRepo();
  const r = run(['create', manifest, '--dry-run'], world([], { labels: [] }), { cwd: dir });
  assert.equal(r.code, 0, r.stderr);
  assert.deepEqual(r.writes, []);
  assert.match(r.stdout, /create A1 "A1 - Owner approval".*loop:needs-human/);
  assert.match(r.stdout, /block A1 by A0/);
  assert.match(r.stdout, /block P2 by P1/);
});

test('create can add phases to an existing epic and honours an explicit blockedBy', () => {
  const dir = mkdtempSync(join(tmpdir(), 'epic-loop-repo-'));
  writeFileSync(join(dir, 'x.md'), 'x\n');
  writeFileSync(join(dir, 'm.json'), JSON.stringify({
    repo: REPO, epic: { number: 1 },
    tasks: [{ key: 'X', title: 'X', body: 'x.md' }, { key: 'Y', title: 'Y', body: 'x.md', blockedBy: [] }, { key: 'Z', title: 'Z', body: 'x.md', blockedBy: ['X', 'Y'] }],
  }));
  const r = run(['create', join(dir, 'm.json')], world([{ n: 1, title: 'Old epic' }]), { cwd: dir });
  assert.equal(r.code, 0, r.stderr);
  const m = r.json.issues;
  assert.equal(m.epic, 1);
  assert.deepEqual(issueOf(r, 1).children, [m.X, m.Y, m.Z]);
  assert.deepEqual(issueOf(r, m.Y).blockedBy, []);
  assert.deepEqual(issueOf(r, m.Z).blockedBy, [m.X, m.Y]);
});

// ---------- status and usage

test('status prints the tree with each task state and the next step', () => {
  const r = run(['status', '46', '-R', REPO], network({ ...closed(48), 49: { labels: ['loop:in-progress'], labelEvents: [{ label: 'loop:in-progress', createdAt: hoursAgo(2) }] } }));
  assert.equal(r.code, 0, r.stderr);
  assert.match(r.stdout, /#48\s+done\s+L0 - Baseline/);
  assert.match(r.stdout, /#49\s+in progress\s+L1 - Flags \(2h\)/);
  assert.match(r.stdout, /#50\s+blocked\s+L2 - Listen \(by #49\)/);
  assert.match(r.stdout, /#52\s+needs human\s+L4 - Owner approval/);
  assert.match(r.stdout, /done 1 \| in progress 1 \| ready 0 \| blocked 8 \| needs human 4/);
  assert.match(r.stdout, /next: skip, #49 is in progress/);
});

test('bad usage exits 2', () => {
  assert.equal(run(['frobnicate'], network()).code, 2);
  assert.equal(run(['next'], network()).code, 2);
  assert.equal(run(['claim', 'abc', '-R', REPO], network()).code, 2);
});

// ---------- the goal-anchored loop: parallel lanes, found issues, rewiring

// An epic whose tasks 3 and 4 depend only on 2: a coordinator may run them side by side.
const lanes = (over = {}) => world([
  { n: 1, title: 'Epic' },
  { n: 2, parent: 1, title: 'T2', ...(over[2] || {}) },
  { n: 3, parent: 1, title: 'T3', blockedBy: [2], ...(over[3] || {}) },
  { n: 4, parent: 1, title: 'T4', blockedBy: [2], ...(over[4] || {}) },
  { n: 5, parent: 1, title: 'T5', blockedBy: [3, 4], ...(over[5] || {}) },
]);

test('claim --parallel takes a ready task while another task of the epic is in progress', () => {
  const st = lanes({ 2: { state: 'CLOSED' }, 3: { labels: ['loop:in-progress'] } });
  assert.equal(run(['claim', '4', '-R', REPO], st).code, 1, 'without --parallel an epic runs one task at a time');
  const r = run(['claim', '4', '-R', REPO, '--parallel'], st);
  assert.equal(r.code, 0, r.stderr);
  assert.deepEqual(r.json, { claimed: true, issue: 4, attempt: 1, epic: 1 });
  assert.ok(issueOf(r, 4).labels.includes('loop:in-progress'));
  assert.equal(run(['claim', '5', '-R', REPO, '--parallel'], r.state).code, 1, '--parallel never takes a blocked task');
});

test('next --parallel lists every ready task even while one is in progress', () => {
  const st = lanes({ 2: { state: 'CLOSED' }, 3: { labels: ['loop:in-progress'], labelEvents: [{ label: 'loop:in-progress', createdAt: hoursAgo(1) }] } });
  assert.equal(run(['next', '1', '-R', REPO, '--dry-run'], st).json.action, 'skip');
  const r = run(['next', '1', '-R', REPO, '--dry-run', '--parallel'], st);
  assert.equal(r.code, 0, r.stderr);
  assert.equal(r.json.action, 'take');
  assert.equal(r.json.issue, 4);
  assert.deepEqual(r.json.ready.map((t) => t.issue), [4]);
  assert.deepEqual(r.json.inProgress, [3]);
  const all = run(['next', '1', '-R', REPO, '--dry-run', '--parallel'], lanes({ 2: { state: 'CLOSED' } }));
  assert.deepEqual(all.json.ready.map((t) => t.issue), [3, 4]);
});

test('release --found files the defect into the epic, makes it block the task, and does not spend an attempt', () => {
  const st = lanes({ 2: { labels: ['loop:in-progress'], comments: ['loop-attempt: 1', 'loop-attempt: 2', 'loop-attempt: 3'] } });
  st.issues['9'] = { title: 'Defect found by T2', body: '', state: 'OPEN', labels: [], parent: null, children: [], blockedBy: [], comments: [], labelEvents: [], closedBy: [] };
  const r = run(['release', '2', '-R', REPO, '--reason', 'Green found a real defect', '--found', '9'], st);
  assert.equal(r.code, 0, r.stderr);
  assert.deepEqual(r.json, { released: true, issue: 2, needsHuman: false, reopened: false, found: [9] });
  assert.ok(issueOf(r, 2).blockedBy.includes(9), 'the found issue blocks the task');
  assert.ok(issueOf(r, 1).children.includes(9), 'the found issue joins the epic, so the loop takes it next');
  assert.deepEqual(issueOf(r, 2).labels, []);
  assert.match(issueOf(r, 2).comments.at(-1), /^loop-release: Green found a real defect[\s\S]*^loop-found: #9/m);
  assert.equal(run(['next', '1', '-R', REPO, '--dry-run'], r.state).json.issue, 9);
  const after = structuredClone(r.state);
  after.issues['9'].state = 'CLOSED';
  const again = run(['claim', '2', '-R', REPO], after);
  assert.equal(again.code, 0, again.stderr);
  assert.equal(again.json.attempt, 3, 'the attempt that found #9 is not counted, so the task is not handed to a human');
});

test('release --found refuses the task itself and an unknown issue', () => {
  const st = lanes({ 2: { labels: ['loop:in-progress'] } });
  const self = run(['release', '2', '-R', REPO, '--reason', 'x', '--found', '2'], st);
  assert.equal(self.code, 2);
  assert.deepEqual(self.writes, []);
  assert.equal(run(['release', '2', '-R', REPO, '--reason', 'x', '--found', '77'], st).code, 2);
});

test('unlink removes "blocked by" links, so the order can follow the real dependencies', () => {
  const st = lanes();
  const r = run(['unlink', '4', '-R', REPO, '--blocked-by', '2', '--blocks', '5'], st);
  assert.equal(r.code, 0, r.stderr);
  assert.deepEqual(issueOf(r, 4).blockedBy, []);
  assert.deepEqual(issueOf(r, 5).blockedBy, [3]);
  assert.deepEqual(r.json.unlinked, ['#4 is no longer blocked by #2', '#5 is no longer blocked by #4']);
  assert.equal(run(['unlink', '4', '-R', REPO], st).code, 2, 'unlink needs --blocked-by or --blocks');
});

test('link is idempotent: an existing "blocked by" link is reported, not an error', () => {
  const r = run(['link', '3', '-R', REPO, '--blocked-by', '2'], lanes());
  assert.equal(r.code, 0, r.stderr);
  assert.deepEqual(r.json.linked, ['#3 is blocked by #2 (already)']);
});
