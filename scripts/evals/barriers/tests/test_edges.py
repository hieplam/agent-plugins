"""Tests for the barrier eval's edges, run against real folders, a real git repo, and a fake
`claude` that answers the way the real CLI's stream-json does — so the whole run → stub → judge →
report → ratchet flow is exercised end to end at no cost.

    python3 -m unittest discover -s scripts/evals/barriers/tests -t .
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BARRIERS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BARRIERS_DIR))

import core  # noqa: E402
import edge  # noqa: E402

GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def make_repo(root):
    """A git repo whose snapshot carries the things a session must not inherit: hooks in
    .claude/settings.json, an .mcp.json, and an output style of its own."""
    repo = Path(root) / "app"
    (repo / ".claude" / "output-styles").mkdir(parents=True)
    (repo / "CLAUDE.md").write_text("# App memory\nuse bun\n")
    (repo / ".claude" / "settings.json").write_text(json.dumps({"outputStyle": "Evil", "hooks": {"SessionStart": []}}))
    (repo / ".claude" / "output-styles" / "evil.md").write_text("---\nname: Evil\n---\nbe evil")
    (repo / ".mcp.json").write_text("{}")
    (repo / "src.py").write_text("print('v1')\n")
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "v1"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, env=GIT_ENV, timeout=60)
    commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                            env=GIT_ENV, timeout=60).stdout.strip()
    (repo / "src.py").write_text("print('v2, after the message')\n")
    return repo, commit


def case_with_workspace(commit):
    return {"id": "ws-case", "tier": "reask", "in_sample": False, "category": "term", "language": "en",
            "source": {"kind": "history", "session": "s", "date": "2026-06-01", "project": "app"},
            "workspace": {"repo": "app", "commit": commit}, "files": [{"path": "notes/PR.md", "content": "pr"}],
            "message": "explain src.py", "follow_up": "what is v1?", "flaw": "it never said what v1 is"}


class ScratchFolder(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo, self.commit = make_repo(self.tmp)
        self.style = {"text": "---\nname: Todd way\n---\nbody", "name": "Todd way", "file": "todd-way.md"}

    def test_the_session_sees_the_repo_as_it_was_and_none_of_its_customization(self):
        scratch = self.tmp / "s1"
        scratch.mkdir()
        edge.prepare_scratch(scratch, case_with_workspace(self.commit), self.style, "OWNER MEMORY", self.tmp)
        self.assertEqual((scratch / "src.py").read_text(), "print('v1')\n")
        self.assertFalse((scratch / ".git").exists())
        self.assertFalse((scratch / ".mcp.json").exists())
        self.assertEqual((scratch / "CLAUDE.md").read_text(), "# App memory\nuse bun\n")
        self.assertEqual((scratch / ".claude" / "CLAUDE.md").read_text(), "OWNER MEMORY")
        settings = json.loads((scratch / ".claude" / "settings.json").read_text())
        self.assertEqual(settings["outputStyle"], "Todd way")
        self.assertTrue(settings["sandbox"]["enabled"])
        self.assertFalse(settings["sandbox"]["allowUnsandboxedCommands"])
        self.assertIn(f"Write(/{Path.home()}/**)", settings["permissions"]["deny"])
        self.assertNotIn("hooks", settings)
        self.assertEqual(sorted(p.name for p in (scratch / ".claude" / "output-styles").iterdir()), ["todd-way.md"])
        self.assertEqual((scratch / "notes" / "PR.md").read_text(), "pr")

    def test_no_style_selects_nothing(self):
        scratch = self.tmp / "s2"
        scratch.mkdir()
        edge.prepare_scratch(scratch, case_with_workspace(self.commit), None, "m", self.tmp)
        settings = json.loads((scratch / ".claude" / "settings.json").read_text())
        self.assertNotIn("outputStyle", settings)
        self.assertTrue(settings["sandbox"]["enabled"])
        self.assertFalse((scratch / ".claude" / "output-styles").exists())

    def test_a_relative_repos_root_typed_from_another_folder_works(self):
        """The shape a person types: cd somewhere, name the folder relatively."""
        scratch = self.tmp / "s3"
        scratch.mkdir()
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        previous = os.getcwd()
        os.chdir(elsewhere)
        try:
            edge.prepare_scratch(scratch, case_with_workspace(self.commit), None, "m", "..")
        finally:
            os.chdir(previous)
        self.assertEqual((scratch / "src.py").read_text(), "print('v1')\n")

    def test_a_repo_outside_the_root_is_refused(self):
        case = case_with_workspace(self.commit)
        case["workspace"]["repo"] = "../app"
        with self.assertRaises(edge.EdgeError):
            edge.prepare_scratch(self.tmp / "s4", case, None, "m", self.tmp / "nested")

    def test_only_files_the_session_wrote_are_kept(self):
        scratch = self.tmp / "s5"
        scratch.mkdir()
        edge.prepare_scratch(scratch, case_with_workspace(self.commit), None, "m", self.tmp)
        before = edge.file_states(scratch)
        (scratch / "answer.html").write_text("<p>x</p>")
        (scratch / ".claude" / "note.md").write_text("internal")
        kept = edge.copy_written_files(scratch, before, self.tmp / "kept")
        self.assertEqual(kept, ["answer.html"])


class Loading(unittest.TestCase):
    def test_arms_reject_reserved_and_duplicate_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            for arms in ([{"name": "stub", "style": None}], [{"name": "a-b", "style": None}] * 2):
                path = Path(tmp) / "arms.json"
                path.write_text(json.dumps(arms))
                with self.assertRaises(edge.EdgeError):
                    edge.load_arms(path)

    def test_the_shipped_arms_resolve_to_the_two_style_versions(self):
        arms = edge.load_arms(BARRIERS_DIR / "arms.json")
        self.assertEqual([a["name"] for a in arms], ["no-style", "todd-way-before-c", "todd-way-with-c"])
        before = edge.resolve_style(arms[1], BARRIERS_DIR.parents[2])
        after = edge.resolve_style(arms[2], BARRIERS_DIR.parents[2])
        self.assertEqual((before["name"], after["name"]), ("Todd way", "Todd way"))
        self.assertNotIn("## Part C", before["text"])
        self.assertIn("## Part C", after["text"])

    def test_a_case_file_must_be_named_after_its_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = case_with_workspace("abc1234")
            (Path(tmp) / "other-name.json").write_text(json.dumps(case))
            with self.assertRaises(edge.EdgeError) as caught:
                edge.load_cases(tmp)
            self.assertIn("named after the case id", str(caught.exception))

    def test_frontmatter_name(self):
        self.assertEqual(edge.frontmatter_name("---\nname: Todd way\nx: y\n---\nbody", "f"), "Todd way")
        self.assertEqual(edge.frontmatter_name("no frontmatter", "fallback"), "fallback")


class EndToEnd(unittest.TestCase):
    """run.py → stubs.py → judge.py → report.py → ratchet.py against a fake `claude` on PATH."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "claude").symlink_to(BARRIERS_DIR / "tests" / "fake_claude.py")
        self.env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                        FAKE_CLAUDE_LOG=str(self.tmp / "calls.jsonl"))
        cases = self.tmp / "cases"
        cases.mkdir()
        reask = {"id": "reask-one", "tier": "reask", "in_sample": False, "category": "term", "language": "en",
                 "source": {"kind": "history", "session": "s", "date": "2026-06-01", "project": "p"},
                 "message": "- what does this flag do?", "follow_up": "what is X?", "flaw": "it used X bare"}
        replay = {"id": "replay-one", "tier": "replay", "in_sample": True, "category": "status", "language": "en",
                  "source": {"kind": "transcript", "session": "t", "date": "2026-09-30", "project": "p"},
                  "conversation": [], "message": "build it", "work": [],
                  "original_reply": "KNOWN-BAD T9 passed", "follow_up": "done?", "flaw": "no status line"}
        for case in (reask, replay):
            (cases / f"{case['id']}.json").write_text(json.dumps(case))
        (self.tmp / "memory.md").write_text("owner memory")
        self.common = ["--cases", "cases", "--out", "run"]

    def script(self, name, *args, mode="ok"):
        env = dict(self.env, FAKE_CLAUDE_MODE=mode)
        return subprocess.run([sys.executable, str(BARRIERS_DIR / name), *args], capture_output=True, text=True,
                              cwd=self.tmp, env=env, timeout=300)

    def run_sessions(self, mode="ok"):
        return self.script("run.py", *self.common, "--memory", "memory.md", "--scratch-root", str(self.tmp),
                           "--style-repo", str(BARRIERS_DIR.parents[2]), mode=mode)

    def calls(self):
        path = self.tmp / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_the_whole_flow(self):
        first = self.run_sessions()
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        record = json.loads((self.tmp / "run/sessions/reask-one/todd-way-with-c/run-1.json").read_text())
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["reply"], "Reply under Todd way.")
        self.assertEqual(record["files_written"], ["page.html"])
        plain = json.loads((self.tmp / "run/sessions/reask-one/no-style/run-1.json").read_text())
        self.assertEqual(plain["reply"], "Reply under default.")
        sessions = [c for c in self.calls() if "--setting-sources" in c["argv"]]
        self.assertEqual(len(sessions), 6)
        self.assertTrue(all(c["prompt"].startswith(("- what does", "This is a working session")) for c in sessions))
        self.assertTrue(all("--tools" in c["argv"] for c in sessions))
        self.assertFalse(any(Path(c["cwd"]).exists() for c in sessions), "session folders are removed")

        again = self.run_sessions()
        self.assertIn("0 session(s) to run", again.stdout)

        self.assertEqual(self.script("stubs.py", *self.common).returncode, 0)
        judged = self.script("judge.py", *self.common, "--model", "judge-x")
        self.assertEqual(judged.returncode, 0, judged.stderr)
        self.assertIn("9 reply(ies) to grade", judged.stdout)
        reported = self.script("report.py", *self.common, "--judge", "judge-x")
        self.assertEqual(reported.returncode, 0, reported.stderr)
        report = json.loads((self.tmp / "run/report-judge-x.json").read_text())
        self.assertEqual(report["scored_cases"], ["reask-one", "replay-one"])
        self.assertTrue(all(g["pass"] for g in report["gates"]), report["gates"])
        self.assertEqual(report["summary"]["todd-way-with-c"]["all"]["wins"], 2)

        same = self.script("ratchet.py", "--baseline", "run/report-judge-x.json",
                           "--candidate", "run/report-judge-x.json", "--arm", "todd-way-with-c")
        self.assertEqual(same.returncode, 0)
        self.assertIn("HOLD: 2 -> 2", same.stdout)

    def test_a_usage_limit_stops_the_run_and_resumes_later(self):
        stopped = self.run_sessions(mode="limit")
        self.assertEqual(stopped.returncode, 3, stopped.stdout + stopped.stderr)
        self.assertIn("usage limit", stopped.stdout)
        resumed = self.run_sessions()
        self.assertEqual(resumed.returncode, 0)
        statuses = {json.loads(p.read_text())["status"] for p in (self.tmp / "run/sessions").glob("*/*/run-*.json")}
        self.assertEqual(statuses, {"ok"})

    def test_a_changed_case_cannot_join_an_old_run_folder(self):
        self.assertEqual(self.run_sessions().returncode, 0)
        path = self.tmp / "cases" / "reask-one.json"
        case = json.loads(path.read_text())
        case["message"] = "a different message"
        path.write_text(json.dumps(case))
        refused = self.run_sessions()
        self.assertEqual(refused.returncode, 2)
        self.assertIn("changed since this run started", refused.stderr)


if __name__ == "__main__":
    unittest.main()
