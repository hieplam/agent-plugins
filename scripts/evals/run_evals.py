#!/usr/bin/env python3
"""Repo-level eval runner for agent-plugins.

Executes an `evals/evals.json` file (the shape already used by
`refactor-for-testability`: skill_name, evals[].id/name/prompt/expected_output/files)
against isolated `claude -p` subprocesses — one clean-context process per case per
configuration, mirroring the official skill-creator eval loop's own runner
(`skill-creator/scripts/run_eval.py` spawns one `claude -p` per query rather than
reusing a session) instead of inventing a new mechanism.

For every case, up to two configurations are run:

  with_skill     the skill/agent under test is made available for this invocation
                 only, and nothing else installed at user scope on the host
                 machine leaks in:
                   - skill evals (kind: "skill") copy the ENTIRE real skill
                     directory (SKILL.md + references/, scripts/, assets — not
                     just the frontmatter `description:`) into the scratch
                     cwd's project scope under .claude/skills/<name>/, exactly
                     like skill-creator's own eval loop installs the skill
                     under test — so the skill can genuinely trigger (or fail
                     to) via the normal Skill tool and exercise its real body
                     (thresholds, phase names, round caps, references,
                     anti-patterns), not dead-end at a one-line stub.
                   - agent evals (kind: "agent") pass the named agent's
                     real frontmatter + body straight through `--agents` /
                     `--agent`, independent of whether it happens to be
                     symlink-installed locally — the eval targets the agent
                     definition in this repo, not the local install state.
                   - both call `claude -p` with `--setting-sources project
                     --strict-mcp-config` (never `--safe-mode`, which would
                     also suppress the throwaway command / --agents payload
                     this leg depends on). `--setting-sources project` loads
                     only the scratch cwd's own `.claude/` — i.e. the one
                     throwaway command we just wrote there — and excludes the
                     user-scope settings source entirely, so every other
                     locally-installed marketplace plugin/skill (this repo's
                     own splitting-plans/check-diff-coverage/explaining/
                     refactor-for-testability included, plus anything else
                     symlink-installed at user scope) is invisible for this
                     run; `--strict-mcp-config` with no `--mcp-config` drops
                     every user-configured MCP server the same way. Verified
                     empirically: registering a throwaway command and running
                     `claude -p ... --setting-sources project
                     --strict-mcp-config` reports exactly the built-in
                     baseline slash_commands plus the one throwaway command
                     (`plugins: []`, `mcp_servers: []` in the `system`/`init`
                     event) — none of the other locally-installed marketplace
                     plugins appear, unlike an unflagged call which reports
                     all of them simultaneously.
  without_skill  a `claude -p --safe-mode` call — nothing registered AND all
                 user-level customizations (CLAUDE.md, skills, plugins, hooks,
                 MCP servers, custom commands/agents) disabled, so a skill
                 that also happens to be installed at user scope on the
                 machine running this harness (common when dogfooding this
                 repo's own marketplace) cannot silently fire and collapse
                 the baseline into the with_skill run. This is the baseline
                 a benchmark compares against.

Each run's transcript and the token/duration numbers `claude -p --output-format
stream-json --verbose` already reports (duration_ms, usage, total_cost_usd — no
separate instrumentation needed) are graded by a second, tool-less `claude -p`
call (the "grader"), scored against the case's expected_output. Results are written as
grading.json per run (schema: skill-creator's references/schemas.md
`grading.json`) and rolled up into one benchmark.json per invocation (schema:
skill-creator's `benchmark.json`, with_skill vs without_skill).

This script only reads skill/agent files and shells out to `claude -p` in a
scratch working directory — it never edits a skill's or agent's runtime files.

Usage:
    scripts/evals/run_evals.py --evals plugins/splitting-plans/skills/splitting-plans/evals/evals.json
    scripts/evals/run_evals.py --evals plugins/explaining/skills/explaining/evals/evals.json --eval-id 3,6
    scripts/evals/run_evals.py --all                      # every evals.json under plugins/
    scripts/evals/run_evals.py --all --mode with_skill     # skip the baseline, just prove it runs
    scripts/evals/run_evals.py --all --dry-run             # list what would run, no API calls
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGURATIONS = ("with_skill", "without_skill")
ARMS = ("clean", "mem")


def plan_arm_configurations(arm: str, configurations: tuple) -> tuple:
    """Pure: which legs this arm runs.

    The without_skill baseline is `claude -p --safe-mode`, and --safe-mode disables
    CLAUDE.md — so a "mem baseline" would silently be a clean baseline wearing a mem
    label. Rather than weaken the baseline's isolation (c3-301 records that as the
    "Baseline contamination" risk), the mem arm runs the with_skill leg only and the
    clean arm's baseline is shared.
    """
    if arm == "mem":
        return tuple(c for c in configurations if c == "with_skill")
    return tuple(configurations)


def plan_memory_files(arm: str, memory_fixture: Path | None) -> dict:
    """Pure: what ambient memory the scratch dir must (not) contain for this arm.

    Mirrors the detection harness's planScratch(): the clean arm does not merely
    skip writing memory, it ASSERTS none is present, so a leak is caught rather
    than quietly measured.
    """
    if arm == "mem":
        return {"memory_files": [("CLAUDE.md", memory_fixture)] if memory_fixture else [],
                "assert_no_memory": False}
    return {"memory_files": [], "assert_no_memory": True}


def plan_jobs(cases: list, configurations: tuple, arms: tuple, runs: int,
              has_memory_fixture: bool) -> tuple:
    """Pure: the (case, configuration, arm, run) grid plus honest skip notes.

    A mem arm requested for a fixture that declares no memory_fixture is SKIPPED
    with a note — never run as a clean cell labelled `mem`, which would report a
    number nobody measured. Likewise, an arm whose runnable legs (per
    plan_arm_configurations) don't intersect the requested `configurations` at
    all (e.g. --mode without_skill with --arm mem, since mem only ever runs
    with_skill) plans zero cells for that arm and gets the same honest note —
    never a silent zero-job vanish with no note and no setup_error.
    """
    jobs: list = []
    notes: list = []
    for arm in arms:
        if arm == "mem" and not has_memory_fixture:
            notes.append("mem arm skipped: fixture declares no memory_fixture")
            continue
        arm_configurations = plan_arm_configurations(arm, configurations)
        if not arm_configurations:
            notes.append(
                f"{arm} arm skipped: requested configurations {configurations} "
                f"don't include its required with_skill leg")
            continue
        for case in cases:
            for configuration in arm_configurations:
                for run_idx in range(runs):
                    jobs.append({"case": case, "configuration": configuration,
                                  "arm": arm, "run_idx": run_idx})
    return jobs, notes


# ---------------------------------------------------------------------------
# Frontmatter parsing (SKILL.md / agents/*.md share the same shape)
# ---------------------------------------------------------------------------

def parse_frontmatter(path: Path) -> tuple[dict, str]:
    """Split a SKILL.md / agent .md into (frontmatter fields, body).

    Handles the `key: value` and `key: >-` / `key: |` block-scalar forms used
    throughout this repo's skills and agents. Not a general YAML parser — just
    enough to recover `name` and `description` for eval registration.
    """
    text = path.read_text()
    if not text.startswith("---"):
        return {}, text

    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_block = text[3:end].strip("\n")
    body = text[end + 4:].lstrip("\n")

    fields: dict[str, str] = {}
    lines = fm_block.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key, rest = m.group(1), m.group(2).strip()
        if rest in (">-", "|", ">", "|-"):
            collected = []
            i += 1
            while i < len(lines) and (lines[i].startswith("  ") or lines[i].strip() == ""):
                collected.append(lines[i][2:] if lines[i].startswith("  ") else "")
                i += 1
            fields[key] = " ".join(l.strip() for l in collected if l.strip())
            continue
        fields[key] = rest.strip('"')
        i += 1
    return fields, body


# ---------------------------------------------------------------------------
# claude -p invocation
# ---------------------------------------------------------------------------

def run_claude(prompt: str, cwd: Path, timeout: int, model: str | None = None,
                agents_json: dict | None = None, agent_name: str | None = None,
                tools: str | None = None, safe_mode: bool = False,
                isolate_user_scope: bool = False,
                permission_mode: str | None = None) -> dict:
    """Run one isolated `claude -p` process and return its parsed result.

    Returns a dict with at least: ok (bool), events (list, may be empty on
    timeout/error), error (str|None).
    """
    # --output-format stream-json (NOT plain "json"), always paired with --verbose:
    # verified empirically that plain `--output-format json` silently collapses from
    # the full per-turn event array to a single bare `{"type":"result",...}` dict
    # whenever `--setting-sources` excludes "user" — exactly the isolate_user_scope
    # leg below (`--setting-sources project`). Isolated further: `--strict-mcp-config`
    # alone does NOT trigger the collapse; only omitting "user" from
    # `--setting-sources` does. stream-json does not exhibit this collapse under the
    # identical `--setting-sources project --strict-mcp-config` flags (one JSON
    # object per stdout line, always including system/assistant/user/result events,
    # confirmed system-event `plugins`/`mcp_servers` still read `[]` for isolation),
    # so it is used unconditionally for both configurations here rather than only for
    # the affected leg, to keep with_skill and without_skill parsed identically.
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--no-session-persistence"]
    if model:
        cmd += ["--model", model]
    if agents_json:
        cmd += ["--agents", json.dumps(agents_json)]
    if agent_name:
        cmd += ["--agent", agent_name]
    if tools is not None:
        cmd += ["--tools", tools]
    if permission_mode:
        # Without this the executor runs under the default permission mode, where
        # `-p` (non-interactive) cannot surface an approval prompt and therefore
        # DENIES every write. Observed directly: an executor read its fixture
        # fine, then had every Write blocked, tried `dd` to get around it, and
        # reported BLOCKED — which the grader scored as a behavioral FAIL. That
        # is a harness artifact masquerading as an agent defect, and it would
        # silently corrupt any regression signal read off this suite.
        #
        # Every executor runs in a fresh mkdtemp scratch dir that this script
        # deletes in run_case's finally block, with user-scope settings/MCP
        # already excluded, so the blast radius is that throwaway directory.
        cmd += ["--permission-mode", permission_mode]
    if safe_mode:
        # Disables CLAUDE.md, skills, plugins, hooks, MCP servers, custom
        # commands/agents — i.e. everything installed at user/project scope
        # on the host machine, not just "nothing extra registered for this
        # call". Without this, a skill that is also symlink-installed at
        # user scope (the normal state when dogfooding this repo) fires for
        # the without_skill baseline too, collapsing the with/without
        # comparison. Only ever passed for the without_skill configuration —
        # with_skill needs the throwaway project command / --agents payload
        # to actually be visible, which --safe-mode would itself suppress.
        cmd += ["--safe-mode"]
    if isolate_user_scope:
        # with_skill's equivalent of --safe-mode: keep the one throwaway
        # project command / --agents payload visible (--safe-mode would
        # suppress those too) while still excluding every OTHER
        # locally-installed marketplace plugin/skill and every
        # user-configured MCP server, so the comparison isn't confounded by
        # whatever else happens to be symlink-installed on the machine
        # running the harness.
        #   --setting-sources project   loads only the scratch cwd's own
        #     .claude/ (the throwaway command file we just wrote there);
        #     the user-scope settings source — where this repo's own
        #     marketplace plugins are normally installed — is excluded.
        #   --strict-mcp-config         with no --mcp-config passed, drops
        #     every user-configured MCP server the same way.
        # Verified empirically: with these two flags, the `system`/`init`
        # event's `plugins` and `mcp_servers` are both `[]` and
        # `slash_commands` is exactly the built-in baseline plus the one
        # throwaway command — none of the other locally-installed
        # marketplace plugins (e.g. superpowers:writing-plans) appear.
        cmd += ["--setting-sources", "project", "--strict-mcp-config"]

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    start = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True,
            timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "events": [], "error": f"timeout after {timeout}s",
                "wall_seconds": time.time() - start}
    except FileNotFoundError:
        return {"ok": False, "events": [], "error": "claude CLI not found on PATH",
                "wall_seconds": time.time() - start}

    wall = time.time() - start
    if proc.returncode != 0 and not proc.stdout.strip():
        return {"ok": False, "events": [], "error": f"exit {proc.returncode}: {proc.stderr[-2000:]}",
                "wall_seconds": wall}

    # stream-json emits one JSON object per stdout line (not one JSON array/value for
    # the whole call) — parse line-by-line rather than json.loads() on the full blob.
    events = []
    for line_no, line in enumerate(proc.stdout.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as e:
            return {"ok": False, "events": [], "error": f"bad JSON on stdout line {line_no}: {e}",
                    "wall_seconds": wall, "raw_stdout": proc.stdout[-2000:]}

    return {"ok": True, "events": events, "error": None, "wall_seconds": wall}


def extract_metrics(run: dict) -> dict:
    """Derive metrics.json-shaped data + a flat transcript from claude -p events."""
    tool_calls: dict[str, int] = {}
    transcript_parts: list[str] = []
    errors = 0
    result_event = None

    for event in run.get("events", []):
        etype = event.get("type")
        if etype == "assistant":
            message = event.get("message", {})
            for item in message.get("content", []):
                if item.get("type") == "text":
                    transcript_parts.append(f"ASSISTANT: {item['text']}")
                elif item.get("type") == "tool_use":
                    name = item.get("name", "unknown")
                    tool_calls[name] = tool_calls.get(name, 0) + 1
                    transcript_parts.append(f"TOOL_USE: {name} {json.dumps(item.get('input', {}))[:300]}")
        elif etype == "user":
            message = event.get("message", {})
            content = message.get("content", [])
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        if item.get("is_error"):
                            errors += 1
        elif etype == "result":
            result_event = event

    transcript = "\n".join(transcript_parts)
    metrics = {
        "tool_calls": tool_calls,
        "total_tool_calls": sum(tool_calls.values()),
        "total_steps": sum(1 for e in run.get("events", []) if e.get("type") == "assistant"),
        "errors_encountered": errors,
        "output_chars": len(result_event.get("result", "")) if result_event else 0,
        "transcript_chars": len(transcript),
    }

    timing = {}
    tokens = 0
    cost_usd = 0.0
    if result_event:
        usage = result_event.get("usage", {})
        tokens = (usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                  + usage.get("cache_creation_input_tokens", 0) + usage.get("cache_read_input_tokens", 0))
        cost_usd = result_event.get("total_cost_usd", 0.0)
        timing = {
            "duration_ms": result_event.get("duration_ms", 0),
            "total_duration_seconds": round(result_event.get("duration_ms", 0) / 1000.0, 1),
            "num_turns": result_event.get("num_turns", 0),
        }

    return {
        "metrics": metrics,
        "timing": timing,
        "transcript": transcript,
        "final_result": result_event.get("result", "") if result_event else "",
        "is_error": bool(result_event.get("is_error")) if result_event else True,
        "total_tokens": tokens,
        "total_cost_usd": cost_usd,
    }


# ---------------------------------------------------------------------------
# Skill registration
# ---------------------------------------------------------------------------

def install_skill(scratch_dir: Path, skill_dir: Path, skill_name: str) -> Path:
    """Install the REAL skill into the scratch project scope for the with_skill leg.

    A description-only stub command can't exercise a skill's real procedure
    (thresholds, phase names, round caps, references/*.md, anti-patterns) — it
    just dead-ends and the executor falls back to generic answers, which
    measures nothing. Instead, copy the whole skill directory (SKILL.md +
    references/, scripts/, assets) to .claude/skills/<skill_name>/ under the
    scratch cwd, which run_claude() already points at via `--setting-sources
    project` — the normal on-disk shape Claude Code discovers project skills
    from, so the executor loads and can genuinely trigger the real skill
    through the ordinary Skill mechanism. The eval fixtures directory itself
    (evals/, containing this case's own expected_output) is excluded so it
    never leaks into the executor's working tree.
    """
    dest = scratch_dir / ".claude" / "skills" / skill_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(skill_dir, dest, ignore=shutil.ignore_patterns("evals", "node_modules"))
    return dest


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

GRADER_INSTRUCTIONS = """You are grading an AI agent's response to a task, exactly like a \
disinterested QA reviewer. You have no tools — judge only from the text given below.

TASK PROMPT GIVEN TO THE AGENT:
{prompt}

EXPECTED BEHAVIOR (what a correct response looks like):
{expected_output}

AGENT'S FULL TRANSCRIPT (assistant text + tool calls it made):
{transcript}

AGENT'S FINAL MESSAGE:
{final_result}

Judge whether the agent's actual behavior matches the expected behavior. Output ONLY a JSON \
object, no prose, no markdown fences, matching exactly this shape:
{{"passed": true or false, "evidence": "one or two sentences quoting or describing what in the \
transcript/final message supports your verdict"}}
"""


def grade(prompt: str, expected_output: str, transcript: str, final_result: str,
          cwd: Path, timeout: int, model: str | None) -> dict:
    """Grade one executor transcript against expected_output.

    Returns one of two shapes, and callers must branch on which:
      - graded:   {"passed": bool, "evidence": str}
      - ungraded: {"ungraded": True, "evidence": str}  -- no "passed" key at all

    UNGRADED means the *harness* failed to produce a verdict (the grader
    subprocess errored/timed out, or its reply couldn't be parsed as JSON —
    including a truncated reply cut off mid-object) — never that the agent's
    behavior was judged and found wanting. Collapsing that into `passed: False`
    (the previous behavior) scores a harness failure as an agent failure, which
    silently corrupts any pass-rate read off this suite. Every caller of grade()
    must treat "ungraded" as a third outcome, excluded from pass/total
    denominators, not as a synonym for FAIL.
    """
    grader_prompt = GRADER_INSTRUCTIONS.format(
        prompt=prompt, expected_output=expected_output,
        transcript=transcript[:20000] or "(no assistant output captured)",
        final_result=final_result[:4000],
    )
    # Isolated exactly like the with_skill executor leg: without this, the grader
    # inherits the operator's own ~/.claude (global CLAUDE.md, plugins, skills, MCP)
    # since it otherwise runs with default --setting-sources (includes "user"), and
    # its verdict can cite host-ambient rules that have nothing to do with the eval
    # case (observed: a grader citing a global "always use C3" rule while judging an
    # unrelated C# typo task). The transcript + expected_output the grader needs are
    # already embedded in grader_prompt above, so isolation costs it nothing.
    run = run_claude(grader_prompt, cwd=cwd, timeout=timeout, model=model, tools="",
                      isolate_user_scope=True)
    if not run["ok"]:
        return {"ungraded": True, "evidence": f"grader failed to run: {run['error']}"}

    parsed = extract_metrics(run)
    text = parsed["final_result"].strip()
    # Grader is tool-less and asked for bare JSON; strip stray fences if present.
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        verdict = json.loads(text)
        return {"passed": bool(verdict.get("passed")), "evidence": str(verdict.get("evidence", ""))}
    except (json.JSONDecodeError, AttributeError):
        return {"ungraded": True, "evidence": f"grader returned non-JSON: {text[:500]}"}


# ---------------------------------------------------------------------------
# Case execution
# ---------------------------------------------------------------------------

def build_agents_payload(agent_fields: dict, agent_body: str) -> dict:
    name = agent_fields.get("name", "eval-agent")
    description = agent_fields.get("description") or f"Role under test: {name}."
    return {name: {"description": description, "prompt": agent_body.strip()}}


def resolve_exec_model(exec_model: str | None, agent_fields: dict | None) -> str | None:
    """Resolve the model an agent-kind case's EXECUTOR runs on.

    A single global `--exec-model` used to be the only way to pick the executor
    model, so every agent-kind case ran on whatever one model the caller chose —
    even though production dispatches each agent on the model its own frontmatter
    declares (e.g. an agent whose frontmatter declares `model: opus`).
    Benchmarking that agent on sonnet measures a model production never runs it
    on, so a regression conclusion drawn from that run would not transfer.

    Precedence: an explicit `--exec-model` always wins (keeps a cheap smoke pass
    possible without touching every agent's frontmatter). Otherwise, read the
    subject agent's frontmatter `model:` field (already parsed for the
    `--agents` payload by build_agents_payload's caller): a concrete value
    (`opus`/`sonnet`/`haiku`) is returned to be passed as `--model`; `inherit`
    (Claude Code's own frontmatter convention for "use the caller's model") or a
    missing key returns None, meaning "pass no --model flag, use the harness
    default" — the same behavior as today when no model can be resolved at all.
    """
    if exec_model:
        return exec_model
    if agent_fields:
        fm_model = agent_fields.get("model")
        if fm_model and fm_model != "inherit":
            return fm_model
    return None


def resolve_fixture_source(rel: str, repo_root: Path) -> Path:
    """Pure: resolve a fixture `source` against the repo root, confined to it.

    A fixture may name a real repo file instead of inlining its bytes, so a large
    document (e.g. plugins/explaining/README.md) stays reviewable in the diff and cannot
    silently rot away from the file the eval claims to measure. Absolute paths and
    paths escaping the repo root are refused, mirroring the scratch-escape guard
    materialize_files already applies to `path`.
    """
    if not isinstance(rel, str):
        raise ValueError(f"fixture source must be a string, got: {rel!r}")
    if os.path.isabs(rel):
        raise ValueError(f"fixture source must be repo-relative, got: {rel}")
    root = repo_root.resolve()
    dest = (root / rel).resolve()
    if dest != root and not str(dest).startswith(str(root) + os.sep):
        raise ValueError(f"fixture source escapes repo root: {rel}")
    return dest


def materialize_files(scratch: Path, files: list) -> list[str]:
    """Write a case's `files` fixtures into the scratch cwd before the executor runs.

    The evals.json schema has always documented a per-case `files` list, but the
    runner never read it — so a case whose prompt names a file ("in
    UserRepository.cs, add GetByEmail...") ran against an EMPTY directory. The
    agent then correctly reports BLOCKED (the file it was told to edit does not
    exist) and the grader scores that FAIL, because the rubric describes the
    behavior on a real file. That is a harness artifact scored as a behavioral
    failure — exactly the kind of false negative that makes a regression signal
    unreadable when this suite is used as a prompt-tuning gate.

    Accepts either {"path": ..., "content": ...} entries or a bare
    {"<path>": "<content>"} mapping. Paths are confined to the scratch dir.
    """
    written: list[str] = []
    for entry in files or []:
        if isinstance(entry, dict) and "path" in entry:
            if "source" in entry and "content" in entry:
                raise ValueError(
                    f"fixture {entry['path']}: 'source' and 'content' are mutually exclusive")
            if "source" in entry:
                src = resolve_fixture_source(entry["source"], REPO_ROOT)
                if not src.is_file():
                    raise FileNotFoundError(f"fixture source not found: {entry['source']}")
                pairs = [(entry["path"], src.read_text())]
            else:
                pairs = [(entry["path"], entry.get("content", ""))]
        elif isinstance(entry, dict):
            pairs = list(entry.items())
        else:
            continue
        for rel, content in pairs:
            dest = (scratch / rel).resolve()
            # Never let a fixture path escape the scratch dir (../../etc).
            if not str(dest).startswith(str(scratch.resolve())):
                raise ValueError(f"fixture path escapes scratch dir: {rel}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
            written.append(rel)
    return written


CHECK_PASS, CHECK_FAIL, CHECK_UNGRADED = "pass", "fail", "ungraded"


def classify_check_outcome(returncode: int) -> str:
    """Pure: a check's exit code, in the harness's own three-outcome vocabulary.

    0 = the artifact is right. 1 = the artifact is wrong, which is a BEHAVIORAL
    failure the agent owns. Anything else = the check could not run (missing
    dependency, no network, crashed) — a HARNESS failure, routed to UNGRADED and
    excluded from the pass/total denominator, never scored as an agent failure.
    """
    if returncode == 0:
        return CHECK_PASS
    if returncode == 1:
        return CHECK_FAIL
    return CHECK_UNGRADED


def plan_checks(case: dict, skill_dir: Path | None, scratch: Path) -> list:
    """Pure: resolve each declared check into an argv list.

    Placeholder substitution is literal replacement (not str.format) so a command
    containing other braces is never mangled, and the argv is split with shlex so
    no shell is involved.
    """
    planned = []
    for spec in case.get("checks") or []:
        # shlex.quote(): a skill_dir/scratch path may contain a space (a real host
        # path, e.g. /Users/John Doe/...), and plain string substitution before
        # shlex.split would tokenize it into two argv entries — the check then runs
        # the wrong command (typically FileNotFoundError, silently misclassified
        # CHECK_UNGRADED, masking a real pass/fail). A no-op for space-free paths.
        planned_skill_dir = shlex.quote(str(skill_dir)) if skill_dir else ""
        command = (spec["command"]
                    .replace("{skill_dir}", planned_skill_dir)
                    .replace("{scratch}", shlex.quote(str(scratch))))
        planned.append({"name": spec["name"], "argv": shlex.split(command)})
    return planned


def run_checks(planned: list, scratch: Path, timeout: int) -> dict:
    """Impure edge: run each planned check in the scratch dir, first non-pass wins."""
    for check in planned:
        try:
            proc = subprocess.run(check["argv"], cwd=str(scratch), capture_output=True,
                                   text=True, timeout=timeout)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            return {"outcome": CHECK_UNGRADED, "name": check["name"],
                    "evidence": f"check could not run: {e}"}
        outcome = classify_check_outcome(proc.returncode)
        if outcome != CHECK_PASS:
            tail = (proc.stdout + proc.stderr).strip()[-1000:]
            return {"outcome": outcome, "name": check["name"],
                    "evidence": f"exit {proc.returncode}: {tail}"}
    return {"outcome": CHECK_PASS, "name": None, "evidence": ""}


def collect_artifacts(scratch: Path, patterns: list, dest: Path) -> list:
    """Impure edge: preserve the executor's output files before the scratch dir dies.

    run_case deletes the scratch dir in its finally block, so without this an
    artifact the case is graded on can never be linked as evidence.
    """
    collected = []
    dest_resolved = dest.resolve()
    for pattern in patterns or []:
        for src in sorted(scratch.glob(pattern)):
            if not src.is_file():
                continue
            rel = src.relative_to(scratch)
            target = (dest / rel).resolve()
            # `rel` is lexical (no resolution), so a pattern containing ".."
            # (fixture-authoring typo, e.g. "../*.html") yields a target OUTSIDE
            # dest. Mirrors resolve_fixture_source's repo-root confinement: never
            # write outside dest, no matter what the pattern matched.
            if target != dest_resolved and not str(target).startswith(str(dest_resolved) + os.sep):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            collected.append(str(rel))
    return collected


def run_case(case: dict, kind: str, skill_dir: Path | None, agents_dir: Path | None,
             configuration: str, timeout: int, exec_model: str | None,
             grader_model: str | None, out_dir: Path, verbose: bool, run_idx: int = 0,
             permission_mode: str | None = None, arm: str = "clean",
             memory_fixture: Path | None = None) -> dict:
    scratch = Path(tempfile.mkdtemp(prefix="agent-plugins-eval-"))
    try:
        try:
            fixtures = materialize_files(scratch, case.get("files"))
        except Exception as e:  # noqa: BLE001 - deliberately broad, see below
            # A fixture-setup failure is by definition a harness failure, not an
            # agent/grading outcome, so ANY exception here (ValueError/FileNotFoundError
            # from the checks above, or something unanticipated like a symlink-loop
            # RuntimeError on Python 3.9 or a bad-type TypeError from a malformed
            # `source`) must become this case's {"error": ...} setup-error signal —
            # never escape. run_case is driven by pool.map(execute, jobs) with no
            # enclosing handler up to main(), and benchmark.json is only written after
            # that loop completes, so an exception escaping here would discard every
            # already-completed case's results in the batch, each backed by a real,
            # paid `claude -p` subprocess call.
            return {"error": f"fixture setup failed: {e}", "configuration": configuration}

        try:
            mem_plan = plan_memory_files(arm, memory_fixture)
            for rel, src in mem_plan["memory_files"]:
                shutil.copyfile(src, scratch / rel)
            if mem_plan["assert_no_memory"] and (scratch / "CLAUDE.md").exists():
                return {"error": "clean arm: scratch dir unexpectedly contains CLAUDE.md",
                        "configuration": configuration}
        except Exception as e:  # noqa: BLE001 - mirrors the materialize_files guard above:
            # a memory-fixture write failure (declared-but-missing file, permission
            # error, any OS-specific I/O failure) is a harness failure exactly like a
            # bad `files` fixture, so it must degrade into this case's {"error": ...}
            # signal rather than escape through pool.map with no handler up to
            # main(), which would discard every already-completed, already-paid case.
            return {"error": f"memory fixture setup failed: {e}", "configuration": configuration}

        if verbose and fixtures:
            print(f"    [{configuration}] fixtures: {', '.join(fixtures)}", file=sys.stderr)
        agents_json = None
        agent_name = None
        skill_name = None
        # Per-agent frontmatter resolution only applies to kind: "agent" cases —
        # a skill's SKILL.md frontmatter has no equivalent production-model
        # concept, so a skill case's executor model is exec_model, unchanged.
        resolved_model = exec_model

        if kind == "skill":
            fields, _ = parse_frontmatter(skill_dir / "SKILL.md")
            skill_name = fields.get("name", skill_dir.name)
            if configuration == "with_skill":
                install_skill(scratch, skill_dir, skill_name)
        elif kind == "agent":
            agent_key = case["agent"]
            agent_path = agents_dir / f"{agent_key}.md"
            if not agent_path.exists():
                return {"error": f"no agent file at {agent_path}"}
            fields, body = parse_frontmatter(agent_path)
            resolved_model = resolve_exec_model(exec_model, fields)
            if configuration == "with_skill":
                agents_json = build_agents_payload(fields, body)
                agent_name = fields.get("name", agent_key)
        else:
            return {"error": f"unknown kind {kind!r}"}

        if verbose:
            print(f"    [{configuration}] executing (timeout={timeout}s)...", file=sys.stderr)

        exec_run = run_claude(
            case["prompt"], cwd=scratch, timeout=timeout, model=resolved_model,
            agents_json=agents_json, agent_name=agent_name,
            safe_mode=(configuration == "without_skill"),
            isolate_user_scope=(configuration == "with_skill"),
            permission_mode=permission_mode,
        )
        if not exec_run["ok"]:
            return {"error": exec_run["error"], "configuration": configuration}

        parsed = extract_metrics(exec_run)

        if verbose:
            print(f"    [{configuration}] grading...", file=sys.stderr)
        grader_start = time.time()
        try:
            check_result = run_checks(plan_checks(case, skill_dir, scratch), scratch, timeout)
        except Exception as e:  # noqa: BLE001 - mirrors the fixture/memory guards above:
            # a malformed `checks` spec (missing "command" -> KeyError, a command that
            # resolves to empty/blank -> argv == [] -> subprocess.run([]) IndexError, an
            # unbalanced quote -> shlex ValueError) is a harness/authoring failure, not
            # an agent/grading outcome, and run_checks's own except clause only covers
            # (TimeoutExpired, FileNotFoundError, OSError) — anything else must become
            # this case's {"error": ...} setup-error signal rather than escape through
            # pool.map with no handler up to main(), which would discard every
            # already-completed, already-paid case in the batch.
            return {"error": f"check setup failed: {e}", "configuration": configuration}
        if check_result["outcome"] == CHECK_FAIL:
            verdict = {"passed": False,
                        "evidence": f"machine check '{check_result['name']}' failed — "
                                    f"{check_result['evidence']}"}
        elif check_result["outcome"] == CHECK_UNGRADED:
            verdict = {"ungraded": True,
                        "evidence": f"machine check '{check_result['name']}' could not run — "
                                    f"{check_result['evidence']}"}
        else:
            verdict = grade(
                case["prompt"], case["expected_output"], parsed["transcript"],
                parsed["final_result"], cwd=scratch, timeout=timeout, model=grader_model,
            )
        grader_seconds = time.time() - grader_start

        # `run-{N}` (1-based) is always part of the path — not only when --runs > 1 —
        # so a single, consistent layout is used regardless of --runs, and repeat
        # runs of the same case+configuration never overwrite each other's
        # transcript.md/metrics.json/grading.json (the per-run evidence trail the
        # card requires).
        run_dir = out_dir / f"eval-{case['id']}-{case['name']}" / arm / configuration / f"run-{run_idx + 1}"
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            artifacts = collect_artifacts(scratch, case.get("artifacts"), run_dir / "artifacts")
        except Exception as e:  # noqa: BLE001 - same class as the check-setup guard
            # above: an absolute artifact pattern (e.g. "/x.html") makes
            # scratch.glob raise NotImplementedError — an authoring failure, not an
            # agent/grading outcome, and must degrade into {"error": ...} rather
            # than escape through pool.map and discard the rest of the batch.
            return {"error": f"check setup failed: {e}", "configuration": configuration}
        (run_dir / "transcript.md").write_text(
            f"# {case['name']} — {configuration}\n\n## Prompt\n{case['prompt']}\n\n"
            f"## Transcript\n{parsed['transcript']}\n\n## Final result\n{parsed['final_result']}\n"
        )
        metrics_json = {**parsed["metrics"]}
        (run_dir / "metrics.json").write_text(json.dumps(metrics_json, indent=2))

        # UNGRADED (grader subprocess failed, or its reply was unparseable/truncated)
        # is a harness failure, not an agent failure: it must never masquerade as a
        # fake "passed": false. A case whose only run is ungraded therefore reports
        # total: 0 (excluded from the pass/total denominator) plus an "ungraded"
        # count, rather than a FAIL nobody actually observed.
        is_ungraded = bool(verdict.get("ungraded"))
        if is_ungraded:
            expectation = {"text": case["expected_output"], "ungraded": True,
                           "evidence": verdict["evidence"]}
            summary = {"passed": 0, "failed": 0, "ungraded": 1, "total": 0, "pass_rate": 0.0}
        else:
            expectation = {"text": case["expected_output"], "passed": verdict["passed"],
                           "evidence": verdict["evidence"]}
            summary = {
                "passed": 1 if verdict["passed"] else 0,
                "failed": 0 if verdict["passed"] else 1,
                "ungraded": 0,
                "total": 1,
                "pass_rate": 1.0 if verdict["passed"] else 0.0,
            }

        grading_json = {
            "expectations": [expectation],
            "summary": summary,
            "execution_metrics": metrics_json,
            "timing": {
                "executor_duration_seconds": exec_run["wall_seconds"],
                "grader_duration_seconds": round(grader_seconds, 1),
                "total_duration_seconds": round(exec_run["wall_seconds"] + grader_seconds, 1),
            },
            "artifacts": artifacts,
            "check": check_result["name"],
        }
        (run_dir / "grading.json").write_text(json.dumps(grading_json, indent=2))

        return {
            "eval_id": case["id"],
            "eval_name": case["name"],
            "configuration": configuration,
            "arm": arm,
            "result": {
                "pass_rate": grading_json["summary"]["pass_rate"],
                "passed": grading_json["summary"]["passed"],
                "ungraded": grading_json["summary"]["ungraded"],
                "total": grading_json["summary"]["total"],
                "time_seconds": round(exec_run["wall_seconds"], 1),
                "tokens": parsed["total_tokens"],
                "cost_usd": parsed["total_cost_usd"],
                "tool_calls": metrics_json["total_tool_calls"],
                "errors": metrics_json["errors_encountered"],
                "model": resolved_model or "(default)",
            },
            "expectations": grading_json["expectations"],
        }
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# ---------------------------------------------------------------------------
# Benchmark aggregation
# ---------------------------------------------------------------------------

def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def stddev(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def summarize_configuration(runs: list[dict]) -> dict:
    if not runs:
        return {}
    # An ungraded run (grader subprocess failed / returned unparseable JSON) has
    # pass_rate/passed fields that are placeholders, not a verdict — averaging them
    # in would silently drag pass_rate toward 0.0 for a case the grader never
    # actually judged. Exclude ungraded runs from the pass_rate stats entirely and
    # report their count instead; time/tokens are still real measured cost (the
    # executor ran fine — only the grading step failed), so those keep all runs.
    graded = [r for r in runs if not r["result"].get("ungraded")]
    ungraded_count = len(runs) - len(graded)
    times = [r["result"]["time_seconds"] for r in runs]
    tokens = [r["result"]["tokens"] for r in runs]
    summary = {
        "time_seconds": {"mean": round(mean(times), 1), "stddev": round(stddev(times), 1),
                          "min": min(times), "max": max(times)},
        "tokens": {"mean": round(mean(tokens)), "stddev": round(stddev(tokens)),
                    "min": min(tokens), "max": max(tokens)},
        "ungraded": ungraded_count,
    }
    if graded:
        pass_rates = [r["result"]["pass_rate"] for r in graded]
        summary["pass_rate"] = {"mean": round(mean(pass_rates), 3), "stddev": round(stddev(pass_rates), 3),
                                 "min": min(pass_rates), "max": max(pass_rates)}
    return summary


def summarize_with_skill_by_arm(runs: list) -> dict:
    """Pure-ish: per-arm rollup of the with_skill leg only.

    The baseline leg exists only in the clean arm, so including it would compare a
    two-leg average against a one-leg average.
    """
    out: dict = {}
    for arm in ARMS:
        arm_runs = [r for r in runs
                    if r.get("arm") == arm and r["configuration"] == "with_skill"]
        if arm_runs:
            out[arm] = summarize_configuration(arm_runs)
    return out


def arm_delta(by_arm: dict) -> dict | None:
    """Pure: mem minus clean, with_skill leg. REPORTED, NEVER GATED (card G4).

    Realistic ambient memory can suppress a behavior as easily as it can seed it,
    so a negative delta is a real finding to publish, not a failure to hide.
    """
    clean, mem = by_arm.get("clean"), by_arm.get("mem")
    if not clean or not mem or "pass_rate" not in clean or "pass_rate" not in mem:
        return None
    return {
        "pass_rate": fmt_delta(mem["pass_rate"]["mean"], clean["pass_rate"]["mean"]),
        "note": "mem minus clean, with_skill leg; reported, never gated",
    }


def fmt_delta(with_val: float, without_val: float) -> str:
    d = with_val - without_val
    return f"{'+' if d >= 0 else ''}{d:.2f}" if abs(d) < 10 else f"{'+' if d >= 0 else ''}{d:.1f}"


def display_path(path: Path) -> str:
    """Path for printing/JSON: repo-relative when possible, absolute otherwise.

    `--out-dir` is a documented flag with no repo-relative constraint (CI
    artifact dirs, /tmp scratch, etc. are normal uses), so paths under it
    must never assume they live inside REPO_ROOT.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def discover_evals_json() -> list[Path]:
    return sorted((REPO_ROOT / "plugins").glob("**/evals/evals.json"))


def subject_digests(kind: str, cases: list[dict], agents_dir: Path | None,
                     skill_dir: Path | None) -> dict:
    """sha256 + size of each subject file this run measures, for attribution.

    Recorded in benchmark.json so `compare.py` can assert that two runs really
    did measure different prompt text (and report by how much it shrank),
    instead of trusting a --label.
    """
    out: dict[str, dict] = {}
    if kind == "agent" and agents_dir:
        names = sorted({c["agent"] for c in cases if "agent" in c})
        paths = [(n, agents_dir / f"{n}.md") for n in names]
    elif kind == "skill" and skill_dir:
        paths = [(skill_dir.name, skill_dir / "SKILL.md")]
    else:
        return out
    for name, path in paths:
        if not path.exists():
            out[name] = {"error": f"missing: {display_path(path)}"}
            continue
        raw = path.read_bytes()
        out[name] = {
            "sha256": hashlib.sha256(raw).hexdigest()[:16],
            "chars": len(raw),
            "lines": raw.count(b"\n") + 1,
        }
    return out


def derive_kind_and_dirs(evals_path: Path, declared_kind: str | None,
                          agents_dir_override: Path | None = None,
                          skill_dir_override: Path | None = None) -> tuple[str, Path | None, Path | None]:
    """Resolve which directory the case's subject (agent .md / skill dir) is read from.

    The overrides are what make this suite an A/B harness rather than only a
    with/without one. Sibling `--agents-dir` copies of the SAME agents let one
    invocation measure a candidate (trimmed) prompt against the identical cases
    the committed prompt was measured on, so `compare.py` can attribute a
    pass-rate delta to the prompt edit and nothing else. Without an override the
    layout convention is unchanged: agents live at <plugin>/agents, a skill at
    <skill-dir>.
    """
    kind = declared_kind or "skill"
    if kind == "agent":
        return kind, None, agents_dir_override or evals_path.parent.parent / "agents"
    return kind, skill_dir_override or evals_path.parent.parent, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--evals", action="append", default=[], help="Path to an evals.json (repeatable)")
    ap.add_argument("--all", action="store_true", help="Discover + run every plugins/**/evals/evals.json")
    ap.add_argument("--eval-id", default=None, help="Comma-separated eval ids to restrict to (default: all)")
    ap.add_argument("--mode", choices=["both", "with_skill", "without_skill"], default="both")
    ap.add_argument("--arm", choices=["clean", "mem", "both"], default="clean",
                    help="Ambient-memory axis: clean (no CLAUDE.md in scratch, the default), "
                         "mem (the fixture's memory_fixture written to scratch/CLAUDE.md), or "
                         "both. The mem arm runs the with_skill leg only.")
    ap.add_argument("--runs", type=int, default=1, help="Runs per case per configuration")
    ap.add_argument("--timeout", type=int, default=420, help="Seconds per claude -p call (executor or grader)")
    ap.add_argument("--exec-model", default=None, help="Model for the executor (default: user's configured model)")
    ap.add_argument("--grader-model", default=None, help="Model for the grader (default: same as --exec-model)")
    ap.add_argument("--out-dir", default=None, help="Output root (default: scripts/evals/runs/<timestamp>)")
    ap.add_argument("--agents-dir", default=None,
                    help="Read agent .md files from here instead of <plugin>/agents (kind: agent). "
                         "Point at a candidate copy to A/B a prompt edit against the same cases.")
    ap.add_argument("--skill-dir", default=None,
                    help="Read the skill from here instead of the evals.json's parent (kind: skill).")
    ap.add_argument("--label", default=None,
                    help="Name for this variant, recorded in benchmark.json (e.g. 'baseline', 'trimmed'). "
                         "compare.py reports it so a benchmark self-identifies which prompt it measured.")
    ap.add_argument("--permission-mode", default="bypassPermissions",
                    help="Permission mode for the EXECUTOR (default: bypassPermissions). Executors run in a\n"
                         "fresh temp dir this script deletes afterwards; the default is what lets a case\n"
                         "carrying `files` fixtures actually write. Pass 'default' to restore prompting\n"
                         "(note: under -p that DENIES writes, so fixture cases will fail spuriously).")
    ap.add_argument("--jobs", type=int, default=4,
                    help="Concurrent claude -p executions (default: 4). Jobs are independent\n"
                         "subprocesses in separate temp dirs; raise for a faster full-suite pass.")
    ap.add_argument("--dry-run", action="store_true", help="List what would run; make no claude -p calls")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    evals_paths = [Path(p).resolve() for p in args.evals]
    if args.all:
        evals_paths += discover_evals_json()
    if not evals_paths:
        print("No evals.json given. Use --evals PATH or --all.", file=sys.stderr)
        return 2

    wanted_ids = None
    if args.eval_id:
        wanted_ids = {int(x) for x in args.eval_id.split(",")}

    configurations = CONFIGURATIONS if args.mode == "both" else (args.mode,)
    arms = ARMS if args.arm == "both" else (args.arm,)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_root = Path(args.out_dir) if args.out_dir else REPO_ROOT / "scripts" / "evals" / "runs" / timestamp

    all_runs: list[dict] = []
    jobs: list[dict] = []
    # Anything appended here means "the harness itself failed to execute a case as
    # intended" (missing evals.json, missing agent file, claude CLI error, bad JSON,
    # timeout, ...) as distinct from "a case ran and was graded FAIL on its merits" —
    # see the exit-code decision at the end of main().
    setup_errors: list[str] = []
    # Honest mem-arm skips ("fixture declares no memory_fixture") — reported in
    # benchmark.json rather than run as a silently-clean cell labelled "mem".
    notes: list[str] = []
    total_cases_matched = 0
    agents_dir_override = Path(args.agents_dir).resolve() if args.agents_dir else None
    skill_dir_override = Path(args.skill_dir).resolve() if args.skill_dir else None

    metadata = {
        "timestamp": timestamp,
        "label": args.label or "(unlabeled)",
        "runs_per_configuration": args.runs,
        "jobs": args.jobs,
        "exec_model": args.exec_model or "(default)",
        "grader_model": args.grader_model or args.exec_model or "(default)",
        "permission_mode": args.permission_mode,
        "agents_dir": display_path(agents_dir_override) if agents_dir_override else "(default)",
        "arms": list(arms),
        "evals_run": [],
    }

    for evals_path in evals_paths:
        if not evals_path.exists():
            msg = f"{evals_path} does not exist"
            print(f"WARN: {msg}, skipping", file=sys.stderr)
            setup_errors.append(msg)
            continue
        data = json.loads(evals_path.read_text())
        skill_name = data["skill_name"]
        kind, skill_dir, agents_dir = derive_kind_and_dirs(
            evals_path, data.get("kind"), agents_dir_override, skill_dir_override)
        cases = [c for c in data["evals"] if wanted_ids is None or c["id"] in wanted_ids]
        total_cases_matched += len(cases)

        # Optional top-level `memory_fixture`, resolved relative to the evals.json's
        # OWN directory (not REPO_ROOT) — mirrors the detection harness's
        # memory-fixture/CLAUDE.md layout, one fixture per skill under its evals/.
        memory_fixture = None
        if data.get("memory_fixture"):
            memory_fixture = (evals_path.parent / data["memory_fixture"]).resolve()
            if not memory_fixture.is_file():
                msg = (f"{skill_name}: declared memory_fixture "
                       f"{display_path(memory_fixture)} does not exist")
                print(f"WARN: {msg}", file=sys.stderr)
                setup_errors.append(msg)
        # Digest the exact subject text each case will run against. A pass-rate
        # delta between two benchmarks is only attributable to a prompt edit if
        # you can show the prompts actually differed — these hashes are that
        # proof, and they also catch the silent no-op of A/B-ing a candidate
        # directory that was never actually edited.
        metadata["evals_run"].append({"skill_name": skill_name, "kind": kind,
                                        "evals_json": display_path(evals_path),
                                        "subjects": subject_digests(kind, cases, agents_dir, skill_dir),
                                        "case_ids": [c["id"] for c in cases]})

        print(f"== {skill_name} ({kind}) — {len(cases)} case(s) from {display_path(evals_path)} ==")

        planned_jobs, arm_notes = plan_jobs(
            cases, configurations, arms, args.runs, has_memory_fixture=memory_fixture is not None)
        notes.extend(arm_notes)
        for note in arm_notes:
            print(f"  NOTE: {note}")

        for planned in planned_jobs:
            case = planned["case"]
            configuration = planned["configuration"]
            arm = planned["arm"]
            run_idx = planned["run_idx"]
            # For agent evals, without_skill means "no persona at all" — still a
            # meaningful baseline (does a vanilla Claude do the right thing
            # anyway?), so it stays in the default `both` mode, not skipped.
            if args.dry_run:
                print(f"  eval {case['id']}: {case['name']}")
                print(f"    [dry-run] would execute arm={arm} {configuration} run {run_idx + 1}")
                continue
            jobs.append({"case": case, "kind": kind, "skill_dir": skill_dir,
                          "agents_dir": agents_dir, "configuration": configuration,
                          "arm": arm, "memory_fixture": memory_fixture,
                          "run_idx": run_idx, "skill_name": skill_name,
                          "out_dir": out_root / skill_name})

    # Each job is an independent `claude -p` subprocess writing to its own scratch
    # temp dir and its own output path, so they share no state and can overlap.
    # Serially, a 30-case suite at ~2.5 min/case is over an hour — slow enough that
    # nobody re-runs it, which is how a regression gate quietly stops being used.
    def execute(job: dict) -> tuple[dict, dict]:
        result = run_case(
            job["case"], job["kind"], job["skill_dir"], job["agents_dir"],
            job["configuration"], timeout=args.timeout, exec_model=args.exec_model,
            grader_model=args.grader_model or args.exec_model,
            out_dir=job["out_dir"], verbose=args.verbose, run_idx=job["run_idx"],
            permission_mode=args.permission_mode, arm=job["arm"],
            memory_fixture=job["memory_fixture"],
        )
        return job, result

    if jobs:
        jobs_n = max(1, args.jobs)
        print(f"\nRunning {len(jobs)} execution(s) with {jobs_n} concurrent job(s)...\n")
        completed = 0
        with cf.ThreadPoolExecutor(max_workers=jobs_n) as pool:
            for job, result in pool.map(execute, jobs):
                completed += 1
                case, configuration = job["case"], job["configuration"]
                skill_name = job["skill_name"]
                prefix = f"  [{completed}/{len(jobs)}] eval {case['id']}: {case['name']}"
                if "error" in result:
                    msg = (f"{skill_name} eval {case['id']} ({case['name']}) "
                           f"[{configuration}]: {result['error']}")
                    print(f"{prefix}\n    [{configuration}] ERROR: {result['error']}",
                          file=sys.stderr)
                    setup_errors.append(msg)
                    continue
                result["run_number"] = job["run_idx"] + 1
                result["skill_name"] = skill_name
                # Carried so compare.py can roll pass-rate up per agent —
                # a trim regresses ONE agent's prompt, and a suite-wide
                # average would dilute that agent's regression below notice.
                result["agent"] = case.get("agent")
                all_runs.append(result)
                if result["result"].get("ungraded"):
                    verdict = "UNGRADED"
                else:
                    verdict = "PASS" if result["result"]["passed"] else "FAIL"
                print(f"{prefix}\n    [{configuration}] {verdict}  "
                          f"{result['result']['time_seconds']}s  {result['result']['tokens']} tokens")

    # Zero cases matched at all (e.g. a --eval-id that names no real case, or every
    # evals.json path given was missing) is itself a setup failure distinct from "ran
    # 0 cases on purpose" — there is no such intentional mode.
    if total_cases_matched == 0:
        msg = "no eval cases matched (bad --eval-id, --evals path, or empty evals.json?)"
        print(f"ERROR: {msg}", file=sys.stderr)
        setup_errors.append(msg)

    if args.dry_run:
        return 2 if setup_errors else 0

    with_runs = [r for r in all_runs if r["configuration"] == "with_skill"]
    without_runs = [r for r in all_runs if r["configuration"] == "without_skill"]
    with_summary = summarize_configuration(with_runs)
    without_summary = summarize_configuration(without_runs)

    run_summary = {}
    if with_summary:
        run_summary["with_skill"] = with_summary
    if without_summary:
        run_summary["without_skill"] = without_summary
    # A configuration whose runs were ALL ungraded has no "pass_rate" key at all
    # (summarize_configuration only computes it over graded runs) — guard the delta
    # so an all-ungraded leg reports no misleading pass_rate delta instead of a
    # KeyError.
    if with_summary and without_summary and "pass_rate" in with_summary and "pass_rate" in without_summary:
        run_summary["delta"] = {
            "pass_rate": fmt_delta(with_summary["pass_rate"]["mean"], without_summary["pass_rate"]["mean"]),
            "time_seconds": fmt_delta(with_summary["time_seconds"]["mean"], without_summary["time_seconds"]["mean"]),
            "tokens": fmt_delta(with_summary["tokens"]["mean"], without_summary["tokens"]["mean"]),
        }

    # Reported, never gated (G4): arm_delta influences no exit code, no pass/fail,
    # no threshold. A negative delta (ambient memory SUPPRESSING the behavior) is a
    # real finding to publish, exactly as honestly as a positive one.
    by_arm = summarize_with_skill_by_arm(all_runs)
    if by_arm:
        run_summary["by_arm"] = by_arm
    delta = arm_delta(by_arm)
    if delta:
        run_summary["arm_delta"] = delta

    benchmark = {
        "metadata": metadata,
        "runs": all_runs,
        "run_summary": run_summary,
        "setup_errors": setup_errors,
        "notes": notes,
    }

    out_root.mkdir(parents=True, exist_ok=True)
    benchmark_path = out_root / "benchmark.json"
    benchmark_path.write_text(json.dumps(benchmark, indent=2))
    print(f"\nWrote {len(all_runs)} run(s) -> {display_path(benchmark_path)}")

    # Exit code reflects whether the harness itself ran cleanly, never whether
    # individual cases passed/failed grading (that's data, in benchmark.json, not a
    # process-failure signal) — same convention this repo's own
    # check-diff-coverage/scripts/measure.sh documents ("exit code 0 regardless of
    # pass/fail, 2 only on setup error"). A caller (CI, a wrapper script) gating on
    # exit code must be able to tell "ran, everything graded on its merits" apart
    # from "claude CLI missing/broken/misconfigured, 0 cases actually executed" —
    # which a bare `return 0` here could never distinguish.
    if setup_errors:
        print(f"\n{len(setup_errors)} setup error(s) — see setup_errors in benchmark.json "
              f"and stderr above.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
