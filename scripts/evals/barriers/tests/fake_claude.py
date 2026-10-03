#!/usr/bin/env python3
"""A stand-in for `claude -p` that costs nothing: it answers the way the real CLI's stream-json does.

The output style it reports is the one the session folder's .claude/settings.json selects, as the
real CLI does under --setting-sources project. FAKE_CLAUDE_MODE picks the behaviour:
  ok (default) — a normal reply; limit — a usage-limit error; judge — a structured verdict.
"""
import json
import os
import sys
from pathlib import Path

argv = sys.argv[1:]
if argv == ["--version"]:
    print("0.0.0 (fake Claude Code)")
    sys.exit(0)
prompt = sys.stdin.read()
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
model = argv[argv.index("--model") + 1] if "--model" in argv else "?"
calls = Path(os.environ["FAKE_CLAUDE_LOG"])
with calls.open("a") as log:
    log.write(json.dumps({"argv": argv, "cwd": os.getcwd(), "prompt": prompt[:200]}) + "\n")

if "--json-schema" in argv:
    verdict_mode = os.environ.get("FAKE_JUDGE", "needed-for-bad")
    bad = "KNOWN-BAD" in prompt
    needed = bad if verdict_mode == "needed-for-bad" else True
    print(json.dumps({"type": "result", "is_error": False, "result": "", "total_cost_usd": 0.01,
                      "modelUsage": {model: {}}, "structured_output": {
                          "follow_up_needed": needed, "evidence": "fake", "answers_message": True, "wrong_claims": []}}))
    sys.exit(0)
if "--safe-mode" in argv:
    print(json.dumps({"type": "result", "is_error": False, "result": "KNOWN-BAD stub reply", "total_cost_usd": 0.01,
                      "modelUsage": {model: {}}}))
    sys.exit(0)

settings = Path(".claude/settings.json")
style = json.loads(settings.read_text()).get("outputStyle", "default") if settings.exists() else "default"
events = [{"type": "system", "subtype": "init", "model": model, "output_style": style, "tools": ["Read"]}]
if mode == "limit":
    events.append({"type": "result", "is_error": True, "subtype": "success", "result": "You've hit your session limit"})
else:
    Path("page.html").write_text("<html><body><p>A page the session wrote.</p></body></html>")
    events.append({"type": "assistant", "message": {"content": [{"type": "text", "text": f"Reply under {style}."}]}})
    events.append({"type": "result", "is_error": False, "subtype": "success", "result": f"Reply under {style}.",
                   "total_cost_usd": 0.2, "duration_ms": 1000, "num_turns": 1, "modelUsage": {model: {}}})
for event in events:
    print(json.dumps(event))
