#!/usr/bin/env bash
# workflow-journal post-install hook.
#
# The plugin ships its auto-capture Stop hook two ways (belt-and-suspenders):
#
#   1. hooks/hooks.json  — native plugin hook. Activates when workflow-journal is
#      loaded as a real plugin (marketplace install, or a whole-plugin @skills-dir
#      symlink where .claude-plugin/plugin.json travels with hooks/).
#   2. THIS script        — the fallback for the repo's default install path, where
#      only the inner skills/workflow-journal/ folder is symlinked into
#      ~/.claude/skills/ (so the native hook is never discovered). It wires the
#      Stop hook straight into settings.json instead.
#
# Both are idempotent and the exporter is exactly-once per run, so if both ever
# fire, the second is a harmless no-op.
#
# Invoked by the repo's top-level install.sh with CLAUDE_DIR passed through.

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
SETTINGS="$CLAUDE_DIR/settings.json"

# The exporter is reached through the skill symlink the top-level install.sh
# creates — one source of truth, no copied script to drift. Home-relative so the
# command is portable to any machine/user (the exporter expanduser()s ~ itself).
# No --out: the exporter resolves the output dir from env ($WF_EXPORT_DIR >
# $WF_JOURNAL_REPO/workflow-journal > ~/workflow-journal). Set WF_JOURNAL_REPO
# (e.g. in settings.json "env") to render into a repo and auto commit+push it.
HOOK_CMD="python3 ~/.claude/skills/workflow-journal/scripts/wf-export.py --hook"

# We only know how to edit the standard ~/.claude location portably. A custom
# CLAUDE_DIR (e.g. test harness) gets the native hooks.json path instead — skip.
if [ "$CLAUDE_DIR" != "$HOME/.claude" ]; then
  echo "  hook    workflow-journal: CLAUDE_DIR is not ~/.claude — relying on native hooks/hooks.json (settings.json not touched)"
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "WARN: workflow-journal: python3 not found — cannot wire Stop hook; add it to settings.json manually (see SKILL.md)" >&2
  exit 0
fi

mkdir -p "$CLAUDE_DIR"

WF_HOOK_CMD="$HOOK_CMD" python3 - "$SETTINGS" <<'PY'
import json, os, sys

settings_path = sys.argv[1]
hook_cmd = os.environ["WF_HOOK_CMD"]

try:
    with open(settings_path) as f:
        settings = json.load(f)
except FileNotFoundError:
    settings = {}
except (json.JSONDecodeError, OSError) as e:
    print(f"WARN: workflow-journal: cannot parse {settings_path} ({e}) — Stop hook NOT wired; add it manually (see SKILL.md)", file=sys.stderr)
    sys.exit(0)

hooks = settings.setdefault("hooks", {})
stop = hooks.setdefault("Stop", [])

# Idempotent AND self-healing: find any existing Stop hook that runs the exporter
# (a hand-wired one, or an OLD install carrying a stale `--out ~/workflow-journal`
# that would override WF_JOURNAL_REPO). Rewrite it to the current command so a
# re-run upgrades an old machine in place. Only append if none exists.
changed = False
found = False
for group in stop:
    for h in group.get("hooks", []):
        if "wf-export.py" in (h.get("command") or ""):
            found = True
            if h.get("command") != hook_cmd:
                h["command"] = hook_cmd   # migrate stale command (drops old --out)
                changed = True

if not found:
    stop.append({
        "matcher": "",
        "hooks": [{"type": "command", "command": hook_cmd}],
    })
    changed = True

# Optionally wire git auto-sync in one shot:  WF_JOURNAL_REPO=/path ./install.sh workflow-journal
repo = os.environ.get("WF_JOURNAL_REPO")
if repo:
    env = settings.setdefault("env", {})
    if env.get("WF_JOURNAL_REPO") != repo:
        env["WF_JOURNAL_REPO"] = repo
        changed = True

if not changed:
    print("  ok      workflow-journal: Stop hook already current in settings.json")
    sys.exit(0)

# Write via temp + atomic replace, keeping a one-shot backup.
tmp = settings_path + ".tmp"
with open(tmp, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")
if os.path.exists(settings_path):
    try:
        os.replace(settings_path, settings_path + ".bak")
    except OSError:
        pass
os.replace(tmp, settings_path)
print("  linked  workflow-journal: Stop hook wired/updated in settings.json (auto-capture on)")
if repo:
    print(f"  env     workflow-journal: WF_JOURNAL_REPO={repo} (git auto-sync on)")
PY
