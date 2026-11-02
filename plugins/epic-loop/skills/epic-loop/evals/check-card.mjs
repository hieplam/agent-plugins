// Check for the goal-writer evals: it compares the fake GitHub state after the run with the state before the run.
// Usage: node check-card.mjs --state <file> --orig <file> --cards <n> [--door two-way|one-way] [--closed <n,...>] [--unchanged <n,...>]
// --cards <n>: the run opened exactly n open issues with the label goal-card, each with one fenced json block that holds a valid card.
// --door: each new card has this door.
// --closed: each issue is closed and carries a loop-tidy: comment, so `E tidy` closed it.
// --unchanged: each issue is exactly as it was.
import { readFileSync } from 'node:fs';
import { cardProblems, jsonBlocks } from '../scripts/card.mjs';

const args = process.argv.slice(2);
const opt = (name) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : undefined; };
const list = (name) => (opt(name) ?? '').split(',').filter(Boolean);
const fail = (msg) => { console.error(msg); process.exit(1); };

const now = JSON.parse(readFileSync(opt('--state'), 'utf8')).issues;
const was = JSON.parse(readFileSync(opt('--orig'), 'utf8')).issues;

const added = Object.keys(now).filter((n) => !(n in was) && now[n].state === 'OPEN' && now[n].labels.includes('goal-card'));
if (added.length !== Number(opt('--cards'))) fail(`${added.length} new goal cards, expected ${opt('--cards')}`);
for (const n of added) {
  const blocks = jsonBlocks(now[n].body);
  if (blocks.length !== 1) fail(`#${n} has ${blocks.length} fenced json blocks, expected 1`);
  let card;
  try { card = JSON.parse(blocks[0]); } catch { fail(`#${n}: the block is not valid JSON`); }
  const problems = cardProblems(card);
  if (problems.length) fail(`#${n}: ${problems.join('; ')}`);
  if (opt('--door') && card.door !== opt('--door')) fail(`#${n}: door is ${card.door}, expected ${opt('--door')}`);
}
for (const n of list('--closed')) {
  const i = now[n];
  if (i?.state !== 'CLOSED' || !(i.comments ?? []).some((c) => c.startsWith('loop-tidy:'))) fail(`#${n} is not closed by tidy`);
}
for (const n of list('--unchanged')) if (JSON.stringify(now[n]) !== JSON.stringify(was[n])) fail(`#${n} changed`);
console.log(`ok: ${added.length} new cards, ${list('--closed').length} closed by tidy, ${list('--unchanged').length} unchanged`);
