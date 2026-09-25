---
name: workflow-journal
description: Set up, run, or query the Claude Code Workflow exporter — it renders each Workflow run to one readable Markdown file (every agent's full prompt paired with its full result + token cost), and a Stop hook auto-captures every run as it completes. Use when the user wants to study their own prompting, review or export past Workflow runs, list what runs are on disk, wire up automatic workflow journaling, or asks things like "export my workflow runs", "list workflow runs", "capture my workflows", "set up the Stop hook to journal workflows", "where did that workflow run go", or "re-export run wf_…". Also self-trigger after authoring a substantial Workflow when the user wants a durable, human-readable record of it.
---

# Workflow Journal

Claude Code already persists everything about a `Workflow` run to disk, but only as raw JSON/JSONL scattered across three locations. This skill's `wf-export.py` **renders one run into a single readable Markdown file** — every agent's *full* prompt next to its *full* result, plus per-agent and total token cost — so you can study your own prompting after the fact. A `Stop` hook makes capture automatic: every run that completes in a session is exported exactly once.

## What it captures

Per run, the exported `.md` contains:

- A header line — status, timestamp, agent count, total tokens, tool calls, wall time.
- **Agents (index)** — a table: phase · label · model · tokens · tools · wall, one row per agent.
- **Agents (full prompt → result)** — for each agent, its complete prompt paired with its complete result (StructuredOutput is pretty-printed as JSON; otherwise the agent's final text).
- **Script (re-runnable)** — the standalone workflow script.
- **Final workflow result** and **Logs**.

## Where the source data lives

The script only reads what Claude Code already wrote under `~/.claude/projects/<project>/<session-id>/`:

```
workflows/wf_<id>.json                        <- run journal (script, result, progress, totals)
workflows/scripts/<name>-wf_<id>.js           <- the standalone script
subagents/workflows/wf_<id>/agent-<id>.jsonl  <- FULL per-agent transcript
```

Nothing is sent anywhere; it is a pure local render.

---

## Setup

The auto-capture `Stop` hook ships **with the plugin** — installing the plugin is the whole setup. It's wired two ways so it works on any machine (only Python 3 stdlib is used — no `pip install`):

- **`hooks/hooks.json`** — a native plugin hook (`${CLAUDE_PLUGIN_ROOT}/skills/workflow-journal/scripts/wf-export.py --hook`). It activates when workflow-journal loads as a real plugin (a marketplace install, or a whole-plugin `@skills-dir` symlink where `.claude-plugin/plugin.json` travels with `hooks/`).
- **`install.sh`** (this plugin's post-install hook) — the fallback for the repo's default install path, where only the inner `skills/workflow-journal/` folder is symlinked into `~/.claude/skills/` and the native hook isn't discovered. It idempotently wires the same `Stop` hook straight into `~/.claude/settings.json`, pointing at the exporter through the skill symlink (`~/.claude/skills/workflow-journal/scripts/wf-export.py`) — one source of truth, no copied script to drift.

Both are idempotent and the exporter is exactly-once per run, so if both ever fire the second is a harmless no-op.

### Install (fresh machine)

```bash
git clone https://github.com/hieplam/agent-plugins.git && cd agent-plugins
./install.sh workflow-journal      # symlinks the skill + runs the post-install hook
```

That symlinks the skill and wires the `Stop` hook. Nothing else to do.

### Manual wiring (if you're not using this repo's install.sh)

Add (or merge) this into `~/.claude/settings.json`. On every session Stop it exports any **newly-completed** runs in that session, exactly once, and never blocks Stop. The output dir comes from env (see below); add the optional `env` block to render into a git repo and auto commit+push:

```json
{
  "env": { "WF_JOURNAL_REPO": "/Users/you/my-claude-workflows" },
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/skills/workflow-journal/scripts/wf-export.py --hook"
          }
        ]
      }
    ]
  }
}
```

Drop the `env` block for a plain local render to `~/workflow-journal`.

### Output directory

The render destination. Precedence, highest first:

| Source | How |
|---|---|
| `--out DIR` / `-o DIR` / `--out=DIR` | CLI flag |
| `$WF_EXPORT_DIR` | env var — handy if you'd rather not hard-code the path in the hook |
| `$WF_JOURNAL_REPO/workflow-journal` | when git auto-sync is enabled (below) |
| `~/workflow-journal` | built-in default |

### Git auto-sync (opt-in) — "install and it syncs"

Set **`WF_JOURNAL_REPO=/path/to/repo`** and the exporter renders into
`<repo>/workflow-journal` and, after each export, `git add` + commit + `git push origin HEAD`.
No wrapper, no hand-wired hook — the plugin's own Stop hook does it. Unset the var and
behaviour is unchanged (plain local render to `~/workflow-journal`).

- Set it once in `~/.claude/settings.json` so every session's hook sees it:
  ```json
  { "env": { "WF_JOURNAL_REPO": "/Users/you/my-claude-workflows" } }
  ```
- The commit is `[<branch>] chore: auto-sync workflow-journal (N runs)`; push targets
  `origin` / the current branch. Set **`WF_JOURNAL_NO_PUSH=1`** to commit locally only.
- Requirements: the path is a git repo with a pushable `origin` + non-interactive git auth
  (SSH key). Local-only `.state/` is auto-gitignored, so markers/logs never get committed.
- **Multi-machine safe.** Before pushing, the sync `git fetch`es + rebases onto `origin/<branch>`,
  so several machines can journal into the same repo without non-fast-forward rejections. Run
  filenames are per-run unique (`…__<runId>.md`), so history diverges but content never collides.
  A rebase conflict (rare) aborts cleanly and retries next run.
- Never blocks Stop: every git call is timeout-bounded and all errors are swallowed to
  `.state/export.log`. A failed push leaves a local commit the next run pushes.

### Upgrading a machine that has an older workflow-journal

Two steps — `install.sh` alone is not enough, because it wires *settings*, not plugin *code*:

1. **Update the code:** `git pull` in your `agent-plugins` checkout (the skill is symlinked, so the
   pull makes the new exporter live — no reinstall needed for code).
2. **Migrate the hook + turn on sync:** `WF_JOURNAL_REPO=/path/to/repo ./install.sh workflow-journal`.
   Re-running now **rewrites** a stale hook (e.g. an old `--out ~/workflow-journal` that would
   otherwise override `WF_JOURNAL_REPO`) to the current command, and writes `WF_JOURNAL_REPO`
   into settings.json `env`. It's a no-op if already current.

### Verify

```bash
SCRIPT=~/.claude/skills/workflow-journal/scripts/wf-export.py
python3 "$SCRIPT" --list                # every run found on disk
python3 "$SCRIPT" <runId|taskId>        # export one now
ls ~/workflow-journal                   # rendered .md files land here
```

---

## Usage (CLI)

```
wf-export.py <journal.json>   export one run from its journal path
wf-export.py <runId>          find + export by runId          (e.g. wf_1e5340ac-a1e)
wf-export.py <taskId>         resolve taskId -> journal, export (e.g. wsoyz2kvx)
wf-export.py <session_dir>    export every completed run in a session dir
wf-export.py --list           list every run on disk (when | taskId | runId | name)
wf-export.py --hook           Stop-hook mode: read hook JSON on stdin, export new runs
```

`--out DIR` composes with any of the above.

## Output & state layout

```
<out>/<ts>__<name>__<runId>.md    one readable file per run
<out>/.state/<runId>.exported     exactly-once marker (delete to force a re-export)
<out>/.state/export.log           what the hook exported, and any hook errors
```

## Troubleshooting

- **Hook seems to do nothing** — it exits silently unless a run *completed* this session. Check `~/workflow-journal/.state/export.log`, or run `--list` to confirm runs exist on disk.
- **Want to re-export a run** — delete its `.state/<runId>.exported` marker, or just run `wf-export.py <runId>` directly (direct invocation ignores the marker).
- **Different project/output dir** — set `WF_EXPORT_DIR` in the hook's environment, or change the `--out` path in the hook command.
- **Never blocks Stop** — hook mode swallows its own errors and always exits 0, so a broken export can't wedge your session.
