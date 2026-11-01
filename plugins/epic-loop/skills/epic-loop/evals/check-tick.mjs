// Check for the tick evals: the reply's last line is the JSON of tick.md step 7 with the expected result (and issue),
// and the fake GitHub state is unchanged when the run was supposed to change nothing.
// --calls <log> --no-query <name>: the log of the fake gh (EPIC_LOOP_FAKE_LOG) must hold no GraphQL "query <name>", which
// shows that the run did not read the part of the graph it was told to skip.
// Usage: node check-tick.mjs --reply <file> --result <RESULT> [--issue <n>|null] [--state <file> --orig <file>]
//                            [--calls <log> --no-query <name>] [--pr <url>]
// --pr <url>: the pr field of the last line must equal the url.
import { readFileSync } from 'node:fs';

const args = process.argv.slice(2);
const opt = (name) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : undefined; };
const fail = (msg) => { console.error(msg); process.exit(1); };

const reply = readFileSync(opt('--reply'), 'utf8').trim();
const last = reply.split(/\r?\n/).map((l) => l.trim()).filter(Boolean).pop() || '';
let json;
try { json = JSON.parse(last.replace(/^`+|`+$/g, '')); } catch { fail(`the last line is not JSON: ${last.slice(0, 200)}`); }
if (json.result !== opt('--result')) fail(`result is ${json.result}, expected ${opt('--result')}`);
const issue = opt('--issue');
if (issue !== undefined && String(json.issue) !== issue) fail(`issue is ${json.issue}, expected ${issue}`);
if (opt('--pr') !== undefined && json.pr !== opt('--pr')) fail(`pr is ${json.pr}, expected ${opt('--pr')}`);
if (opt('--state')) {
  const now = readFileSync(opt('--state'), 'utf8');
  const was = readFileSync(opt('--orig'), 'utf8');
  if (JSON.stringify(JSON.parse(now).issues) !== JSON.stringify(JSON.parse(was).issues)) fail('the run changed the GitHub state');
}
if (opt('--no-query')) {
  const name = opt('--no-query');
  const log = readFileSync(opt('--calls'), 'utf8');
  if (new RegExp(`query ${name.replace(/\W/g, '')}\\b`).test(log)) fail(`the run logged query ${name}`);
}
console.log(`ok: ${last}`);
