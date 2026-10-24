// A fake `gh` for the epic.mjs tests. epic.mjs loads this module instead of running the real CLI when the environment
// variable EPIC_LOOP_GH_MODULE points here. The fake GitHub lives in the JSON file EPIC_LOOP_FAKE_STATE (read and
// written on every call, because each test runs epic.mjs as a child process); every call is appended to
// EPIC_LOOP_FAKE_LOG as one JSON line, so tests can assert which writes happened.
//
// State shape:
//   { repo, defaultBranch, nextNumber, labels: [name], files: { path: text },
//     issues: { "<n>": { title, body, state: "OPEN"|"CLOSED", labels: [name], parent: n|null, children: [n],
//                       blockedBy: [n], comments: [body], labelEvents: [{ label, createdAt, type? }],
//                       closedBy: [{ number, url, merged, title, body }] } } }
import { appendFileSync, readFileSync, writeFileSync } from 'node:fs';

const STATE = process.env.EPIC_LOOP_FAKE_STATE;
const LOG = process.env.EPIC_LOOP_FAKE_LOG;
const now = () => process.env.EPIC_LOOP_NOW || new Date().toISOString();

export function gh(args) {
  if (LOG) appendFileSync(LOG, JSON.stringify(args) + '\n');
  const s = JSON.parse(readFileSync(STATE, 'utf8'));
  const out = run(s, args);
  writeFileSync(STATE, JSON.stringify(s, null, 2));
  return out;
}

function fail(msg) { const e = new Error(msg); e.stderr = msg; throw e; }
function opt(args, name) { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : undefined; }
function opts(args, name) { const v = []; args.forEach((a, i) => { if (a === name) v.push(args[i + 1]); }); return v; }
function issue(s, n) { const i = s.issues[String(n)]; if (!i) fail(`GraphQL: Could not resolve to an issue with the number of ${n}.`); return i; }
const id = (n) => 900000 + Number(n);
const byId = (s, v) => Number(v) - 900000;

function ref(s, n) {
  const i = issue(s, n);
  return { number: Number(n), title: i.title, state: i.state, repository: { nameWithOwner: s.repo } };
}
function node(s, n) {
  const i = issue(s, n);
  const blocking = Object.entries(s.issues).filter(([, x]) => (x.blockedBy || []).includes(Number(n))).map(([k]) => ref(s, k));
  return {
    number: Number(n), title: i.title, state: i.state, url: `https://github.com/${s.repo}/issues/${n}`,
    repository: { nameWithOwner: s.repo },
    labels: { nodes: (i.labels || []).map((name) => ({ name })) },
    subIssuesSummary: { total: (i.children || []).length },
    blockedBy: { nodes: (i.blockedBy || []).map((b) => ref(s, b)) },
    blocking: { nodes: blocking },
  };
}

function graphql(s, args) {
  const q = args.find((a) => a.startsWith('query=')).slice(6);
  const op = /query\s+(\w+)/.exec(q)[1];
  const vars = {};
  args.forEach((a, i) => { if ((a === '-f' || a === '-F') && !args[i + 1].startsWith('query=')) { const [k, ...v] = args[i + 1].split('='); vars[k] = v.join('='); } });
  if (`${vars.owner}/${vars.name}` !== s.repo) return JSON.stringify({ data: { repository: null }, errors: [{ message: `Could not resolve to a Repository with the name '${vars.owner}/${vars.name}'.` }] });
  const n = Number(vars.number);
  const i = issue(s, n);
  const parent = i.parent ? { number: i.parent, repository: { nameWithOwner: s.repo } } : null;
  let data;
  if (op === 'EpicNode') data = { ...node(s, n), parent, subIssues: { nodes: (i.children || []).map((c) => node(s, c)) } };
  else if (op === 'Parent') data = { number: n, repository: { nameWithOwner: s.repo }, parent };
  else if (op === 'LabelEvents') {
    data = { timelineItems: { nodes: (i.labelEvents || []).map((e) => ({ __typename: e.type || 'LabeledEvent', createdAt: e.createdAt, label: { name: e.label } })) } };
  } else if (op === 'Comments') data = { comments: { nodes: (i.comments || []).map((body) => ({ body })) } };
  else if (op === 'Closing') {
    data = { number: n, state: i.state, labels: { nodes: (i.labels || []).map((name) => ({ name })) },
      closedByPullRequestsReferences: { nodes: i.closedBy || [] } };
  } else fail(`fake gh: unknown GraphQL operation ${op}`);
  return JSON.stringify({ data: { repository: { issue: data } } });
}

function api(s, args) {
  if (args[1] === 'graphql') return graphql(s, args);
  const method = opt(args, '-X') || 'GET';
  const path = args.find((a, i) => i > 0 && a.startsWith('repos/'));
  let m;
  if ((m = /^repos\/([^/]+\/[^/]+)\/issues\/(\d+)\/sub_issues$/.exec(path)) && method === 'POST') {
    const child = byId(s, opt(args, '-F').split('=')[1]);
    const p = issue(s, m[2]);
    if (issue(s, child).parent) fail('HTTP 422: Issue may not contain duplicate sub-issues / already has a parent');
    p.children = [...(p.children || []), child];
    issue(s, child).parent = Number(m[2]);
    return JSON.stringify({ id: id(m[2]), number: Number(m[2]) });
  }
  if ((m = /^repos\/([^/]+\/[^/]+)\/issues\/(\d+)\/dependencies\/blocked_by$/.exec(path)) && method === 'POST') {
    const blocker = byId(s, opt(args, '-F').split('=')[1]);
    const i = issue(s, m[2]);
    if ((i.blockedBy || []).includes(blocker)) fail('gh: Validation failed: Target issue has already been taken (HTTP 422)');
    i.blockedBy = [...(i.blockedBy || []), blocker];
    return JSON.stringify({ id: id(m[2]), number: Number(m[2]) });
  }
  if ((m = /^repos\/([^/]+\/[^/]+)\/issues\/(\d+)\/dependencies\/blocked_by\/(\d+)$/.exec(path)) && method === 'DELETE') {
    const blocker = byId(s, m[3]);
    const i = issue(s, m[2]);
    if (!(i.blockedBy || []).includes(blocker)) fail('HTTP 404: Not Found');
    i.blockedBy = i.blockedBy.filter((b) => b !== blocker);
    return '';
  }
  if ((m = /^repos\/([^/]+\/[^/]+)\/issues\/(\d+)$/.exec(path)) && method === 'GET') {
    issue(s, m[2]);
    return JSON.stringify({ id: id(m[2]), number: Number(m[2]) });
  }
  if ((m = /^repos\/([^/]+\/[^/]+)\/contents\/(.+)$/.exec(path)) && method === 'GET') {
    const text = (s.files || {})[m[2]];
    if (text === undefined) fail('HTTP 404: Not Found');
    return JSON.stringify({ encoding: 'base64', content: Buffer.from(text, 'utf8').toString('base64') });
  }
  fail(`fake gh: unknown api call ${method} ${path}`);
}

function issueCmd(s, args) {
  const sub = args[1];
  if (sub === 'create') {
    const n = s.nextNumber++;
    const labels = opts(args, '--label');
    for (const l of labels) if (!s.labels.includes(l)) fail(`could not add label: '${l}' not found`);
    s.issues[String(n)] = { title: opt(args, '--title'), body: readFileSync(opt(args, '--body-file'), 'utf8'), state: 'OPEN',
      labels, parent: null, children: [], blockedBy: [], comments: [], labelEvents: labels.map((label) => ({ label, createdAt: now() })) };
    return `https://github.com/${s.repo}/issues/${n}`;
  }
  const i = issue(s, args[2]);
  if (sub === 'edit') {
    for (const l of opts(args, '--add-label')) {
      if (!s.labels.includes(l)) fail(`'${l}' not found`);
      if (!i.labels.includes(l)) { i.labels.push(l); i.labelEvents = [...(i.labelEvents || []), { label: l, createdAt: now() }]; }
    }
    for (const l of opts(args, '--remove-label')) {
      if (i.labels.includes(l)) { i.labels = i.labels.filter((x) => x !== l); i.labelEvents = [...(i.labelEvents || []), { label: l, createdAt: now(), type: 'UnlabeledEvent' }]; }
    }
    const bf = opt(args, '--body-file');
    if (bf) i.body = readFileSync(bf, 'utf8');
    return '';
  }
  if (sub === 'comment') { i.comments = [...(i.comments || []), readFileSync(opt(args, '--body-file'), 'utf8')]; return ''; }
  if (sub === 'close') { i.state = 'CLOSED'; const c = opt(args, '--comment'); if (c) i.comments = [...(i.comments || []), c]; return ''; }
  if (sub === 'reopen') { i.state = 'OPEN'; return ''; }
  fail(`fake gh: unknown issue command ${sub}`);
}

function run(s, args) {
  const repoFlag = opt(args, '-R');
  if (repoFlag && repoFlag !== s.repo) fail(`fake gh: unknown repository ${repoFlag}`);
  if (args[0] === 'repo' && args[1] === 'view') {
    if (args.includes('defaultBranchRef')) return JSON.stringify({ defaultBranchRef: { name: s.defaultBranch || 'main' } });
    return JSON.stringify({ nameWithOwner: s.repo });
  }
  if (args[0] === 'api') return api(s, args);
  if (args[0] === 'issue') return issueCmd(s, args);
  if (args[0] === 'label' && args[1] === 'list') return JSON.stringify(s.labels.map((name) => ({ name })));
  if (args[0] === 'label' && args[1] === 'create') { if (!s.labels.includes(args[2])) s.labels.push(args[2]); return ''; }
  fail(`fake gh: unknown command ${args.join(' ')}`);
}
