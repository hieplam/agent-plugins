#!/usr/bin/env node
// epic.mjs - read and write the issue graph of an epic, so that a loop run, or the session driving one, never guesses.
//
// An epic is a GitHub issue whose sub-issues are its phases or its tasks (any depth); a task is an issue with no
// sub-issues. "Blocked by" links give the order, two labels give the state, and comments count the attempts. Nothing
// is stored locally: every command reads GitHub again, so a fresh session picks up exactly where the last one stopped.
//
//   status <epic> [--json]                         every task: done / in progress / needs human / blocked / ready
//   next <epic> [--dry-run] [--parallel]           the decision of one run, one JSON line: take | skip | wait | blocked | done
//                                                  (also closes phases and the epic once all their sub-issues are closed);
//                                                  --parallel: take even while tasks are in progress, and list every ready task
//   claim <task> [--epic <n>] [--note "<text>"] [--parallel]
//                                                  take a ready task: label loop:in-progress, comment "loop-attempt: <k>";
//                                                  --parallel allows it while other tasks of the epic are in progress
//   release <task> --reason "<why>" [--needs-human] [--found <n,...>]
//                                                  hand it back to the queue, or to a human; reopens it if closed;
//                                                  --found: the attempt found defects filed as issues <n,...>: each joins the
//                                                  epic and blocks the task, and the attempt does not count toward the limit
//   check <task> [--dry-run] [--evidence <path> --heading "<text>"]
//                                                  really done? closed by a merged PR whose body has a "## Verify" section
//                                                  naming Red, Green and Stub; PASS removes the label, closes finished parents
//   link <issue> [--parent <n>] [--blocked-by <n,...>] [--blocks <n,...>]   (an existing link is reported, not an error)
//   unlink <issue> [--blocked-by <n,...>] [--blocks <n,...>]                remove "blocked by" links (rewire to the real order)
//   create <manifest.json> [--dry-run]             create an epic, its phases and tasks, and every link (references/plan.md)
//
// Options: -R <owner/name> (default: the repository of the current directory), --stale-hours <h> (default 6),
// --max-attempts <k> (default 3). Labels: loop:in-progress and loop:needs-human; a repository that already has
// loop:dang-lam / loop:can-nguoi keeps using those. Also read, never written: in-progress (taken), frozen, on-hold, blocked (held). Exit codes: 0 ok, 1 refused or not done, 2 bad usage or GitHub error.
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, isAbsolute, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const WORK = ['loop:in-progress', 'loop:dang-lam'];    // the first name is the one created when a repo has none
const HUMAN = ['loop:needs-human', 'loop:can-nguoi'];
// Read, never written: other labels that already mean "someone is on it" or "do not take this".
const WORK_SEEN = [...WORK, 'in-progress'];
const HELD = [...HUMAN, 'frozen', 'on-hold', 'blocked'];
const STYLE = {
  'loop:in-progress': { color: 'FBCA04', description: 'A loop run is working on this task' },
  'loop:needs-human': { color: 'D93F0B', description: 'Waiting for a human: a loop never takes this task' },
};
const ATTEMPT = /^loop-(attempt|baseline):/;            // loop-baseline: is what the older loop.js / loop.mjs wrote
const FOUND = /^loop-found: /m;                         // in a loop-release: comment: the attempt filed real defects
const GROUP = { done: 'done', 'in-progress': 'in progress', ready: 'ready', blocked: 'blocked', 'needs-human': 'needs human' };
const NOW = process.env.EPIC_LOOP_NOW ? new Date(process.env.EPIC_LOOP_NOW) : new Date();

class Usage extends Error {}
class Refused extends Error {}

// ---------- gh

// Tests replace the CLI with a module (EPIC_LOOP_GH_MODULE) that exports gh(args) -> stdout.
const fake = process.env.EPIC_LOOP_GH_MODULE ? await import(pathToFileURL(resolve(process.env.EPIC_LOOP_GH_MODULE)).href) : null;

function gh(args) {
  try {
    if (fake) return String(fake.gh(args)).trim();
    return execFileSync('gh', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], maxBuffer: 64 * 1024 * 1024 }).trim();
  } catch (e) {
    const why = String(e.stderr || e.message || e).trim().split(/\r?\n/).slice(0, 3).join(' | ');
    throw new Error(`gh ${args.slice(0, 2).join(' ')} failed: ${why}`);
  }
}

function graphql(query, vars) {
  const args = ['api', 'graphql', '-f', `query=${query}`];
  for (const [k, v] of Object.entries(vars)) args.push(typeof v === 'number' ? '-F' : '-f', `${k}=${v}`);
  const out = JSON.parse(gh(args));
  if (out.errors?.length) throw new Error(out.errors.map((e) => e.message).join('; '));
  return out.data;
}

let tmp = null;
function bodyFile(text) {
  tmp ??= mkdtempSync(join(tmpdir(), 'epic-loop-'));
  const f = join(tmp, `body-${Math.random().toString(36).slice(2)}.md`);
  writeFileSync(f, text, 'utf8');
  return f;
}
process.on('exit', () => { if (tmp) rmSync(tmp, { recursive: true, force: true }); });

// ---------- reading the graph

const FIELDS = `number title state url repository { nameWithOwner } labels(first: 50) { nodes { name } } subIssuesSummary { total }
  blockedBy(first: 50) { nodes { number title state repository { nameWithOwner } } }
  blocking(first: 50) { nodes { number title state repository { nameWithOwner } } }`;
const Q_NODE = `query EpicNode($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) {
  issue(number: $number) { ${FIELDS} parent { number repository { nameWithOwner } } subIssues(first: 100) { nodes { ${FIELDS} } } } } }`;
const Q_PARENT = `query Parent($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) {
  issue(number: $number) { number repository { nameWithOwner } parent { number repository { nameWithOwner } } } } }`;
const Q_LABEL_EVENTS = `query LabelEvents($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) {
  issue(number: $number) { timelineItems(itemTypes: [LABELED_EVENT], last: 50) { nodes { __typename ... on LabeledEvent { createdAt label { name } } } } } } }`;
const Q_COMMENTS = `query Comments($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) {
  issue(number: $number) { comments(last: 100) { nodes { body } } } } }`;
const Q_CLOSING = `query Closing($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) {
  issue(number: $number) { number state labels(first: 50) { nodes { name } }
    closedByPullRequestsReferences(first: 10, includeClosedPrs: true) { nodes { number url merged title body } } } } }`;

function issueQuery(query, repo, number) {
  const [owner, name] = repo.split('/');
  const issue = graphql(query, { owner, name, number: Number(number) })?.repository?.issue;
  if (!issue) throw new Error(`${repo}#${number} not found`);
  return issue;
}

const keyOf = (x) => `${x.repo}#${x.number}`;
const ref = (r) => ({ repo: r.repository.nameWithOwner, number: r.number, title: r.title, open: r.state === 'OPEN' });
const hasAny = (labels, names) => labels.some((l) => names.includes(l));

function shape(raw) {
  return {
    repo: raw.repository.nameWithOwner, number: raw.number, title: raw.title, url: raw.url, open: raw.state === 'OPEN',
    labels: raw.labels.nodes.map((l) => l.name), hasChildren: raw.subIssuesSummary.total > 0,
    blockedBy: raw.blockedBy.nodes.map(ref), blocking: raw.blocking.nodes.map(ref), children: [],
  };
}

function loadTree(repo, number, seen = new Set()) {
  const raw = issueQuery(Q_NODE, repo, number);
  const node = shape(raw);
  seen.add(keyOf(node));
  for (const c of raw.subIssues.nodes) {
    const child = shape(c);
    if (seen.has(keyOf(child))) continue;
    node.children.push(child.hasChildren ? loadTree(child.repo, child.number, seen) : child);
  }
  return node;
}

// A parent is finished when it has sub-issues and every one of them is finished; a task is finished when closed.
function finished(node) { return node.children.length ? node.children.every(finished) : !node.open; }

// Sort every task into one group. Labels and blockers of a parent apply to everything under it, and a finished
// parent that nobody closed yet no longer blocks anything (a done phase must not hold back the next one).
function classify(root) {
  const done = new Map();
  (function mark(n) { n.children.forEach(mark); if (n.children.length) done.set(keyOf(n), finished(n)); })(root);
  const closed = (r) => !r.open || done.get(keyOf(r)) === true;
  const tasks = [], parents = [], holders = [];
  (function walk(node, inherited, path) {
    const held = node.open && hasAny(node.labels, HELD);
    const human = inherited.human || held;
    // what holds it, for status: a label other than the loop's own, or the parent that carries the hold
    const hold = held ? (node.labels.find((l) => HELD.includes(l) && !HUMAN.includes(l)) ?? null) : inherited.hold;
    const blockers = [...inherited.blockers, ...node.blockedBy.filter((b) => !closed(b))];
    if (node.open && hasAny(node.labels, WORK_SEEN) && !held) holders.push(node);
    if (node !== root && !node.children.length) {
      let group = 'ready';
      if (!node.open) group = 'done';
      else if (human) group = 'needs-human';
      else if (hasAny(node.labels, WORK_SEEN)) group = 'in-progress';
      else if (blockers.length) group = 'blocked';
      const openBlockers = [...new Map(blockers.map((b) => [keyOf(b), b])).values()];
      tasks.push({ ...node, group, hold, order: tasks.length, path, openBlockers, unblocks: node.blocking.filter((b) => b.open).length });
      return;
    }
    if (node !== root) parents.push(node);
    for (const c of node.children) walk(c, { human, blockers, hold: held ? `via #${node.number}` : inherited.hold }, [...path, node.number]);
  })(root, { human: false, blockers: [], hold: null }, []);
  return { tasks, parents, holders, done };
}

function labeledAt(repo, number) {
  const nodes = issueQuery(Q_LABEL_EVENTS, repo, number).timelineItems.nodes;
  const last = nodes.filter((e) => e.__typename === 'LabeledEvent' && WORK_SEEN.includes(e.label?.name)).pop();
  return last ? new Date(last.createdAt) : null;
}
const hoursSince = (d) => (d ? Math.round(((NOW - d) / 3600e3) * 10) / 10 : null);
// Attempts that ended by filing a real defect (release --found) are not failures of the task, so they do not count.
function attemptsOf(repo, number) {
  const bodies = issueQuery(Q_COMMENTS, repo, number).comments.nodes.map((c) => c.body);
  const tries = bodies.filter((b) => ATTEMPT.test(b)).length;
  const excused = bodies.filter((b) => b.startsWith('loop-release:') && FOUND.test(b)).length;
  return Math.max(0, tries - excused);
}

function counts(tasks) {
  const n = (g) => tasks.filter((t) => t.group === g).length;
  return { total: tasks.length, done: n('done'), inProgress: n('in-progress'), ready: n('ready'), blocked: n('blocked'), needsHuman: n('needs-human') };
}

// The decision of one run. Only `take` lets the run work; every other action ends the run.
function decide(root, c, { staleHours, closedNow = [], parallel = false }) {
  const base = { epic: root.number, counts: counts(c.tasks), closed: closedNow };
  const brief = (t) => ({ issue: t.number, title: t.title, blockedBy: t.openBlockers.map((b) => b.number) });
  if (!root.open && finished(root)) return { action: 'done', ...base };
  if (!c.tasks.length) return { action: 'blocked', reason: `epic #${root.number} has no sub-issues`, ...base };
  const ready = c.tasks.filter((t) => t.group === 'ready').sort((a, b) => b.unblocks - a.unblocks || a.order - b.order);
  if (parallel && ready.length) {
    const t = ready[0];
    return { action: 'take', issue: t.number, title: t.title, attempt: attemptsOf(t.repo, t.number) + 1, path: [...t.path, t.number],
      ready: ready.map((x) => ({ issue: x.number, title: x.title })), inProgress: c.holders.map((h) => h.number), ...base };
  }
  if (c.holders.length) {
    const h = c.holders[0];
    const since = labeledAt(h.repo, h.number);
    const hours = hoursSince(since);
    return { action: 'skip', reason: 'in progress', issue: h.number, title: h.title, since: since?.toISOString() ?? null, hours, stale: hours !== null && hours > staleHours, ...base };
  }
  if (ready.length) {
    const t = ready[0];
    return { action: 'take', issue: t.number, title: t.title, attempt: attemptsOf(t.repo, t.number) + 1, path: [...t.path, t.number], ...base };
  }
  const open = c.tasks.filter((t) => t.group !== 'done');
  if (!open.length) return { action: 'done', ...base };
  const gates = open.filter((t) => t.group === 'needs-human' && !t.openBlockers.length);
  if (gates.length) return { action: 'wait', reason: 'waiting for a human', issues: gates.map(brief), ...base };
  return { action: 'blocked', reason: 'every open task is blocked', issues: open.map(brief), ...base };
}

// ---------- writing

const labelCache = new Map();
function repoLabels(repo) {
  if (!labelCache.has(repo)) labelCache.set(repo, new Set(JSON.parse(gh(['label', 'list', '-R', repo, '--json', 'name', '--limit', '1000'])).map((l) => l.name)));
  return labelCache.get(repo);
}
const pickLabel = (repo, names) => names.find((n) => repoLabels(repo).has(n)) || names[0];
function ensureLabel(repo, names) {
  const name = pickLabel(repo, names);
  if (!repoLabels(repo).has(name)) {
    const s = STYLE[name] || { color: 'EDEDED', description: '' };
    gh(['label', 'create', name, '-R', repo, '--color', s.color, '--description', s.description]);
    repoLabels(repo).add(name);
  }
  return name;
}

const comment = (repo, n, text) => gh(['issue', 'comment', String(n), '-R', repo, '--body-file', bodyFile(text)]);
const closeIssue = (repo, n, why) => gh(['issue', 'close', String(n), '-R', repo, '--reason', 'completed', '--comment', why]);
const idCache = new Map();
function issueId(repo, n) {
  const k = `${repo}#${n}`;
  if (!idCache.has(k)) idCache.set(k, JSON.parse(gh(['api', `repos/${repo}/issues/${n}`])).id);
  return idCache.get(k);
}
const addSubIssue = (repo, parent, child) => gh(['api', '-X', 'POST', `repos/${repo}/issues/${parent}/sub_issues`, '-F', `sub_issue_id=${issueId(repo, child)}`]);
// Returns false when the link already existed (GitHub answers 422 "already been taken").
function addBlockedBy(repo, n, blocker) {
  try { gh(['api', '-X', 'POST', `repos/${repo}/issues/${n}/dependencies/blocked_by`, '-F', `issue_id=${issueId(repo, blocker)}`]); return true; }
  catch (e) { if (/already been taken/i.test(e.message)) return false; throw e; }
}
const removeBlockedBy = (repo, n, blocker) => gh(['api', '-X', 'DELETE', `repos/${repo}/issues/${n}/dependencies/blocked_by/${issueId(repo, blocker)}`]);

// Close every parent under (and including) `root` whose sub-issues are all finished.
function sweep(root) {
  const closed = [];
  (function walk(n) {
    n.children.filter((c) => c.children.length).forEach(walk);
    if (n.children.length && n.open && finished(n)) { closeIssue(n.repo, n.number, 'All sub-issues are closed.'); n.open = false; closed.push(n.number); }
  })(root);
  return closed;
}

function closeFinishedAncestors(repo, number) {
  const closed = [];
  let cur = { repo, number };
  for (let depth = 0; depth < 10; depth++) {
    const p = issueQuery(Q_PARENT, cur.repo, cur.number).parent;
    if (!p) break;
    const parent = loadTree(p.repository.nameWithOwner, p.number);
    if (!finished(parent)) break;
    if (parent.open) { closeIssue(parent.repo, parent.number, 'All sub-issues are closed.'); closed.push(parent.number); }
    cur = parent;
  }
  return closed;
}

function topAncestor(repo, number) {
  let cur = { repo, number };
  for (let depth = 0; depth < 10; depth++) {
    const p = issueQuery(Q_PARENT, cur.repo, cur.number).parent;
    if (!p) break;
    cur = { repo: p.repository.nameWithOwner, number: p.number };
  }
  return cur;
}

// ---------- commands

const fmt = (r, repo) => (r.repo === repo ? `#${r.number}` : `${r.repo}#${r.number}`);

function describe(d, repo) {
  if (d.action === 'take') return `take #${d.issue} ${d.title} (attempt ${d.attempt})`;
  if (d.action === 'skip') return `skip, #${d.issue} is in progress${d.hours !== null ? ` (${d.hours}h${d.stale ? ', stale' : ''})` : ''}`;
  if (d.action === 'wait') return `wait for a human: ${d.issues.map((i) => `#${i.issue} ${i.title}`).join('; ')}`;
  if (d.action === 'blocked') return `blocked: ${d.reason}`;
  return `done${d.closed.length ? ` (closed ${d.closed.map((n) => `#${n}`).join(', ')})` : ''}`;
}

function cmdStatus(repo, n, flags) {
  const root = loadTree(repo, n);
  const c = classify(root);
  for (const t of c.tasks) if (t.group === 'in-progress') t.hours = hoursSince(labeledAt(t.repo, t.number));
  const d = decide(root, c, { staleHours: Number(flags['stale-hours'] ?? 6) });
  if (flags.json) {
    const tasks = c.tasks.map((t) => ({ issue: t.number, title: t.title, group: t.group, blockedBy: t.openBlockers.map((b) => b.number), path: t.path }));
    console.log(JSON.stringify({ epic: n, tasks, counts: d.counts, next: d }));
    return;
  }
  const byKey = new Map(c.tasks.map((t) => [keyOf(t), t]));
  const lines = [];
  (function walk(node, depth) {
    const pad = '  '.repeat(depth);
    if (node === root || node.children.length) {
      const leaves = []; (function collect(x) { if (x.children.length) x.children.forEach(collect); else leaves.push(x); })(node);
      const blockers = node.blockedBy.filter((b) => b.open);
      lines.push(`${pad}#${node.number} ${node.title}  [${node.open ? 'open' : 'closed'}, ${leaves.filter((l) => !l.open).length}/${leaves.length} done` +
        `${blockers.length ? `, blocked by ${blockers.map((b) => fmt(b, repo)).join(', ')}` : ''}]`);
      node.children.forEach((ch) => walk(ch, depth + 1));
      return;
    }
    const t = byKey.get(keyOf(node));
    let extra = '';
    if (t.group === 'blocked') extra = ` (by ${t.openBlockers.map((b) => fmt(b, repo)).join(', ')})`;
    if (t.group === 'in-progress' && t.hours !== null) extra = ` (${t.hours}h)`;
    if (t.group === 'needs-human' && t.hold) extra = ` (${t.hold})`;
    lines.push(`${pad}${fmt(t, repo).padEnd(6)} ${GROUP[t.group].padEnd(12)} ${t.title}${extra}`);
  })(root, 0);
  const k = d.counts;
  lines.push('', `done ${k.done} | in progress ${k.inProgress} | ready ${k.ready} | blocked ${k.blocked} | needs human ${k.needsHuman}`, `next: ${describe(d, repo)}`);
  console.log(lines.join('\n'));
}

function cmdNext(repo, n, flags) {
  const root = loadTree(repo, n);
  const c = classify(root);
  const closedNow = flags['dry-run'] ? [] : sweep(root);
  console.log(JSON.stringify(decide(root, c, { staleHours: Number(flags['stale-hours'] ?? 6), closedNow, parallel: Boolean(flags.parallel) })));
}

function cmdClaim(repo, n, flags) {
  const top = flags.epic ? { repo, number: num(flags.epic, '--epic <n>') } : topAncestor(repo, n);
  const root = loadTree(top.repo, top.number);
  const c = classify(root);
  const t = c.tasks.find((x) => x.repo === repo && x.number === n);
  if (!t) {
    const isParent = (top.number === n && top.repo === repo) || c.parents.some((p) => p.repo === repo && p.number === n);
    throw new Refused(isParent ? `#${n} is not a task: it has sub-issues` : `#${n} is not under epic #${root.number}`);
  }
  const other = c.holders.find((h) => keyOf(h) !== keyOf(t));
  if (other && !flags.parallel) throw new Refused(`#${other.number} is in progress; an epic runs one task at a time (--parallel runs lanes side by side)`);
  if (t.group === 'blocked') throw new Refused(`#${n} is blocked (by ${t.openBlockers.map((b) => fmt(b, repo)).join(', ')})`);
  if (t.group === 'needs-human') throw new Refused(`#${n} needs human: a loop never takes it`);
  if (t.group === 'in-progress') throw new Refused(`#${n} is in progress already`);
  if (t.group === 'done') throw new Refused(`#${n} is closed`);
  const attempts = attemptsOf(repo, n);
  const max = Number(flags['max-attempts'] ?? 3);
  if (attempts >= max) {
    const label = ensureLabel(repo, HUMAN);
    gh(['issue', 'edit', String(n), '-R', repo, '--add-label', label]);
    comment(repo, n, `loop-gave-up: ${attempts} attempts did not finish this task. It now waits for a human (\`${label}\`); read the loop-release comments above for what went wrong.`);
    console.log(JSON.stringify({ claimed: false, gaveUp: true, issue: n, attempts }));
    process.exitCode = 1;
    return;
  }
  gh(['issue', 'edit', String(n), '-R', repo, '--add-label', ensureLabel(repo, WORK)]);
  comment(repo, n, `loop-attempt: ${attempts + 1}${flags.note ? `\n\n${flags.note}` : ''}`);
  console.log(JSON.stringify({ claimed: true, issue: n, attempt: attempts + 1, epic: root.number }));
}

function cmdRelease(repo, n, flags) {
  if (!flags.reason) throw new Usage('release needs --reason "<why>"');
  const found = flags.found.map((v) => num(v, '--found <n>'));
  if (found.includes(n)) throw new Usage(`--found names #${n} itself`);
  const parents = found.map((f) => issueQuery(Q_PARENT, repo, f).parent);   // also proves each found issue exists
  const info = issueQuery(Q_CLOSING, repo, n);
  const reopened = info.state !== 'OPEN';
  if (reopened) gh(['issue', 'reopen', String(n), '-R', repo]);
  for (const l of info.labels.nodes.map((x) => x.name).filter((x) => WORK_SEEN.includes(x))) gh(['issue', 'edit', String(n), '-R', repo, '--remove-label', l]);
  let label = null;
  if (flags['needs-human']) { label = ensureLabel(repo, HUMAN); gh(['issue', 'edit', String(n), '-R', repo, '--add-label', label]); }
  if (found.length) {
    const epic = topAncestor(repo, n);
    found.forEach((f, i) => { if (!parents[i] && !(epic.repo === repo && epic.number === n)) addSubIssue(repo, epic.number, f); addBlockedBy(repo, n, f); });
  }
  const foundLine = found.length ? `loop-found: ${found.map((f) => `#${f}`).join(', ')} (each now blocks this task; this attempt does not count toward the attempt limit)\n\n` : '';
  comment(repo, n, `loop-release: ${flags.reason}\n\n${foundLine}${label ? `Waiting for a human (\`${label}\`): a loop will not take this task until that label is removed.` : 'Back in the queue: the next run can take it again.'}`);
  console.log(JSON.stringify({ released: true, issue: n, needsHuman: Boolean(label), reopened, ...(found.length ? { found } : {}) }));
}

function verifySection(body) {
  const lines = String(body || '').split(/\r?\n/);
  const i = lines.findIndex((l) => /^#{2,3}\s+Verify\b/i.test(l));
  if (i < 0) return null;
  const rest = lines.slice(i + 1);
  const end = rest.findIndex((l) => /^#{1,2}\s/.test(l));
  return (end < 0 ? rest : rest.slice(0, end)).join('\n');
}

function fileOnDefaultBranch(repo, path) {
  try {
    const f = JSON.parse(gh(['api', `repos/${repo}/contents/${path.split('/').map(encodeURIComponent).join('/')}`]));
    return Buffer.from(f.content || '', f.encoding === 'base64' ? 'base64' : 'utf8').toString('utf8');
  } catch { return null; }
}

function cmdCheck(repo, n, flags) {
  if (flags.evidence && !flags.heading) throw new Usage('--evidence needs --heading "<text>"');
  const info = issueQuery(Q_CLOSING, repo, n);
  const fails = [];
  if (info.state !== 'CLOSED') fails.push('the issue is not closed');
  const pr = info.closedByPullRequestsReferences.nodes.find((p) => p.merged);
  if (!pr) fails.push('no merged pull request closes it (the PR body needs "Closes #' + n + '")');
  else {
    const v = verifySection(pr.body);
    if (v === null) fails.push(`PR #${pr.number} has no "## Verify" section`);
    else for (const w of ['Red', 'Green', 'Stub']) if (!new RegExp(`\\b${w}\\b`, 'i').test(v)) fails.push(`PR #${pr.number}: its Verify section does not mention ${w}`);
  }
  if (flags.evidence) {
    const text = fileOnDefaultBranch(repo, flags.evidence);
    const h = flags.heading;
    if (text === null) fails.push(`the evidence log ${flags.evidence} is not on the default branch`);
    else if (!text.split(/\r?\n/).some((l) => l === h || (l.startsWith(h) && /^\W/.test(l.slice(h.length))))) fails.push(`the evidence log ${flags.evidence} has no "${h}" heading`);
  }
  if (fails.length) {
    console.log(`FAIL #${n}\n- ${fails.join('\n- ')}`);
    process.exitCode = 1;
    return;
  }
  const out = [`PASS #${n} via PR #${pr.number} (${pr.url})`];
  if (!flags['dry-run']) {
    for (const l of info.labels.nodes.map((x) => x.name).filter((x) => WORK_SEEN.includes(x))) gh(['issue', 'edit', String(n), '-R', repo, '--remove-label', l]);
    comment(repo, n, `loop-check: PASS via PR #${pr.number}.`);
    for (const p of closeFinishedAncestors(repo, n)) out.push(`closed #${p}: all its sub-issues are closed`);
  }
  console.log(out.join('\n'));
}

function cmdLink(repo, n, flags) {
  if (!flags.parent && !flags['blocked-by'].length && !flags.blocks.length) throw new Usage('link needs --parent, --blocked-by or --blocks');
  const linked = [];
  if (flags.parent) { addSubIssue(repo, num(flags.parent, '--parent <n>'), n); linked.push(`#${n} is a sub-issue of #${flags.parent}`); }
  const already = (added) => (added ? '' : ' (already)');
  for (const b of flags['blocked-by']) linked.push(`#${n} is blocked by #${b}${already(addBlockedBy(repo, n, num(b, '--blocked-by <n>')))}`);
  for (const x of flags.blocks) linked.push(`#${x} is blocked by #${n}${already(addBlockedBy(repo, num(x, '--blocks <n>'), n))}`);
  console.log(JSON.stringify({ linked }));
}

function cmdUnlink(repo, n, flags) {
  if (!flags['blocked-by'].length && !flags.blocks.length) throw new Usage('unlink needs --blocked-by or --blocks');
  const unlinked = [];
  for (const b of flags['blocked-by']) { removeBlockedBy(repo, n, num(b, '--blocked-by <n>')); unlinked.push(`#${n} is no longer blocked by #${b}`); }
  for (const x of flags.blocks) { removeBlockedBy(repo, num(x, '--blocks <n>'), n); unlinked.push(`#${x} is no longer blocked by #${n}`); }
  console.log(JSON.stringify({ unlinked }));
}

// The copy of one plan section: from its heading ("## L0 - Title") up to the next heading of the same or a higher level.
function planSection(text, id, where) {
  const lines = text.split(/\r?\n/);
  const esc = id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const start = lines.findIndex((l) => new RegExp(`^#{2,4}\\s+${esc}\\b`).test(l));
  if (start < 0) throw new Usage(`no section "${id}" in ${where} (expected a heading like "## ${id} - <title>")`);
  const level = /^#+/.exec(lines[start])[0].length;
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) { const h = /^(#+)\s/.exec(lines[i]); if (h && h[1].length <= level) { end = i; break; } }
  const sec = lines.slice(start, end);
  while (sec.length && /^\s*(-{3,})?\s*$/.test(sec.at(-1))) sec.pop();
  return sec.join('\n');
}

function cmdCreate(manifestPath, flags) {
  const file = resolve(manifestPath);
  const base = dirname(file);
  const m = JSON.parse(readFileSync(file, 'utf8'));
  const repo = flags.repo || m.repo || currentRepo();
  const dry = Boolean(flags['dry-run']);
  if (!m.epic || (!m.epic.number && !m.epic.title)) throw new Usage('the manifest needs "epic": { "title", "body" } or { "number" }');
  const read = (p) => readFileSync(isAbsolute(p) ? p : join(base, p), 'utf8');
  let branch = null;
  const link = (p) => `[${p}](https://github.com/${repo}/blob/${(branch ??= defaultBranch(repo))}/${p})`;
  const epicKey = m.epic.key || 'epic';
  const chain = m.chain !== false;

  const items = [{
    key: epicKey, kind: 'epic', number: m.epic.number ? Number(m.epic.number) : null, title: m.epic.title, parent: null, blockedBy: [],
    body: m.epic.number ? '' : m.epic.body ? read(m.epic.body)
      : `Sub-issues are the tasks, chained with "blocked by"; the labels \`loop:in-progress\` and \`loop:needs-human\` hold the state.\n\n## Loop prompt\n\n` +
        `\`\`\`text\nRun one tick of the epic loop on epic {{#${epicKey}}} of ${repo}, using the epic-loop skill. If that skill is not available, change nothing and reply SKILL MISSING.\n\`\`\`\n`,
  }];
  const groups = m.phases ? m.phases.map((p) => ({ phase: p, tasks: p.tasks || [] })) : [{ phase: null, tasks: m.tasks || [] }];
  let prevTask = null, prevPhase = null;
  for (const { phase, tasks } of groups) {
    const ctx = { spec: phase?.spec ?? m.spec, plan: phase?.plan ?? m.plan, evidence: phase?.evidence ?? m.evidence };
    let parentKey = epicKey;
    if (phase) {
      if (!phase.key || !phase.title) throw new Usage('every phase needs "key" and "title"');
      const docs = [ctx.spec && `Spec: ${link(ctx.spec)}`, ctx.plan && `plan: ${link(ctx.plan)}`].filter(Boolean).join(', ');
      const body = phase.body ? read(phase.body)
        : `Phase of epic {{#${epicKey}}}.${docs ? ` ${docs}.` : ''}\n\nThe tasks are sub-issues of this issue, chained with "blocked by" so they run in order. This issue is closed when every task is closed.\n`;
      prevPhase = { key: phase.key, kind: 'phase', title: phase.title, body, parent: epicKey, blockedBy: phase.blockedBy ?? (chain && prevPhase ? [prevPhase.key] : []) };
      items.push(prevPhase);
      parentKey = phase.key;
    }
    for (const t of tasks) {
      if (!t.key || !t.title) throw new Usage('every task needs "key" and "title"');
      let body;
      if (t.body) body = read(t.body);
      else {
        const where = phase ? `${phase.title} ({{#${phase.key}}}, epic {{#${epicKey}}})` : `epic {{#${epicKey}}}`;
        const docs = [ctx.spec && `Spec: ${link(ctx.spec)}`, (t.plan ?? ctx.plan) && `plan: ${link(t.plan ?? ctx.plan)}`].filter(Boolean).join(', ');
        const done = t.needsHuman
          ? 'Done means: the owner did what the section says and closed this issue. A loop never works on this issue.'
          : `Done means: every Green command in the Verify block was run and matched${ctx.evidence ? `, the evidence log \`${ctx.evidence}\` has a \`## ${t.key}\` section with before and after numbers` : ''}, and the merged pull request says \`Closes #<this issue>\` and has a \`## Verify\` section with Red, Green and Stub check.`;
        body = `Task **${t.key}** of ${where}.${docs ? ` ${docs}.` : ''}${t.section ? ' The section below is copied from the plan; the plan file wins if they differ.' : ''}\n\n${done}\n`;
      }
      if (t.section) {
        const plan = t.plan ?? ctx.plan;
        if (!plan) throw new Usage(`task ${t.key}: "section" needs a "plan" (repository-relative path)`);
        body += `\n---\n\n${planSection(readFileSync(resolve(plan), 'utf8'), t.section, plan)}\n`;
      }
      prevTask = { key: t.key, kind: 'task', title: t.title, body, parent: parentKey, needsHuman: Boolean(t.needsHuman), blockedBy: t.blockedBy ?? (chain && prevTask ? [prevTask.key] : []) };
      items.push(prevTask);
    }
  }

  // Check every reference before writing anything.
  const keys = new Set();
  for (const it of items) { if (keys.has(it.key)) throw new Usage(`duplicate key "${it.key}"`); keys.add(it.key); }
  for (const it of items) {
    for (const b of it.blockedBy) if (typeof b !== 'number' && !keys.has(b)) throw new Usage(`${it.key}: blockedBy "${b}" is not a key of this manifest`);
    for (const [, k] of it.body.matchAll(/\{\{#([\w.-]+)\}\}/g)) if (!keys.has(k)) throw new Usage(`${it.key}: the body refers to {{#${k}}}, which is not a key of this manifest`);
  }

  const topics = m.labels || [];
  const human = items.some((i) => i.needsHuman) ? (dry ? pickLabel(repo, HUMAN) : ensureLabel(repo, HUMAN)) : null;
  if (!dry) topics.forEach((l) => ensureLabel(repo, [l]));
  const numbers = new Map();
  const say = [];
  const fill = (text) => text.replace(/\{\{#([\w.-]+)\}\}/g, (all, k) => (numbers.has(k) ? `#${numbers.get(k)}` : all));
  const later = [];
  for (const it of items) {
    if (it.number) { numbers.set(it.key, it.number); say.push(`use #${it.number} as ${it.key}`); continue; }
    const labels = [...topics, ...(it.needsHuman ? [human] : [])];
    say.push(`create ${it.key} "${it.title}"${labels.length ? ` [${labels.join(', ')}]` : ''}`);
    if (dry) continue;
    const body = fill(it.body);
    const url = gh(['issue', 'create', '-R', repo, '--title', it.title, '--body-file', bodyFile(body), ...labels.flatMap((l) => ['--label', l])]);
    numbers.set(it.key, Number(url.trim().split('/').pop()));
    if (/\{\{#[\w.-]+\}\}/.test(body)) later.push(it);
  }
  const no = (k) => (typeof k === 'number' ? k : numbers.get(k));
  for (const it of items) if (it.parent) { say.push(`link ${it.key} under ${it.parent}`); if (!dry) addSubIssue(repo, no(it.parent), no(it.key)); }
  for (const it of items) for (const b of it.blockedBy) { say.push(`block ${it.key} by ${b}`); if (!dry) addBlockedBy(repo, no(it.key), no(b)); }
  for (const it of later) gh(['issue', 'edit', String(no(it.key)), '-R', repo, '--body-file', bodyFile(fill(it.body))]);
  if (dry) { console.log(say.join('\n')); console.log(JSON.stringify({ dryRun: true, issues: items.length - (items[0].number ? 1 : 0) })); return; }
  console.log(JSON.stringify({ epic: numbers.get(epicKey), issues: Object.fromEntries(items.map((i) => [i.key, numbers.get(i.key)])) }));
}

// ---------- main

const currentRepo = () => JSON.parse(gh(['repo', 'view', '--json', 'nameWithOwner'])).nameWithOwner;
const defaultBranch = (repo) => JSON.parse(gh(['repo', 'view', repo, '--json', 'defaultBranchRef'])).defaultBranchRef.name;

function num(v, what) {
  if (!/^\d+$/.test(String(v ?? '').replace(/^#/, ''))) throw new Usage(`expected ${what}, got ${v === undefined ? 'nothing' : JSON.stringify(v)}`);
  return Number(String(v).replace(/^#/, ''));
}

function parse(argv) {
  const flags = { 'blocked-by': [], blocks: [], found: [] };
  const pos = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '-R' || a === '--repo') { flags.repo = argv[++i]; continue; }
    if (!a.startsWith('--')) { pos.push(a); continue; }
    const eq = a.indexOf('=');
    const name = eq > 0 ? a.slice(2, eq) : a.slice(2);
    if (['dry-run', 'json', 'needs-human', 'parallel'].includes(name)) { flags[name] = true; continue; }
    const value = eq > 0 ? a.slice(eq + 1) : argv[++i];
    if (value === undefined) throw new Usage(`--${name} needs a value`);
    if (name === 'blocked-by' || name === 'blocks' || name === 'found') flags[name].push(...value.split(',').map((v) => v.trim()).filter(Boolean));
    else flags[name] = value;
  }
  if (flags.repo !== undefined && !/^[\w.-]+\/[\w.-]+$/.test(flags.repo)) throw new Usage(`-R needs owner/name, got ${flags.repo}`);
  return { flags, pos };
}

const USAGE = 'usage: epic.mjs status <epic> [--json] | next <epic> [--dry-run] [--parallel] | claim <task> [--epic <n>] [--note "..."] [--parallel] | ' +
  'release <task> --reason "..." [--needs-human] [--found <n,...>] | check <task> [--dry-run] [--evidence <path> --heading "..."] | ' +
  'link <issue> [--parent <n>] [--blocked-by <n,...>] [--blocks <n,...>] | unlink <issue> [--blocked-by <n,...>] [--blocks <n,...>] | ' +
  'create <manifest.json> [--dry-run]   (options: -R owner/name, --stale-hours, --max-attempts)';

try {
  const { flags, pos } = parse(process.argv.slice(2));
  const [cmd, arg] = pos;
  const repo = () => flags.repo || currentRepo();
  switch (cmd) {
    case 'status': { const n = num(arg, 'an epic number'); cmdStatus(repo(), n, flags); break; }
    case 'next': { const n = num(arg, 'an epic number'); cmdNext(repo(), n, flags); break; }
    case 'claim': { const n = num(arg, 'a task number'); cmdClaim(repo(), n, flags); break; }
    case 'release': { const n = num(arg, 'a task number'); cmdRelease(repo(), n, flags); break; }
    case 'check': { const n = num(arg, 'a task number'); cmdCheck(repo(), n, flags); break; }
    case 'link': { const n = num(arg, 'an issue number'); cmdLink(repo(), n, flags); break; }
    case 'unlink': { const n = num(arg, 'an issue number'); cmdUnlink(repo(), n, flags); break; }
    case 'create': { if (!arg) throw new Usage('create needs a manifest file'); cmdCreate(arg, flags); break; }
    default: throw new Usage(USAGE);
  }
} catch (e) {
  console.error(e.message);
  process.exitCode = e instanceof Refused ? 1 : 2;
}
