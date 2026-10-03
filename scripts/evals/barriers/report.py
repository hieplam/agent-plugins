#!/usr/bin/env python3
"""Turn one run folder into a report: gates first, then win rates, paired comparisons and cost.

    report.py --cases DIR --out RUN_DIR --judge JUDGE_MODEL [--arms arms.json] [--runs N]

Writes RUN_DIR/report-<judge>.json (what ratchet.py compares) and RUN_DIR/report-<judge>.md.
The pairs compared are each arm against the next one in arms.json order, plus every arm against
the first (the no-style floor).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import core
import edge

HERE = Path(__file__).resolve().parent


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--cases", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--judge", required=True, help="the judge model whose verdicts to report")
    p.add_argument("--arms", default=str(HERE / "arms.json"))
    p.add_argument("--runs", type=int, default=None, help="runs per cell (default: the most found)")
    return p.parse_args(argv)


def load_records(folder, pattern):
    return [edge.read_json(path) for path in sorted(Path(folder).glob(pattern))] if Path(folder).exists() else []


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        # Report the cases this run folder ran, not every case in the folder: a pilot runs a few.
        ran = edge.read_json(Path(args.out) / "run.json").get("cases", {})
        cases = [c for c in edge.load_cases(args.cases) if c["id"] in ran]
        arms = [arm["name"] for arm in edge.load_arms(args.arms)]
        sessions = [s for s in load_records(Path(args.out) / "sessions", "*/*/run-*.json") if s.get("arm") != "stub"]
        verdicts = load_records(Path(args.out) / "verdicts" / args.judge, "*/*/run-*.json")
    except edge.EdgeError as exc:
        print(f"report.py: {exc}", file=sys.stderr)
        return 2
    if not verdicts:
        print(f"report.py: no verdicts from {args.judge} in {args.out}; run judge.py first", file=sys.stderr)
        return 2
    runs = args.runs or max(s["run"] for s in sessions)
    pairs = []
    for a, b in zip(arms[1:], arms[:-1]):
        pairs.append((a, b))
    for arm in arms[2:]:
        pairs.append((arm, arms[0]))
    report = core.summarize_run(cases, sessions, verdicts, arms, pairs, runs)
    slug = args.judge.replace("/", "-")
    edge.write_json(Path(args.out) / f"report-{slug}.json", report)
    markdown = core.render_report_md(report)
    (Path(args.out) / f"report-{slug}.md").write_text(markdown + "\n", encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
