# Barrier eval — would the reply have spared the owner a follow-up?

The `explaining` plugin's fixture (`plugins/explaining/evals/evals.json`) checks that a style
**follows its rules**: terms are defined, flows are drawn, long drafts get a blind reader. It runs on
hand-written questions about general topics. This eval asks a different question, on the owner's
own history: **in the moments where a reply really did not land, would this style have prevented
the follow-up the owner had to send?**

A *follow-up* is the owner's next message that shows a reply failed: "qmd là gì?" (what is qmd?),
"explain more with surrounding context", "giải thích lại bằng tiếng việt" (explain again in
Vietnamese), "done?". That moment — the message the assistant answered, what it could see, and the
follow-up — is a *case*.

## How it works

```
~/.claude/history.jsonl ─┐                       ┌─ reask case: the owner's message (+ a repo snapshot or files)
~/.claude/projects/*.jsonl ┴─ mine.py ─ person ─ build_cases.py ─┤
                               (candidates)  (picks,  (copies facts,  └─ replay case: the real conversation, the
                                              writes   scrubs secrets)    message, the tool results, the original reply
                                              the flaw)
cases ── run.py ──── one claude -p session per case × arm × run ── sessions/<case>/<arm>/run-N.json
      ── stubs.py ── one deliberately flawed reply per reask case ── sessions/<case>/stub/run-1.json
      ── judge.py ── one blind judge session per reply ─────────── verdicts/<judge>/<case>/<arm>/run-N.json
      ── report.py ─ gates, win rates, paired comparisons, cost ── report-<judge>.json / .md
      ── ratchet.py  the new report against the committed baseline: REGRESSION / HOLD / IMPROVED
```

- **Arm** — one setup every case runs under (`arms.json`): no output style, Todd way before Part C,
  Todd way with Part C. A style is pinned to a git commit of this repo, so an arm always means the
  same text.
- **Cell** — one case under one arm. With `--runs 3`, a cell wins when 2 of 3 runs win (the owner's
  own rule from the detection-eval design).
- **Win** — the judge says the owner would *not* still need that follow-up, the message is answered,
  and no claim is wrong.

## What keeps the numbers honest

| Guard | Why |
| --- | --- |
| The session under test sees only the message, the conversation, the tool results and the files (`core.ARM_VISIBLE`). `validate_case` refuses a case whose flaw, or long follow-up, appears in any of them. | The follow-up is the answer key. A session that can read it passes by copying. |
| Sessions run with `--setting-sources project`, a fresh folder, the owner's memory as project memory, and the arm's style selected in `.claude/settings.json`. `classify_session` rejects a session whose reported style or model is not its arm's (`isolation_breach`). | The owner's real settings select Todd way. Without this, the "no style" arm silently runs Todd way. Verified on 2026-10-03: the no-style session saw neither Part B nor Part C; the before-Part-C session saw Part B only. |
| Sessions have no shell and no web (`--tools Read,Glob,Grep,Write,Edit,Agent`), and every permission prompt is denied (`--permission-prompts none`). A repo is given as a `git archive` snapshot, with its own `.claude/` and `.mcp.json` removed. | A replayed "pr then merge" must not merge a real PR. The older harness's `bypassPermissions` let eval sessions run git in the owner's real repo (tribe #203). Verified: reads and writes outside the folder are denied, also from a subagent. |
| Every case is graded twice more: the judge must say "follow-up still needed" for a known-bad reply — the real original reply (replay) or a stub written to have exactly the flaw (reask). A case whose known-bad reply passes is not scored. | A lenient judge passes everything. This is the stub check of the oracle itself. |
| The judge sees one reply at a time, never an arm name, with `--safe-mode --tools ""` and a JSON schema. | Blind, structured, and unable to wander. |
| Replay cases come from the 10 sessions Part C was written from, so they are reported *in sample*; the verdict on Part C rests on the out-of-sample (reask) cases. | A rule tested on the cases it was fitted to measures memory, not clarity. |
| Do not add a case's words to the Todd way dictionary. | Same reason: the dictionary would be fitted to the test. |

## Running it

The cases are private (the owner's conversations) and live in the private `research` repo,
`raw/todd-way-barrier-eval/`. From this folder:

```bash
E=~/repos/research/raw/todd-way-barrier-eval
OUT=$E/runs/<date>-<label>
python3 run.py    --cases $E/cases --memory $E/owner-memory.md --out $OUT --runs 1 --jobs 3
python3 stubs.py  --cases $E/cases --out $OUT
python3 judge.py  --cases $E/cases --out $OUT --model claude-sonnet-5-5
python3 report.py --cases $E/cases --out $OUT --judge claude-sonnet-5-5
python3 ratchet.py --baseline $E/baseline/report-claude-sonnet-5-5.json --candidate $OUT/report-claude-sonnet-5-5.json --arm todd-way-with-c
```

Every step is resumable: re-running it skips what is already done. A usage limit stops new sessions
and exits 3; run the same command after the limit resets. `run.py --dry-run` prints the plan and
spends nothing. Sessions default to `claude-opus-5-5` at effort `medium`, a $5 cap per session
(`--budget`) and a 30-minute timeout.

To compare a new version of the style, add an arm to `arms.json` pointing at its commit, run only
that arm (`--only-arm`) into a new run folder, and ratchet it against the baseline's
`todd-way-with-c` arm with `--candidate-arm`.

## Growing the case set

```bash
python3 mine.py --out $E/candidates.json --cases $E/cases   # marks candidates that already have a case
```

Read the new candidates, add the ones that are real barriers — and replayable — to
`$E/cases.spec.json` with their category and flaw, then `build_cases.py --spec ... --out $E/cases`.
`curation.md` there records why every earlier candidate was or was not taken.

## Tests

```bash
python3 -m unittest discover -s scripts/evals/barriers/tests -t .
```

No `claude -p` calls and no cost: `tests/fake_claude.py` stands in for the CLI, so the end-to-end
test drives `run.py → stubs.py → judge.py → report.py → ratchet.py` for real, including a usage-limit
stop and resume.
