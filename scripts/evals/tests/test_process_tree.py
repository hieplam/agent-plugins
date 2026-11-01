"""A case must leave no process behind, whatever way the session ends.

Each test starts a stand-in for a model session. The stand-in starts a child that
sleeps for 5 minutes, and then ends (or hangs, or fails). The test checks that the
child is gone after run_claude / run_checks returns. Real Windows or POSIX
processes are used, with no mock.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

EVALS_DIR = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("run_evals", EVALS_DIR / "run_evals.py")
run_evals = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("run_evals", run_evals)
_spec.loader.exec_module(run_evals)

CHILD = "import os,time,sys; open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(300)"

# The session: start the sleeping child, wait until it wrote its pid, then do `ending`.
SESSION = (
    "import subprocess,sys,time,os\n"
    "pidfile=sys.argv[1]\n"
    "subprocess.Popen([sys.executable,'-c',%r,pidfile])\n"
    "t=time.time()\n"
    "while not os.path.exists(pidfile) or not open(pidfile).read():\n"
    "    time.sleep(0.05)\n"
    "    if time.time()-t>20: sys.exit(9)\n"
    "ending=sys.argv[2]\n"
    "if ending=='hang': time.sleep(300)\n"
    "if ending=='fail': sys.exit(3)\n"
) % CHILD


def alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True).stdout
        return f" {pid} " in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_dead(pid: int, seconds: float = 10) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if not alive(pid):
            return True
        time.sleep(0.1)
    return not alive(pid)


class SessionTreeEndsWithTheCase(unittest.TestCase):
    def _run(self, ending: str, timeout: int):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pidfile = Path(tmp.name) / "child.pid"
        cmd = [sys.executable, "-c", SESSION, str(pidfile), ending]
        with mock.patch.object(run_evals, "build_claude_command", return_value=cmd):
            result = run_evals.run_claude("p", cwd=Path(tmp.name), timeout=timeout)
        self.assertTrue(pidfile.exists(), "the session never started its child")
        pid = int(pidfile.read_text())
        self.addCleanup(lambda: alive(pid) and subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)] if os.name == "nt" else ["kill", "-9", str(pid)],
            capture_output=True))
        return result, pid

    def test_child_is_gone_after_the_session_exits_normally(self):
        result, pid = self._run("exit", timeout=20)
        self.assertTrue(result["ok"] or result["error"], result)
        self.assertTrue(wait_dead(pid), f"child {pid} survived a normal session exit")

    def test_child_is_gone_after_the_session_fails(self):
        _, pid = self._run("fail", timeout=20)
        self.assertTrue(wait_dead(pid), f"child {pid} survived a failed session")

    def test_child_is_gone_after_a_timeout(self):
        result, pid = self._run("hang", timeout=4)
        self.assertIn("timeout", result["error"])
        self.assertTrue(wait_dead(pid), f"child {pid} survived a timeout")

    def test_run_checks_also_ends_its_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = Path(tmp) / "child.pid"
            planned = [{"name": "c", "argv": [sys.executable, "-c", SESSION, str(pidfile), "exit"]}]
            run_evals.run_checks(planned, Path(tmp), timeout=20)
            pid = int(pidfile.read_text())
            self.assertTrue(wait_dead(pid), f"child {pid} survived a check")

    def test_stdout_and_exit_code_still_reach_the_caller(self):
        done = run_evals.run_in_tree(
            [sys.executable, "-c", "import sys;print('hi');sys.stderr.write('e');sys.exit(2)"],
            cwd=None, env=None, timeout=30)
        self.assertEqual((done.returncode, done.stdout.strip(), done.stderr), (2, "hi", "e"))


if __name__ == "__main__":
    unittest.main()
