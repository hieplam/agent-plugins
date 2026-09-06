#!/usr/bin/env python3
"""
wf-export.py — export Claude Code Workflow runs to readable Markdown, for studying
your own prompting (every agent's full prompt paired with its full result + cost).

WHY: Claude Code already persists everything for a workflow run under
  ~/.claude/projects/<project>/<session-id>/
    workflows/wf_<id>.json                         <- run journal (script, result, workflowProgress, totals)
    workflows/scripts/<name>-wf_<id>.js            <- the standalone script
    subagents/workflows/wf_<id>/agent-<id>.jsonl   <- FULL per-agent transcript
This tool just renders that into one readable .md per run.

USAGE
  wf-export.py <journal.json>      export one run from its journal path
  wf-export.py <runId>             find that run's journal and export it (e.g. wf_1e5340ac-a1e)
  wf-export.py <taskId>            resolve taskId -> journal and export (e.g. wsoyz2kvx)
  wf-export.py <session_dir>       export every completed run in a session dir
  wf-export.py --list              list every run on disk (when | taskId | runId | name)
  wf-export.py --hook              Stop-hook mode: read hook JSON on stdin, export any
                                   newly-completed runs in this session (exactly-once)

CHOOSE OUTPUT DIR (works with every mode above):
  --out DIR  /  -o DIR  /  --out=DIR     CLI flag, highest priority
  export WF_EXPORT_DIR=DIR               env var (use this for the Stop hook)
  precedence:  --out  >  $WF_EXPORT_DIR  >  $WF_JOURNAL_REPO/workflow-journal  >  ~/workflow-journal

OPT-IN GIT AUTO-SYNC:
  export WF_JOURNAL_REPO=/path/to/repo   render into <repo>/workflow-journal and,
                                         after each export, git add+commit+push it.
  export WF_JOURNAL_NO_PUSH=1            commit locally but skip the push.
  A fresh install syncs with no wrapper/hand-wiring — just set WF_JOURNAL_REPO.

OUTPUT  <out>/<ts>__<name>__<runId>.md
STATE   <out>/.state/<runId>.exported    (exactly-once marker)
LOG     <out>/.state/export.log
"""
import sys, os, json, glob, re, datetime, subprocess

HOME = os.path.expanduser("~")
DEFAULT_OUT = os.path.join(HOME, "workflow-journal")

# Opt-in git auto-sync. Point WF_JOURNAL_REPO at a git repo and completed runs are
# rendered into <repo>/workflow-journal and committed+pushed after each export, so a
# fresh install syncs with zero hand-wiring. Unset -> plain local render (old behaviour).
JOURNAL_REPO = os.environ.get("WF_JOURNAL_REPO")
JOURNAL_REPO = os.path.abspath(os.path.expanduser(JOURNAL_REPO)) if JOURNAL_REPO else None
_repo_out = os.path.join(JOURNAL_REPO, "workflow-journal") if JOURNAL_REPO else None

# Export dir precedence:  --out FLAG  >  $WF_EXPORT_DIR  >  $WF_JOURNAL_REPO/workflow-journal  >  ~/workflow-journal
OUT_DIR = os.path.abspath(os.path.expanduser(
    os.environ.get("WF_EXPORT_DIR") or _repo_out or DEFAULT_OUT))
STATE_DIR = os.path.join(OUT_DIR, ".state")
LOG = os.path.join(STATE_DIR, "export.log")
PROJECTS = os.path.join(HOME, ".claude", "projects")


def set_out_dir(path):
    """Repoint all outputs (.md + .state markers + log) at `path`."""
    global OUT_DIR, STATE_DIR, LOG
    OUT_DIR = os.path.abspath(os.path.expanduser(path))
    STATE_DIR = os.path.join(OUT_DIR, ".state")
    LOG = os.path.join(STATE_DIR, "export.log")
    return OUT_DIR


def log(msg):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}\n")


def sanitize(s, n=60):
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", (s or "").strip())
    return s[:n].strip("-") or "x"


def msg_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def read_agent_transcript(jsonl_path):
    """Return (full_prompt, full_result_str) from an agent .jsonl, or (None, None)."""
    prompt = None
    last_struct = None
    last_text = None
    try:
        with open(jsonl_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                m = d.get("message")
                if not isinstance(m, dict):
                    continue
                role = m.get("role")
                content = m.get("content")
                if role == "user" and prompt is None:
                    t = msg_text(content)          # skips tool_result-only user turns
                    if t.strip():
                        prompt = t
                elif role == "assistant" and isinstance(content, list):
                    for b in content:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "tool_use" and b.get("name") == "StructuredOutput":
                            last_struct = b.get("input")
                        elif b.get("type") == "text" and b.get("text", "").strip():
                            last_text = b.get("text")
    except FileNotFoundError:
        return (None, None)
    if last_struct is not None:
        result = json.dumps(last_struct, indent=2, ensure_ascii=False)
    else:
        result = last_text
    return (prompt, result)


def fence(s, lang=""):
    s = s if isinstance(s, str) else json.dumps(s, indent=2, ensure_ascii=False)
    # pick a fence longer than any backtick run inside the body
    longest = max((len(m) for m in re.findall(r"`+", s)), default=0)
    bars = "`" * max(3, longest + 1)
    return f"{bars}{lang}\n{s}\n{bars}"


def ms(v):
    try:
        return f"{int(v)/1000:.1f}s"
    except Exception:
        return "?"


def export_run(journal_path):
    with open(journal_path) as f:
        j = json.load(f)
    run_id = j.get("runId") or os.path.splitext(os.path.basename(journal_path))[0]
    name = j.get("workflowName") or "workflow"
    ts = j.get("timestamp") or ""
    session_dir = os.path.dirname(os.path.dirname(os.path.abspath(journal_path)))
    agent_dir = os.path.join(session_dir, "subagents", "workflows", run_id)

    progress = j.get("workflowProgress") or []
    # one entry per agentId, keep the latest occurrence (final attempt/state)
    agents = {}
    order = []
    for e in progress:
        if e.get("type") != "workflow_agent":
            continue
        aid = e.get("agentId") or f"idx{e.get('index')}"
        if aid not in agents:
            order.append(aid)
        agents[aid] = e

    out = []
    out.append(f"# Workflow: {name}")
    out.append("")
    out.append(
        f"- **runId**: `{run_id}`  ·  **status**: {j.get('status')}  ·  **when**: {ts}\n"
        f"- **agents**: {j.get('agentCount')}  ·  **tokens**: {j.get('totalTokens')}  ·  "
        f"**tool calls**: {j.get('totalToolCalls')}  ·  **wall**: {ms(j.get('durationMs'))}\n"
        f"- **journal**: `{journal_path}`"
    )
    if j.get("summary"):
        out.append("")
        out.append(f"> {j.get('summary')}")

    # index table
    out.append("\n## Agents (index)\n")
    out.append("| # | phase | label | model | tokens | tools | wall |")
    out.append("|---|-------|-------|-------|-------:|------:|-----:|")
    for i, aid in enumerate(order, 1):
        e = agents[aid]
        out.append(
            f"| {i} | {e.get('phaseTitle','')} | {e.get('label','')} | "
            f"{e.get('model','')} | {e.get('tokens','')} | {e.get('toolCalls','')} | {ms(e.get('durationMs'))} |"
        )

    # full prompt + result per agent
    out.append("\n## Agents (full prompt → result)\n")
    for i, aid in enumerate(order, 1):
        e = agents[aid]
        prompt, result = read_agent_transcript(os.path.join(agent_dir, f"agent-{aid}.jsonl"))
        src = "transcript"
        if not prompt:
            prompt, src = e.get("promptPreview", "(prompt unavailable)"), "promptPreview (truncated)"
        if not result:
            result = e.get("resultPreview", "(result unavailable)")
        out.append(
            f"### {i}. `{e.get('label','agent')}`  "
            f"_({e.get('phaseTitle','')} · {e.get('model','')} · {e.get('tokens','?')} tok · "
            f"{e.get('toolCalls','?')} tools · {ms(e.get('durationMs'))} · state={e.get('state','?')})_"
        )
        out.append(f"\n**Prompt** _(source: {src})_\n")
        out.append(fence(prompt))
        out.append("\n**Result**\n")
        out.append(fence(result, "json" if result.strip().startswith(("{", "[")) else ""))
        out.append("\n---\n")

    # the script + final aggregated result + logs
    if j.get("script"):
        out.append("\n## Script (re-runnable)\n")
        out.append(fence(j["script"], "js"))
    if j.get("result") is not None:
        out.append("\n## Final workflow result\n")
        out.append(fence(j["result"], "json"))
    if j.get("logs"):
        out.append("\n## Logs\n")
        out.append(fence("\n".join(str(x) for x in j["logs"])))

    os.makedirs(OUT_DIR, exist_ok=True)
    tstamp = sanitize(ts.replace(":", "").replace("T", "-")[:15]) if ts else "no-ts"
    out_path = os.path.join(OUT_DIR, f"{tstamp}__{sanitize(name,40)}__{run_id}.md")
    with open(out_path, "w") as f:
        f.write("\n".join(out) + "\n")
    return out_path


def find_journal_by_runid(run_id):
    hits = glob.glob(os.path.join(PROJECTS, "*", "*", "workflows", f"{run_id}.json"))
    return hits[0] if hits else None


def find_journal_by_taskid(task_id):
    for jp in glob.glob(os.path.join(PROJECTS, "*", "*", "workflows", "wf_*.json")):
        try:
            with open(jp) as f:
                if json.load(f).get("taskId") == task_id:
                    return jp
        except Exception:
            continue
    return None


def resolve_journal(arg):
    """Accept a journal path, a runId (wf_...), or a taskId."""
    if os.path.isfile(arg):
        return arg
    jp = find_journal_by_runid(arg)
    if jp:
        return jp
    return find_journal_by_taskid(arg)


def export_session_dir(session_dir, only_new=False):
    done = []
    for jp in sorted(glob.glob(os.path.join(session_dir, "workflows", "wf_*.json"))):
        try:
            j = json.load(open(jp))
        except Exception:
            continue
        if j.get("status") != "completed":
            continue
        run_id = j.get("runId") or os.path.splitext(os.path.basename(jp))[0]
        marker = os.path.join(STATE_DIR, run_id + ".exported")
        if only_new and os.path.exists(marker):
            continue
        out = export_run(jp)
        os.makedirs(STATE_DIR, exist_ok=True)
        open(marker, "w").close()
        log(f"exported {run_id} -> {out}")
        done.append(out)
    return done


def git_sync(exported):
    """Opt-in git sync: commit newly-exported journals to $WF_JOURNAL_REPO and push.

    No-op unless $WF_JOURNAL_REPO is set. Set $WF_JOURNAL_NO_PUSH to commit locally
    without pushing. Local-only .state/ (markers + log) is kept out of history.
    Never raises and bounds every git call with a timeout, so it can't wedge Stop.
    """
    if not JOURNAL_REPO or not exported:
        return
    if not OUT_DIR.startswith(JOURNAL_REPO + os.sep):
        log(f"git sync skipped: out dir {OUT_DIR} is outside WF_JOURNAL_REPO {JOURNAL_REPO}")
        return
    if not os.path.isdir(os.path.join(JOURNAL_REPO, ".git")):
        log(f"git sync skipped: {JOURNAL_REPO} is not a git repo")
        return

    def git(*args, timeout=30):
        return subprocess.run(
            ("git", "-C", JOURNAL_REPO) + args,
            capture_output=True, text=True, timeout=timeout,
        )

    try:
        # self-healing on a fresh repo: keep exactly-once markers/log untracked
        gi = os.path.join(OUT_DIR, ".gitignore")
        if not os.path.exists(gi):
            os.makedirs(OUT_DIR, exist_ok=True)
            with open(gi, "w") as f:
                f.write(".state/\n")
        rel = os.path.relpath(OUT_DIR, JOURNAL_REPO)
        git("add", "--", rel)
        if git("diff", "--cached", "--quiet").returncode == 0:
            return  # export changed nothing tracked; nothing to commit
        branch = (git("rev-parse", "--abbrev-ref", "HEAD").stdout or "").strip() or "HEAD"
        n = len(exported)
        git("commit", "-q", "-m",
            f"[{branch}] chore: auto-sync workflow-journal ({n} run{'s' if n != 1 else ''})")
        if os.environ.get("WF_JOURNAL_NO_PUSH"):
            log(f"git sync: committed {n} run(s) to {branch} (push disabled)")
            return
        # Integrate other machines' pushes FIRST so our push fast-forwards. Other
        # machines journal into the same branch; filenames are per-run unique, so
        # history diverges but content won't conflict — rebase replays our commit on
        # top. On the rare conflict, abort cleanly and let the next run retry.
        if branch != "HEAD":
            f = git("fetch", "origin", branch, timeout=60)
            if f.returncode == 0:
                rb = git("rebase", "FETCH_HEAD")
                if rb.returncode != 0:
                    git("rebase", "--abort")
                    log(f"git sync: committed {n} run(s); rebase onto origin/{branch} "
                        f"conflicted — left local commit, will retry next run")
                    return
        p = git("push", "origin", "HEAD", timeout=60)
        if p.returncode == 0:
            log(f"git sync: pushed {n} run(s) to origin/{branch}")
        else:
            log(f"git sync: committed {n} run(s); push rejected/failed "
                f"(will retry next run): {(p.stderr or '').strip()[:200]}")
    except Exception as ex:
        log(f"git sync error: {ex}")


def list_runs():
    """Print every workflow run found on disk: when | taskId | runId | status | agents | tokens | name."""
    rows = []
    for jp in glob.glob(os.path.join(PROJECTS, "*", "*", "workflows", "wf_*.json")):
        try:
            d = json.load(open(jp))
        except Exception:
            continue
        rows.append((
            (d.get("timestamp") or "?")[:19],
            d.get("taskId") or "?",
            d.get("runId") or os.path.splitext(os.path.basename(jp))[0],
            (d.get("status") or "?")[:9],
            str(d.get("agentCount") or "?"),
            str(d.get("totalTokens") or "?"),
            d.get("workflowName") or "?",
        ))
    rows.sort(reverse=True)
    hdr = ("WHEN", "TASKID", "RUNID", "STATUS", "AGENTS", "TOKENS", "NAME")
    print(f"{hdr[0]:<19}  {hdr[1]:<10}  {hdr[2]:<18}  {hdr[3]:<9}  {hdr[4]:>6}  {hdr[5]:>8}  {hdr[6]}")
    for r in rows:
        print(f"{r[0]:<19}  {r[1]:<10}  {r[2]:<18}  {r[3]:<9}  {r[4]:>6}  {r[5]:>8}  {r[6]}")
    print(f"\n{len(rows)} run(s).  Export one:  wf-export.py <taskId|runId>")


def hook_mode():
    raw = sys.stdin.read()
    try:
        hook = json.loads(raw)
    except Exception:
        hook = {}
    tp = hook.get("transcript_path")
    session_dir = None
    if tp and tp.endswith(".jsonl"):
        cand = tp[: -len(".jsonl")]
        if os.path.isdir(cand):
            session_dir = cand
    if not session_dir:
        cwd, sid = hook.get("cwd"), hook.get("session_id")
        if cwd and sid:
            proj = cwd.replace("/", "-")
            cand = os.path.join(PROJECTS, proj, sid)
            if os.path.isdir(cand):
                session_dir = cand
    if not session_dir:
        sys.exit(0)  # nothing to do; never block Stop
    try:
        done = export_session_dir(session_dir, only_new=True)
        git_sync(done)
    except Exception as ex:
        log(f"hook error: {ex}")
    sys.exit(0)


def main():
    argv = sys.argv[1:]
    # pull --out/-o DIR (or --out=DIR) out of the args, wherever it appears
    out_override, cleaned, i = None, [], 0
    while i < len(argv):
        a = argv[i]
        if a in ("--out", "-o"):
            if i + 1 >= len(argv):
                print("--out needs a directory", file=sys.stderr)
                sys.exit(2)
            out_override = argv[i + 1]
            i += 2
            continue
        if a.startswith("--out="):
            out_override = a.split("=", 1)[1]
            i += 1
            continue
        cleaned.append(a)
        i += 1
    if out_override:
        set_out_dir(out_override)

    if not cleaned:
        print(__doc__)
        sys.exit(2)
    arg = cleaned[0]
    if arg == "--hook":
        return hook_mode()
    if arg in ("--list", "-l", "ls"):
        return list_runs()
    if os.path.isdir(arg):
        outs = export_session_dir(arg)
    elif os.path.isfile(arg):
        outs = [export_run(arg)]
    else:
        jp = resolve_journal(arg)
        if not jp:
            print(f"no journal found for runId/taskId/path: {arg}", file=sys.stderr)
            sys.exit(1)
        outs = [export_run(jp)]
    for o in outs:
        print(o)
    git_sync(outs)  # manual backfill also syncs when WF_JOURNAL_REPO is set


if __name__ == "__main__":
    main()
