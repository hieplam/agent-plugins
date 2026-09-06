#!/usr/bin/env python3
"""Measure agent/skill prompt size — the objective metric for a prompt-trim run.

Why this exists
---------------
`run_evals.py` answers "did behavior hold?". It does not answer "did the prompt
actually get smaller?", and a trim run needs both numbers or it is steering
blind: a rewrite that preserves every eval verdict while shrinking nothing has
cost real tokens and bought nothing.

What it measures
----------------
Bytes and lines are exact. Tokens are an ESTIMATE and labelled as one — there is
no local tokenizer here and this script deliberately makes no network call, so it
reports chars/`CHARS_PER_TOKEN` and says so rather than implying a precision it
does not have. Rank ordering and percentage deltas — which is all a trim run
steers on — are unaffected by the constant.

Every agent's system prompt is re-sent on each dispatch and each retry, so these
bytes are recurring cost, not a one-time load.

Usage
-----
    scripts/evals/prompt_size.py                          # every agent in this repo
    scripts/evals/prompt_size.py --dir path/to/agents     # a candidate copy
    scripts/evals/prompt_size.py --baseline B --candidate C   # the delta
    scripts/evals/prompt_size.py --json                   # machine-readable
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AGENTS_DIR = REPO_ROOT / "plugins" / "research-to-blog" / "agents"

# English prose + markdown sits near this ratio for Claude's tokenizer. Used only
# to put the byte counts on a familiar scale; every threshold in this repo is
# expressed against chars, which are exact.
CHARS_PER_TOKEN = 3.7


def measure_dir(d: Path) -> dict[str, dict]:
    """Per-file size for every .md directly under `d`."""
    out: dict[str, dict] = {}
    for path in sorted(d.glob("*.md")):
        raw = path.read_bytes()
        out[path.stem] = {
            "chars": len(raw),
            "lines": raw.count(b"\n") + 1,
            "est_tokens": round(len(raw) / CHARS_PER_TOKEN),
        }
    return out


def fmt_table(sizes: dict[str, dict]) -> str:
    total = sum(v["chars"] for v in sizes.values()) or 1
    rows = [f"{'agent':<12} {'lines':>7} {'chars':>9} {'~tokens':>9} {'share':>7}"]
    rows.append("-" * 48)
    for name, v in sorted(sizes.items(), key=lambda kv: -kv[1]["chars"]):
        share = 100.0 * v["chars"] / total
        rows.append(f"{name:<12} {v['lines']:>7} {v['chars']:>9,} "
                    f"{v['est_tokens']:>9,} {share:>6.1f}%")
    rows.append("-" * 48)
    rows.append(f"{'TOTAL':<12} {sum(v['lines'] for v in sizes.values()):>7} "
                f"{total:>9,} {round(total / CHARS_PER_TOKEN):>9,} {'100.0%':>7}")
    return "\n".join(rows)


def fmt_delta(base: dict[str, dict], cand: dict[str, dict]) -> tuple[str, float]:
    rows = [f"{'agent':<12} {'base ch':>9} {'cand ch':>9} {'delta':>10} {'pct':>8}"]
    rows.append("-" * 52)
    for name in sorted(set(base) | set(cand)):
        b = base.get(name, {}).get("chars", 0)
        c = cand.get(name, {}).get("chars", 0)
        d = c - b
        pct = (100.0 * d / b) if b else float("nan")
        rows.append(f"{name:<12} {b:>9,} {c:>9,} {d:>+10,} {pct:>+7.1f}%")
    bt = sum(v["chars"] for v in base.values())
    ct = sum(v["chars"] for v in cand.values())
    total_pct = (100.0 * (ct - bt) / bt) if bt else 0.0
    rows.append("-" * 52)
    rows.append(f"{'TOTAL':<12} {bt:>9,} {ct:>9,} {ct - bt:>+10,} {total_pct:>+7.1f}%")
    return "\n".join(rows), total_pct


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=None, help="Directory of .md prompts (default: research-to-blog agents)")
    ap.add_argument("--baseline", default=None, help="Baseline prompt dir, for a delta report")
    ap.add_argument("--candidate", default=None, help="Candidate prompt dir, for a delta report")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    args = ap.parse_args()

    if bool(args.baseline) != bool(args.candidate):
        print("ERROR: --baseline and --candidate must be given together.", file=sys.stderr)
        return 2

    if args.baseline:
        b_dir, c_dir = Path(args.baseline), Path(args.candidate)
        for d in (b_dir, c_dir):
            if not d.is_dir():
                print(f"ERROR: not a directory: {d}", file=sys.stderr)
                return 2
        base, cand = measure_dir(b_dir), measure_dir(c_dir)
        table, total_pct = fmt_delta(base, cand)
        if args.json:
            print(json.dumps({"baseline": base, "candidate": cand,
                              "total_pct_change": round(total_pct, 2)}, indent=2))
        else:
            print(table)
            print(f"\nPrompt size change: {total_pct:+.1f}%")
        return 0

    d = Path(args.dir) if args.dir else DEFAULT_AGENTS_DIR
    if not d.is_dir():
        print(f"ERROR: not a directory: {d}", file=sys.stderr)
        return 2
    sizes = measure_dir(d)
    if not sizes:
        print(f"ERROR: no .md prompts found in {d}", file=sys.stderr)
        return 2
    print(json.dumps(sizes, indent=2) if args.json else fmt_table(sizes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
