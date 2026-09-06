# 📓 Workflow Journal

> Turn every Claude Code **Workflow** run into one readable Markdown file — each agent's *full* prompt next to its *full* result and token cost — captured automatically, and (optionally) auto-committed to a git repo so your prompting history syncs across machines.

Claude Code already persists everything about a `Workflow` run, but only as raw JSON/JSONL scattered across three locations. This plugin renders a run into a single `.md` you can actually read and study, a `Stop` hook captures each run exactly once as it completes, and one env var turns that into a self-syncing archive.

---

## Why

You can't improve prompting you can't see. After a fan-out finishes, the interesting part — what each sub-agent was actually told, and what it gave back — is buried. This plugin surfaces it: **prompt → result, per agent, in reading order**, plus cost, so a run becomes a document you can review, diff, and learn from.

---

## What a rendered run contains

| Section | Content |
|---|---|
| **Header** | status · timestamp · agent count · total tokens · tool calls · wall time |
| **Agents (index)** | one table row per agent: phase · label · model · tokens · tools · wall |
| **Agents (full prompt → result)** | every agent's complete prompt paired with its complete result (StructuredOutput pretty-printed as JSON) |
| **Script** | the standalone, re-runnable workflow script |
| **Final result** & **Logs** | the workflow's aggregate output and log lines |

---

## Quick start

```bash
# from the agent-plugins repo
./install.sh workflow-journal
```

That symlinks the skill and wires the `Stop` hook. Every run that completes in a session is now rendered to `~/workflow-journal/` — exactly once. Nothing else to do.

### Turn on git auto-sync (optional)

Point one env var at a git repo and each render is committed + pushed automatically:

```bash
WF_JOURNAL_REPO=/path/to/repo ./install.sh workflow-journal
```

or set it yourself in `~/.claude/settings.json`:

```json
{ "env": { "WF_JOURNAL_REPO": "/Users/you/my-claude-workflows" } }
```

Now on each session Stop the exporter renders into `<repo>/workflow-journal/`, then `git add` + commit (`[<branch>] chore: auto-sync workflow-journal (N runs)`) + `git push` — only when something changed. It **fetches + rebases before pushing**, so several machines can journal into the same repo without collisions (run files are per-run unique). Set `WF_JOURNAL_NO_PUSH=1` to commit locally only.

---

## How capture works

```
Session Stop
   │
   ▼
hooks/hooks.json ──▶ wf-export.py --hook
   │                     │  reads only local run journals for THIS session
   │                     │  exactly-once via <out>/.state/<runId>.exported
   ▼                     ▼
render <ts>__<name>__<runId>.md      ── if WF_JOURNAL_REPO set ──▶ git add + rebase + push
```

The hook **never blocks Stop**: it exits silently when no run completed, every git call is timeout-bounded, and all errors are swallowed to `.state/export.log`.

---

## CLI

```
wf-export.py <runId|taskId>    export one run now (ignores the exactly-once marker)
wf-export.py <session_dir>     export every completed run in a session dir
wf-export.py --list            list every run found on disk
wf-export.py --hook            Stop-hook mode (reads hook JSON on stdin)
```

Output dir precedence: `--out` > `$WF_EXPORT_DIR` > `$WF_JOURNAL_REPO/workflow-journal` > `~/workflow-journal`.

---

## Layout

```
plugins/workflow-journal/
├── .claude-plugin/plugin.json          plugin manifest
├── hooks/hooks.json                    native Stop hook (auto-capture)
├── install.sh                          symlink + wire/migrate the Stop hook
└── skills/workflow-journal/
    ├── SKILL.md                        full docs (setup, sync, troubleshooting)
    └── scripts/wf-export.py            the renderer + git sync
```

See **[SKILL.md](skills/workflow-journal/SKILL.md)** for full setup, the opt-in sync details, upgrading an older machine, and troubleshooting.
