#!/usr/bin/env python3
"""Write one deliberately flawed reply (a *stub*) per case, to calibrate the judge.

    stubs.py --cases DIR --out RUN_DIR [--model claude-opus-5-5] [--effort medium] [--jobs 3]

The stub is a plausible reply with exactly the flaw the owner reacted to. The judge must say the
follow-up is still needed after the stub; a judge that passes stubs is lenient, and report.py's
calibration gate fails. A case whose stub passes is left out of every score. (A replay case also
has its real original reply, which report.py uses to check the case itself.)

Results land in RUN_DIR/sessions/<case>/stub/run-1.json. Resumable; exit 3 on a usage limit.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import tempfile
import threading

import core
import edge


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--cases", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--model", default="claude-opus-5-5")
    p.add_argument("--effort", default="medium")
    p.add_argument("--jobs", type=int, default=3)
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--only-case", default="")
    return p.parse_args(argv)


def write_stub(args, case, stop):
    if stop.is_set():
        return "skipped"
    with tempfile.TemporaryDirectory(prefix="barrier-stub-") as folder:
        proc = edge.run_claude(core.build_stub_command(args.model, args.effort), core.build_stub_prompt(case),
                               folder, args.timeout, core.SESSION_ENV)
    record = {"case": case["id"], "arm": "stub", "run": 1, "status": "error", "reply": "", "reason": ""}
    try:
        payload = json.loads(proc["stdout"])
    except ValueError:
        payload = None
    if proc["timed_out"] or not isinstance(payload, dict):
        record["reason"] = "timed out" if proc["timed_out"] else f"no JSON output: {proc['stderr'][-300:]}"
    elif payload.get("is_error"):
        text = payload.get("result") or ""
        record["status"] = "rate_limited" if (payload.get("api_error_status") == 429 or core.RATE_LIMIT_RE.search(text)) else "error"
        record["reason"] = text[:300]
    elif (payload.get("result") or "").strip():
        record.update(status="ok", reply=payload["result"].strip(), cost_usd=payload.get("total_cost_usd"),
                      models=sorted((payload.get("modelUsage") or {}).keys()))
    else:
        record["reason"] = "empty reply"
    edge.write_json(edge.cell_dir(args.out, case["id"], "stub") / "run-1.json", record)
    if record["status"] == "rate_limited":
        stop.set()
    return record["status"]


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        cases = edge.load_cases(args.cases, [x for x in args.only_case.split(",") if x])
    except edge.EdgeError as exc:
        print(f"stubs.py: {exc}", file=sys.stderr)
        return 2
    todo = []
    for case in cases:
        path = edge.cell_dir(args.out, case["id"], "stub") / "run-1.json"
        if not (path.exists() and edge.read_json(path).get("status") == "ok"):
            todo.append(case)
    print(f"{len(todo)} stub(s) to write")
    stop = threading.Event()
    counts = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = {pool.submit(write_stub, args, case, stop): case["id"] for case in todo}
        for future in concurrent.futures.as_completed(futures):
            status = future.result()
            counts[status] = counts.get(status, 0) + 1
            print(f"  {status:<14} {futures[future]}", flush=True)
    print("done: " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())))
    return 3 if stop.is_set() else 0


if __name__ == "__main__":
    sys.exit(main())
