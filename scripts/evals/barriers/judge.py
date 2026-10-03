#!/usr/bin/env python3
"""Grade every reply in a run folder with a blind judge.

    judge.py --cases DIR --out RUN_DIR --model JUDGE_MODEL [--effort medium] [--jobs 4]

The judge is a separate `claude -p` session with no tools and no customization. It sees the case
(the message, what the assistant could see, the owner's real follow-up and what it shows was
missing) and ONE reply, never the arm that wrote it. It answers whether the owner would still need
that follow-up, whether the message is answered, and which claims are wrong.

It also grades the calibration replies: every case's stub, which must come out as "follow-up still
needed", and each replay case's real original reply. report.py explains how each is used.

Verdicts land in RUN_DIR/verdicts/<judge model>/<case>/<arm>/run-<n>.json. Resumable; exit 3 on a
usage limit.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import tempfile
import threading
from pathlib import Path

import core
import edge

FILE_CHARS = 8000


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--cases", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--model", required=True, help="judge model, e.g. claude-sonnet-5-5")
    p.add_argument("--effort", default="medium")
    p.add_argument("--jobs", type=int, default=4)
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--only-case", default="")
    return p.parse_args(argv)


def reply_with_files(record, cell):
    """The reply as the owner would read it: the text, plus any page or note the session wrote and
    named (a Todd way reply may point at an HTML page instead of repeating it)."""
    parts = [record["reply"]]
    for rel in record.get("files_written") or []:
        path = cell / f"run-{record['run']}.files" / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if rel.endswith(".html"):
            text = edge.html_to_text(text)
        if len(text) > FILE_CHARS:
            text = text[:FILE_CHARS] + "\n[... cut ...]"
        parts.append(f"[File the assistant wrote: {rel}]\n{text}")
    return "\n\n".join(parts)


def items_to_grade(out, case):
    """(arm, run, reply) for every reply of this case that can be graded."""
    items = []
    case_dir = Path(out) / "sessions" / case["id"]
    if case_dir.exists():
        for arm_dir in sorted(p for p in case_dir.iterdir() if p.is_dir()):
            for path in sorted(arm_dir.glob("run-*.json")):
                record = edge.read_json(path)
                if record.get("status") == "ok" and record.get("reply"):
                    reply = record["reply"] if arm_dir.name == "stub" else reply_with_files(record, arm_dir)
                    items.append((arm_dir.name, record["run"], reply))
    if case["tier"] == "replay":
        items.append(("original", 1, case["original_reply"]))
    return items


def verdict_path(out, model, case_id, arm, run):
    return Path(out) / "verdicts" / model / case_id / arm / f"run-{run}.json"


def grade_one(args, case, arm, run, reply, stop):
    if stop.is_set():
        return "skipped"
    with tempfile.TemporaryDirectory(prefix="barrier-judge-") as folder:
        proc = edge.run_claude(core.build_judge_command(args.model, args.effort),
                               core.build_judge_prompt(case, reply), folder, args.timeout, core.SESSION_ENV)
    record = {"case": case["id"], "arm": arm, "run": run, "judge_model": args.model, "verdict": None,
              "win": None, "ungraded": None}
    try:
        payload = json.loads(proc["stdout"])
    except ValueError:
        payload = None
    if proc["timed_out"] or not isinstance(payload, dict):
        record["ungraded"] = "timed out" if proc["timed_out"] else f"no JSON output: {proc['stderr'][-300:]}"
    elif payload.get("is_error"):
        text = payload.get("result") or ""
        if payload.get("api_error_status") == 429 or core.RATE_LIMIT_RE.search(text):
            stop.set()
            return "rate_limited"
        record["ungraded"] = text[:300]
    else:
        try:
            verdict = core.parse_verdict(payload)
            record.update(verdict=verdict, win=core.is_win(verdict), cost_usd=payload.get("total_cost_usd"),
                          models=sorted((payload.get("modelUsage") or {}).keys()))
        except (ValueError, json.JSONDecodeError) as exc:
            record["ungraded"] = f"bad verdict: {exc}"
    edge.write_json(verdict_path(args.out, args.model, case["id"], arm, run), record)
    return "graded" if record["verdict"] else "ungraded"


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        cases = edge.load_cases(args.cases, [c for c in args.only_case.split(",") if c])
        jobs = []
        for case in cases:
            for arm, run, reply in items_to_grade(args.out, case):
                path = verdict_path(args.out, args.model, case["id"], arm, run)
                if path.exists() and edge.read_json(path).get("verdict"):
                    continue
                jobs.append((case, arm, run, reply))
    except edge.EdgeError as exc:
        print(f"judge.py: {exc}", file=sys.stderr)
        return 2
    print(f"{len(jobs)} reply(ies) to grade with {args.model}, effort {args.effort}")
    stop = threading.Event()
    counts = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = {pool.submit(grade_one, args, case, arm, run, reply, stop): (case["id"], arm, run)
                   for case, arm, run, reply in jobs}
        for future in concurrent.futures.as_completed(futures):
            status = future.result()
            counts[status] = counts.get(status, 0) + 1
            case_id, arm, run = futures[future]
            print(f"  {status:<12} {case_id:<40} {arm:<22} run {run}", flush=True)
    print("done: " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())))
    return 3 if stop.is_set() else 0


if __name__ == "__main__":
    sys.exit(main())
