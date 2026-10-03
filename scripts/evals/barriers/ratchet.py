#!/usr/bin/env python3
"""The ratchet: a style change may not make the owner ask again more often.

    ratchet.py --baseline BASELINE_REPORT.json --candidate NEW_REPORT.json --arm todd-way-with-c
               [--candidate-arm NAME]

Compares one arm's cell wins, case by case, between the committed baseline report and a new run's
report (both from report.py, same judge). Cases that only one of the two runs scored are listed,
never counted, so adding cases cannot fake a change.

Exit codes: 0 HOLD or IMPROVED; 1 REGRESSION (significantly more cases lost than gained,
one-sided exact binomial p < 0.05); 2 bad input. On IMPROVED, commit the candidate report as the
new baseline: the number only ever moves up.
"""
from __future__ import annotations

import argparse
import sys

import core
import edge


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--baseline", required=True)
    p.add_argument("--candidate", required=True)
    p.add_argument("--arm", required=True, help="the arm to compare in the baseline")
    p.add_argument("--candidate-arm", default=None, help="the arm in the candidate (default: same name)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        baseline, candidate = edge.read_json(args.baseline), edge.read_json(args.candidate)
        before = baseline["cells"][args.arm]
        after = candidate["cells"][args.candidate_arm or args.arm]
    except (edge.EdgeError, KeyError, TypeError) as exc:
        print(f"ratchet.py: cannot read the two reports' cells: {exc}", file=sys.stderr)
        return 2
    if baseline.get("judge_model") != candidate.get("judge_model"):
        print(f"ratchet.py: the reports use different judges ({baseline.get('judge_model')} vs "
              f"{candidate.get('judge_model')}); a change of judge is not a change of style", file=sys.stderr)
        return 2
    result = core.ratchet(before, after)
    print(f"{result['status']}: {result['before']} -> {result['after']} wins on {result['cases']} shared case(s)")
    if result["lost"]:
        print(f"  lost ({len(result['lost'])}): {', '.join(result['lost'])}  p_worse={result['p_worse']}")
    if result["gained"]:
        print(f"  gained ({len(result['gained'])}): {', '.join(result['gained'])}  p_better={result['p_better']}")
    if result["only_in_candidate"]:
        print(f"  new, not scored against the baseline: {', '.join(result['only_in_candidate'])}")
    if result["only_in_baseline"]:
        print(f"  missing from the candidate: {', '.join(result['only_in_baseline'])}")
    if result["status"] == "IMPROVED":
        print("  commit the candidate report as the new baseline.")
    return 1 if result["status"] == "REGRESSION" else 0


if __name__ == "__main__":
    sys.exit(main())
