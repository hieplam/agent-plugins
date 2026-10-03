#!/usr/bin/env python3
"""Build case files from a curated spec: the person decides, this script copies the facts exactly.

    build_cases.py --spec SPEC.json --out CASES_DIR [--scrub-literal TEXT ...]

The spec is a JSON list. Each entry names one barrier moment and adds what only a person can
judge: the category, the flaw, the language, whether it is in sample. Everything else is copied
from the record, never retyped:

  replay: {"tier": "replay", "session": "<uuid>", "follow_up_match": "all done?", "occurrence": 1, ...}
          — the message, the conversation before it, the work done for it and the original reply
          come from the transcript.
  reask:  {"tier": "reask", "session": "<uuid>", "follow_up_match": "qmd là gì", ...}
          — the message is the owner's prompt before the follow-up in ~/.claude/history.jsonl.

Optional per entry: "workspace": {"repo": "ai-dict", "commit": "<sha>"} or {"repo": ..., "branch":
"origin/master"} (the last commit on that branch before the message was sent); "files": [{"path":
..., one of "content", "from_file", "from_git": {"repo", "path", "branch" | "commit"},
"from_gh_pr": "owner/repo#N", "from_gh_issue": "owner/repo#N"}]; "note"; "terms"; "must_cover".

Every text field is scrubbed of secret shapes and of each --scrub-literal before it is written.
"""
from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

import core
import edge
import mine

PR_DIFF_CHARS = 60000


def parse_args(argv):
    home = Path.home() / ".claude"
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--spec", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--history", default=str(home / "history.jsonl"))
    p.add_argument("--paste-cache", default=str(home / "paste-cache"))
    p.add_argument("--projects", default=str(home / "projects"))
    p.add_argument("--repos-root", default=str(Path.home() / "repos"))
    p.add_argument("--scrub-literal", action="append", default=[], help="text to redact wherever it appears")
    return p.parse_args(argv)


def gh(*args, timeout=120):
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise edge.EdgeError(f"gh {' '.join(args)}: {exc}") from None
    if proc.returncode != 0:
        raise edge.EdgeError(f"gh {' '.join(args)}: {proc.stderr.strip()[:300]}")
    return proc.stdout


def pr_text(reference):
    """A PR's title, description and diff as one Markdown file: what the PR page showed."""
    repo, _, number = reference.partition("#")
    if not repo or not number.isdigit():
        raise edge.EdgeError(f"from_gh_pr must look like owner/repo#123, got {reference!r}")
    info = json.loads(gh("pr", "view", number, "--repo", repo, "--json", "title,body,state,baseRefName,headRefName"))
    diff = gh("pr", "diff", number, "--repo", repo, timeout=300)
    if len(diff) > PR_DIFF_CHARS:
        diff = diff[:PR_DIFF_CHARS] + f"\n[... diff cut at {PR_DIFF_CHARS} characters ...]\n"
    return (f"# PR #{number} — {info['title']}\n\nRepository: {repo} · {info['headRefName']} → "
            f"{info['baseRefName']} · {info['state']}\n\n{info['body'] or '(no description)'}\n\n"
            f"## Diff\n\n```diff\n{diff}```\n")


def issue_text(reference):
    """An issue's title, description and comments as one Markdown file: what the issue page showed."""
    repo, _, number = reference.partition("#")
    if not repo or not number.isdigit():
        raise edge.EdgeError(f"from_gh_issue must look like owner/repo#123, got {reference!r}")
    info = json.loads(gh("issue", "view", number, "--repo", repo, "--json", "title,body,state,comments"))
    comments = "\n\n".join(f"### Comment by {c.get('author', {}).get('login', '?')}\n\n{c.get('body', '')}"
                            for c in info.get("comments") or [])
    return (f"# Issue #{number} — {info['title']}\n\nRepository: {repo} · {info['state']}\n\n"
            f"{info['body'] or '(no description)'}\n\n{comments}\n")


def commit_before(repos_root, repo, branch, sent_at):
    """The last commit on `branch` before the owner sent the message: the repo as they saw it."""
    path = Path(repos_root).expanduser() / repo
    commit = edge.git(path, "rev-list", "-1", f"--before={sent_at.isoformat()}", branch).strip()
    if not commit:
        raise edge.EdgeError(f"no commit on {branch} in {path} before {sent_at}")
    return commit


def build_files(entry, sent_at, repos_root):
    files = []
    for spec in entry.get("files") or []:
        if "content" in spec:
            content = spec["content"]
        elif "from_file" in spec:
            try:
                content = Path(spec["from_file"]).expanduser().read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise edge.EdgeError(f"{entry['id']}: {exc}") from None
        elif "from_gh_pr" in spec:
            content = pr_text(spec["from_gh_pr"])
        elif "from_gh_issue" in spec:
            content = issue_text(spec["from_gh_issue"])
        elif "from_git" in spec:
            source = spec["from_git"]
            if sent_at is None and not source.get("commit"):
                raise edge.EdgeError(f"{entry['id']}: from_git needs a commit on a replay case")
            commit = source.get("commit") or commit_before(repos_root, source["repo"], source.get("branch", "HEAD"), sent_at)
            content = edge.git(Path(repos_root).expanduser() / source["repo"], "show", f"{commit}:{source['path']}")
        else:
            raise edge.EdgeError(f"{entry['id']}: a file needs content, from_file, from_git, from_gh_pr or from_gh_issue")
        files.append({"path": spec["path"], "content": content})
    return files


def resolve_workspace(entry, sent_at, repos_root):
    spec = entry.get("workspace")
    if not spec:
        return None
    if spec.get("commit"):
        return {"repo": spec["repo"], "commit": spec["commit"]}
    if sent_at is None:
        raise edge.EdgeError(f"{entry['id']}: a replay case's workspace needs an explicit commit")
    return {"repo": spec["repo"], "commit": commit_before(repos_root, spec["repo"], spec.get("branch", "HEAD"), sent_at)}


def find_replay(entry, projects):
    paths = list(Path(projects).glob(f"*/{entry['session']}.jsonl"))
    if len(paths) != 1:
        raise edge.EdgeError(f"{entry['id']}: transcript {entry['session']} not found (or found twice)")
    entries = mine.read_jsonl(paths[0])
    items = core.transcript_items(entries)
    if "follow_up_index" in entry:
        index = entry["follow_up_index"]
    else:
        target = core._norm(entry["follow_up_match"])
        hits = [i for i, item in enumerate(items) if item["role"] == "user" and target in core._norm(item["text"])]
        occurrence = entry.get("occurrence", 1)
        if len(hits) < occurrence:
            raise edge.EdgeError(f"{entry['id']}: follow-up {entry['follow_up_match']!r} found {len(hits)} time(s)")
        index = hits[occurrence - 1]
    try:
        parts = core.replay_parts(items, index)
    except ValueError as exc:
        raise edge.EdgeError(f"{entry['id']}: {exc}") from None
    stamps = [e.get("timestamp") for e in entries if e.get("uuid") == items[index].get("uuid")]
    date = (stamps[0] or "")[:10] if stamps else ""
    project = next((e.get("cwd") for e in entries if e.get("cwd")), "") or ""
    return parts, date, Path(project).name, None


def find_reask(entry, history, paste_cache):
    target = core._norm(entry["follow_up_match"])
    prompts = sorted((e for e in history if e.get("sessionId") == entry["session"]), key=lambda e: e.get("timestamp") or 0)
    texts = []
    for e in prompts:
        display = e.get("display") or ""
        if display.startswith(("/", "!")):
            continue
        texts.append((e, core.expand_pasted(display, e.get("pastedContents"), paste_cache)))
    hits = [i for i, (_, text) in enumerate(texts) if i > 0 and target in core._norm(text)]
    occurrence = entry.get("occurrence", 1)
    if len(hits) < occurrence:
        raise edge.EdgeError(f"{entry['id']}: follow-up {entry['follow_up_match']!r} found {len(hits)} time(s) "
                             f"in session {entry['session']}")
    index = hits[occurrence - 1]
    message_entry, message = texts[index - 1]
    follow_up = texts[index][1]
    sent_at = datetime.datetime.fromtimestamp(message_entry["timestamp"] / 1000, tz=datetime.timezone.utc)
    parts = {"conversation": [], "message": message, "work": [], "original_reply": None, "follow_up": follow_up}
    return parts, sent_at.strftime("%Y-%m-%d"), Path(message_entry.get("project") or "").name, sent_at


def build_case(entry, history, paste_cache, args):
    if entry.get("tier") == "replay":
        parts, date, project, sent_at = find_replay(entry, args.projects)
        source_kind = "transcript"
    elif entry.get("tier") == "reask":
        parts, date, project, sent_at = find_reask(entry, history, paste_cache)
        source_kind = "history"
    else:
        raise edge.EdgeError(f"{entry.get('id')}: tier must be replay or reask")
    case = {
        "id": entry["id"], "tier": entry["tier"], "in_sample": entry.get("in_sample", False),
        "category": entry.get("category"), "language": entry.get("language"),
        "source": {"kind": source_kind, "session": entry["session"], "date": date, "project": project},
        "workspace": resolve_workspace(entry, sent_at, args.repos_root),
        "files": build_files(entry, sent_at, args.repos_root), "conversation": parts["conversation"], "message": parts["message"],
        "work": parts["work"], "note": entry.get("note"), "original_reply": parts["original_reply"],
        "follow_up": parts["follow_up"], "flaw": entry.get("flaw"), "terms": entry.get("terms", []),
        "must_cover": entry.get("must_cover", []), "curator_note": entry.get("curator_note", ""),
    }
    case = core.scrub_case(case, args.scrub_literal)
    errors = core.validate_case(case)
    if errors:
        raise edge.EdgeError(f"{entry['id']}: " + "; ".join(errors))
    return case


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        spec = edge.read_json(args.spec)
        if not isinstance(spec, list):
            raise edge.EdgeError(f"{args.spec} must be a JSON list")
        history = mine.read_jsonl(args.history)
        paste_cache = mine.load_paste_cache(args.paste_cache)
    except edge.EdgeError as exc:
        print(f"build_cases.py: {exc}", file=sys.stderr)
        return 2
    failures = 0
    ids = [e.get("id") for e in spec]
    if len(ids) != len(set(ids)):
        print("build_cases.py: case ids in the spec must be unique", file=sys.stderr)
        return 2
    for entry in spec:
        try:
            case = build_case(entry, history, paste_cache, args)
        except edge.EdgeError as exc:
            failures += 1
            print(f"  FAILED  {exc}", file=sys.stderr)
            continue
        edge.write_json(Path(args.out) / f"{case['id']}.json", case)
        print(f"  built   {case['id']} ({case['tier']}, {case['category']}, {case['language']})")
    print(f"{len(spec) - failures} built, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
