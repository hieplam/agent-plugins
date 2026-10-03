"""Impure edges of the barrier eval: files, git, and `claude -p`.

Every function here fails closed: bad input becomes an EdgeError with a readable message, never a
traceback halfway through a run. Every subprocess carries a timeout, and every git call ignores the
host's global git config.
"""
from __future__ import annotations

import hashlib
import html as html_lib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import core


class EdgeError(Exception):
    """A refusal with a message. Nothing was half-done when it is raised."""


GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
RESERVED_ARMS = ("original", "stub")


def git(repo, *args, timeout=120, binary=False):
    env = dict(os.environ, **GIT_ENV)
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=env, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EdgeError(f"git {' '.join(args)} in {repo}: {exc}") from None
    if proc.returncode != 0:
        raise EdgeError(f"git {' '.join(args)} in {repo}: {proc.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EdgeError(f"{path}: {exc}") from None


def write_json(path, data):
    """Write JSON through a temp file and a rename, so a crash never leaves half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_cases(cases_dir, only=None):
    """Every case in `cases_dir`, validated. One bad file stops the whole load, with every problem listed."""
    cases, problems = [], []
    for path in sorted(Path(cases_dir).glob("*.json")):
        try:
            case = read_json(path)
        except EdgeError as exc:
            problems.append(str(exc))
            continue
        errors = core.validate_case(case)
        if isinstance(case, dict) and path.stem != case.get("id"):
            errors.append(f"the file must be named after the case id ({case.get('id')}.json)")
        if errors:
            problems.append(f"{path.name}: " + "; ".join(errors))
            continue
        cases.append(case)
    if problems:
        raise EdgeError("invalid case file(s):\n  " + "\n  ".join(problems))
    if not cases:
        raise EdgeError(f"no case files in {cases_dir}")
    if only:
        unknown = sorted(set(only) - {c["id"] for c in cases})
        if unknown:
            raise EdgeError(f"unknown case id(s): {', '.join(unknown)}")
        cases = [c for c in cases if c["id"] in only]
    return cases


def load_arms(path, only=None):
    arms = read_json(path)
    if not isinstance(arms, list) or not arms:
        raise EdgeError(f"{path} must be a non-empty JSON list of arms")
    names = []
    for arm in arms:
        if not isinstance(arm, dict) or not isinstance(arm.get("name"), str) or not core.ID_RE.match(arm["name"]):
            raise EdgeError(f"{path}: every arm needs a slug name")
        if arm["name"] in RESERVED_ARMS:
            raise EdgeError(f"{path}: '{arm['name']}' is reserved for calibration replies")
        style = arm.get("style")
        if style is not None and not (isinstance(style, dict) and style.get("ref") and style.get("path")):
            raise EdgeError(f"{path}: arm {arm['name']}: style must be null or {{ref, path}}")
        names.append(arm["name"])
    if len(names) != len(set(names)):
        raise EdgeError(f"{path}: arm names must be unique")
    if only:
        unknown = sorted(set(only) - set(names))
        if unknown:
            raise EdgeError(f"unknown arm(s): {', '.join(unknown)}")
        arms = [a for a in arms if a["name"] in only]
    return arms


def frontmatter_name(text, fallback):
    """The style's `name:` from its frontmatter: the name Claude Code selects it by."""
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        for line in text[4:end if end > 0 else 0].splitlines():
            if line.startswith("name:"):
                return line.split(":", 1)[1].strip().strip("'\"") or fallback
    return fallback


def resolve_style(arm, style_repo):
    """The arm's style text at its git ref, with the name it is selected by and a content hash.
    None for the arm that runs with no output style."""
    style = arm.get("style")
    if not style:
        return None
    text = git(style_repo, "show", f"{style['ref']}:{style['path']}")
    stem = Path(style["path"]).stem
    return {"text": text, "name": frontmatter_name(text, stem), "file": Path(style["path"]).name,
            "ref": style["ref"], "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def _safe_member(member, dest):
    target = (dest / member.name).resolve()
    if target != dest and dest not in target.parents:
        return False
    if member.issym() or member.islnk():
        link = (target.parent / member.linkname).resolve()
        return link == dest or dest in link.parents
    return member.isfile() or member.isdir()


def snapshot_repo(repos_root, repo, commit, dest):
    """Unpack `repo` at `commit` into `dest` with `git archive`: the files only, no .git, so the
    session can read the code as it was and can run nothing against the real clone."""
    root = Path(repos_root).expanduser().resolve()
    source = (root / repo).resolve()
    if source != root and root not in source.parents:
        raise EdgeError(f"repo {repo!r} is outside the repos root {root}")
    if not (source / ".git").exists():
        raise EdgeError(f"{source} is not a git clone; clone it before running this case")
    data = git(source, "archive", "--format=tar", commit, binary=True, timeout=300)
    dest = Path(dest).resolve()
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        members = [m for m in archive.getmembers() if _safe_member(m, dest)]
        archive.extractall(dest, members=members)


def prepare_scratch(scratch, case, style, memory_text, repos_root):
    """Lay out one session's folder: the repo snapshot (if any), the case's files, the owner's
    memory in .claude/CLAUDE.md, and the arm's style selected in .claude/settings.json.

    The snapshot's own .claude/ folder and .mcp.json are removed first: a repo's hooks, settings or
    output style must not steer the session. Its root CLAUDE.md stays, as it would in a real session.
    """
    scratch = Path(scratch)
    workspace = case.get("workspace")
    if workspace:
        snapshot_repo(repos_root, workspace["repo"], workspace["commit"], scratch)
    shutil.rmtree(scratch / ".claude", ignore_errors=True)
    mcp = scratch / ".mcp.json"
    if mcp.exists():
        mcp.unlink()
    for entry in case.get("files") or []:
        if not core.safe_relative_path(entry["path"]):
            raise EdgeError(f"case {case['id']}: unsafe file path {entry['path']!r}")
        target = scratch / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(entry["content"], encoding="utf-8")
    claude_dir = scratch / ".claude"
    claude_dir.mkdir()
    (claude_dir / "CLAUDE.md").write_text(memory_text, encoding="utf-8")
    settings = {}
    if style:
        (claude_dir / "output-styles").mkdir()
        (claude_dir / "output-styles" / style["file"]).write_text(style["text"], encoding="utf-8")
        settings["outputStyle"] = style["name"]
    (claude_dir / "settings.json").write_text(json.dumps(settings) + "\n", encoding="utf-8")


def file_states(folder):
    """{relative path: (size, mtime_ns)} for every file outside .claude/ — what changed is what the
    session wrote."""
    folder = Path(folder)
    states = {}
    for path in folder.rglob("*"):
        if path.is_file() and ".claude" not in path.relative_to(folder).parts:
            stat = path.stat()
            states[str(path.relative_to(folder))] = (stat.st_size, stat.st_mtime_ns)
    return states


KEEP_SUFFIXES = (".html", ".md", ".txt", ".json", ".mmd", ".svg", ".jsonl")


def copy_written_files(folder, before, dest, max_bytes=1_000_000):
    """Copy the text files the session created or changed into `dest`; return their relative paths."""
    folder = Path(folder)
    kept = []
    for rel, state in sorted(file_states(folder).items()):
        if before.get(rel) == state or not rel.endswith(KEEP_SUFFIXES) or state[0] > max_bytes:
            continue
        target = Path(dest) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(folder / rel, target)
        kept.append(rel)
    return kept


def run_claude(argv, prompt, cwd, timeout, env_extra=None):
    """Run one `claude -p` with the prompt on stdin. A timeout is reported, not raised."""
    env = dict(os.environ)
    env.update(env_extra or {})
    try:
        proc = subprocess.run(argv, input=prompt, capture_output=True, text=True, cwd=str(cwd), env=env,
                              timeout=timeout)
        return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "timed_out": False}
    except subprocess.TimeoutExpired as exc:
        def text(value):
            return value.decode("utf-8", "replace") if isinstance(value, bytes) else (value or "")
        return {"returncode": None, "stdout": text(exc.stdout), "stderr": text(exc.stderr), "timed_out": True}
    except OSError as exc:
        raise EdgeError(f"cannot start claude: {exc}") from None


def claude_version():
    try:
        proc = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=60,
                              stdin=subprocess.DEVNULL)
        return proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def cell_dir(out, case_id, arm):
    return Path(out) / "sessions" / case_id / arm


def html_to_text(html):
    html = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"[ \t]+", " ", html_lib.unescape(text)).strip()
