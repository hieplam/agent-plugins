#!/usr/bin/env python3
"""Run every case under every arm: one isolated `claude -p` session per cell run.

    run.py --cases DIR --arms arms.json --memory owner-memory.md --out RUN_DIR [options]

Each session gets a fresh folder holding the case's files (or a snapshot of the repo at the
case's commit), the owner's memory as project memory, and the arm's output style. It runs in
bypassPermissions mode, as the owner does, but its shell is sandboxed (writes only in its folder and
the temp area, no network) and its file tools may not write under the home folder, so it can change
nothing real (core.session_settings).

The run is resumable: a cell run whose result file already says `ok` is skipped, so re-running the
same command after a usage limit picks up where it stopped. A usage limit stops new sessions from
starting and exits 3.

Exit codes: 0 every planned session ran; 1 a session ran under the wrong style or model; 2 bad
input; 3 stopped by a usage limit.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import hashlib
import shutil
import sys
import tempfile
import threading
from pathlib import Path

import core
import edge

HERE = Path(__file__).resolve().parent


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--cases", required=True, help="folder of case JSON files")
    p.add_argument("--arms", default=str(HERE / "arms.json"), help="arm definitions (default: arms.json here)")
    p.add_argument("--memory", required=True, help="the owner's global CLAUDE.md, given to every session")
    p.add_argument("--out", required=True, help="run folder; results land under OUT/sessions/")
    p.add_argument("--style-repo", default=str(HERE.parents[2]), help="git repo holding the styles (default: this repo)")
    p.add_argument("--repos-root", default=str(Path.home() / "repos"), help="where the cases' repos are cloned")
    p.add_argument("--scratch-root", default=tempfile.gettempdir(), help="where session folders are made")
    p.add_argument("--runs", type=int, default=1, help="runs per cell (default 1)")
    p.add_argument("--jobs", type=int, default=3, help="sessions at once (default 3)")
    p.add_argument("--model", default="claude-opus-5-5")
    p.add_argument("--effort", default="medium")
    p.add_argument("--budget", type=float, default=5.0, help="max USD one session may spend (default 5)")
    p.add_argument("--timeout", type=int, default=1800, help="seconds before a session is killed (default 1800)")
    p.add_argument("--only-case", default="", help="comma-separated case ids")
    p.add_argument("--only-arm", default="", help="comma-separated arm names")
    p.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    return p.parse_args(argv)


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def manifest(args, cases, arms, styles, memory_text):
    return {
        "model": args.model, "effort": args.effort,
        "claude_code": edge.claude_version(),
        "tool_commit": edge.git(HERE, "rev-parse", "HEAD").strip(),
        "memory_sha256": sha256_text(memory_text),
        "arms": {arm["name"]: (None if styles[arm["name"]] is None else
                               {"ref": styles[arm["name"]]["ref"], "name": styles[arm["name"]]["name"],
                                "sha256": styles[arm["name"]]["sha256"]}) for arm in arms},
        "cases": {case["id"]: sha256_text(core.build_session_prompt(case)) for case in cases},
    }


def check_manifest(out, current):
    """A run folder holds one experiment. Resuming it with a different model, effort, memory or style
    would mix two experiments in one table, so that is refused."""
    path = Path(out) / "run.json"
    if not path.exists():
        current["started"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        edge.write_json(path, current)
        return
    previous = edge.read_json(path)
    for key in ("model", "effort", "memory_sha256"):
        if previous.get(key) != current[key]:
            raise edge.EdgeError(f"{path}: this run folder used {key}={previous.get(key)!r}; "
                                 f"now {current[key]!r}. Use a new --out folder.")
    for arm, spec in current["arms"].items():
        if arm in previous.get("arms", {}) and previous["arms"][arm] != spec:
            raise edge.EdgeError(f"{path}: arm {arm} now has a different style. Use a new --out folder.")
    for case_id, digest in current["cases"].items():
        if case_id in previous.get("cases", {}) and previous["cases"][case_id] != digest:
            raise edge.EdgeError(f"{path}: case {case_id} changed since this run started. Use a new --out folder.")
    previous["arms"].update(current["arms"])
    previous["cases"].update(current["cases"])
    edge.write_json(path, previous)


def result_path(out, case_id, arm, run):
    return edge.cell_dir(out, case_id, arm) / f"run-{run}.json"


def already_ok(out, case_id, arm, run):
    path = result_path(out, case_id, arm, run)
    if not path.exists():
        return False
    try:
        return edge.read_json(path).get("status") == "ok"
    except edge.EdgeError:
        return False


def run_one(args, case, arm, run, style, memory_text, stop):
    if stop.is_set():
        return "skipped"
    scratch = Path(tempfile.mkdtemp(prefix=f"barrier-{case['id'][:20]}-", dir=args.scratch_root))
    try:
        edge.prepare_scratch(scratch, case, style, memory_text, args.repos_root)
        before = edge.file_states(scratch)
        argv = core.build_session_command(args.model, args.effort, args.budget)
        started = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        # The Todd way style writes its pages and review logs under $EXPLAINING_ARTIFACTS: keep them in
        # the session's own folder, where the sandbox lets it write and copy_written_files finds them.
        env = dict(core.SESSION_ENV, EXPLAINING_ARTIFACTS=str(scratch / ".artifacts"))
        proc = edge.run_claude(argv, core.build_session_prompt(case), scratch, args.timeout, env)
        parsed = core.parse_stream(proc["stdout"].splitlines())
        if proc["timed_out"]:
            status, reason = "error", f"killed after {args.timeout}s"
        else:
            status, reason = core.classify_session(parsed, style["name"] if style else core.NO_STYLE, args.model)
        cell = edge.cell_dir(args.out, case["id"], arm["name"])
        cell.mkdir(parents=True, exist_ok=True)
        files = edge.copy_written_files(scratch, before, cell / f"run-{run}.files")
        result = parsed.get("result") or {}
        record = {
            "case": case["id"], "arm": arm["name"], "run": run, "status": status, "reason": reason,
            "started": started, "reply": parsed["reply"], "files_written": files,
            "init": parsed["init"], "tool_uses": parsed["tool_uses"],
            "cost_usd": result.get("total_cost_usd"), "duration_ms": result.get("duration_ms"),
            "num_turns": result.get("num_turns"), "models": sorted((result.get("modelUsage") or {}).keys()),
            "metrics": core.reply_metrics(parsed["reply"], case) if parsed["reply"] else None,
            "stderr_tail": proc["stderr"][-1500:],
        }
        (cell / f"run-{run}.stream.jsonl").write_text(proc["stdout"], encoding="utf-8")
        edge.write_json(result_path(args.out, case["id"], arm["name"], run), record)
        if status == "rate_limited":
            stop.set()
        return status
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        cases = edge.load_cases(args.cases, [c for c in args.only_case.split(",") if c])
        arms = edge.load_arms(args.arms, [a for a in args.only_arm.split(",") if a])
        memory_text = Path(args.memory).read_text(encoding="utf-8")
        styles = {arm["name"]: edge.resolve_style(arm, args.style_repo) for arm in arms}
    except (edge.EdgeError, OSError, UnicodeDecodeError) as exc:
        print(f"run.py: {exc}", file=sys.stderr)
        return 2
    jobs = [(case, arm, run) for case in cases for arm in arms for run in range(1, args.runs + 1)
            if not already_ok(args.out, case["id"], arm["name"], run)]
    print(f"{len(cases)} case(s) x {len(arms)} arm(s) x {args.runs} run(s): {len(jobs)} session(s) to run "
          f"on {args.model}, effort {args.effort}")
    if args.dry_run:
        for case, arm, run in jobs:
            print(f"  {case['id']:<40} {arm['name']:<22} run {run}")
        return 0
    try:
        check_manifest(args.out, manifest(args, cases, arms, styles, memory_text))
    except edge.EdgeError as exc:
        print(f"run.py: {exc}", file=sys.stderr)
        return 2

    stop = threading.Event()
    counts = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = {pool.submit(run_one, args, case, arm, run, styles[arm["name"]], memory_text, stop):
                   (case["id"], arm["name"], run) for case, arm, run in jobs}
        for future in concurrent.futures.as_completed(futures):
            case_id, arm_name, run = futures[future]
            try:
                status = future.result()
            except (edge.EdgeError, OSError) as exc:
                status = "error"
                print(f"  {case_id} {arm_name} run {run}: {exc}", file=sys.stderr)
            counts[status] = counts.get(status, 0) + 1
            print(f"  {status:<16} {case_id:<40} {arm_name:<22} run {run}", flush=True)
    print("done: " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())))
    if stop.is_set():
        print("stopped by a usage limit; run the same command again after it resets to resume.")
        return 3
    return 1 if counts.get("isolation_breach") else 0


if __name__ == "__main__":
    sys.exit(main())
