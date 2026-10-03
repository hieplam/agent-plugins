#!/usr/bin/env python3
"""Find the moments in the owner's history where a reply did not land.

    mine.py --out candidates.json [--cases DIR] [--history FILE] [--paste-cache DIR] [--projects DIR]

Two sources:
- ~/.claude/history.jsonl: every prompt the owner typed, in every session, but none of the
  replies. A candidate here becomes a *reask* case.
- ~/.claude/projects/*/*.jsonl: full transcripts of interactive sessions (Claude Code deletes them
  after `cleanupPeriodDays`, 30 by default). A candidate here can become a *replay* case.

A candidate is a message carrying a barrier signal (core.SIGNALS: "là gì", "I don't understand",
"explain more", "tiếng việt", "done?" ...). Signals over-match on purpose: a person reads every
candidate and decides. With --cases, candidates that already have a case are marked, so a later
run shows only what is new.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import core
import edge


def parse_args(argv):
    home = Path.home() / ".claude"
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", required=True)
    p.add_argument("--cases", default=None, help="existing cases, to mark candidates already used")
    p.add_argument("--history", default=str(home / "history.jsonl"))
    p.add_argument("--paste-cache", default=str(home / "paste-cache"))
    p.add_argument("--projects", default=str(home / "projects"))
    return p.parse_args(argv)


def read_jsonl(path):
    entries = []
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    continue
    except OSError as exc:
        raise edge.EdgeError(f"{path}: {exc}") from None
    return entries


def load_paste_cache(folder):
    cache = {}
    for path in Path(folder).glob("*.txt") if Path(folder).exists() else []:
        try:
            cache[path.stem] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return cache


def interactive(entries):
    """True for a session the owner typed into (`cli`), not a headless run (`sdk-cli`)."""
    return any(e.get("entrypoint") == "cli" for e in entries[:50])


def transcript_candidates(projects):
    found = []
    for path in sorted(Path(projects).glob("*/*.jsonl")):
        entries = read_jsonl(path)
        if not interactive(entries):
            continue
        items = core.transcript_items(entries)
        users = [i for i, item in enumerate(items) if item["role"] == "user"]
        for position, index in enumerate(users[1:], start=1):
            names = core.signals(items[index]["text"])
            if names:
                previous = items[users[position - 1]]["text"]
                found.append({"transcript": str(path), "session": path.stem, "follow_up_index": index,
                              "message": previous[:400], "follow_up": items[index]["text"][:600], "signals": names})
    return found


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        history = core.history_candidates(read_jsonl(args.history), load_paste_cache(args.paste_cache))
        transcripts = transcript_candidates(args.projects)
        used = {}
        if args.cases:
            for case in edge.load_cases(args.cases):
                used[(case["source"]["session"], core._norm(case["follow_up"]))] = case["id"]
    except edge.EdgeError as exc:
        print(f"mine.py: {exc}", file=sys.stderr)
        return 2
    for candidate in history + transcripts:
        candidate["case"] = used.get((candidate["session"], core._norm(candidate["follow_up"])))
    for candidate in history:
        stamp = candidate.get("timestamp")
        candidate["date"] = (datetime.datetime.fromtimestamp(stamp / 1000).strftime("%Y-%m-%d") if stamp else None)
    edge.write_json(args.out, {"generated": datetime.datetime.now().isoformat(timespec="seconds"),
                               "history": history, "transcripts": transcripts})
    new = sum(1 for c in history + transcripts if not c["case"])
    print(f"{len(history)} history candidate(s), {len(transcripts)} transcript candidate(s); "
          f"{new} without a case yet -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
