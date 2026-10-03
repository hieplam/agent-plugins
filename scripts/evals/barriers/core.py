"""Pure core of the barrier eval: would a reply have spared the owner a follow-up they really sent?

Vocabulary, in the owner's own eval terms:

- A **case** is one real moment from the owner's sessions where a reply did not land: the message
  the assistant answered, what it could see when it answered, and the owner's real next message —
  the **follow-up** that shows the reply failed (the *barrier*).
- An **arm** is one setup every case runs under: one output style, or none.
- A **cell** is one case under one arm. It runs `runs` times and counts as a win when more than
  half of its graded runs win.
- The **judge** is a separate model session that reads one reply, with no arm name, and answers
  whether the owner would still have needed that follow-up.

Everything in this module takes plain data and returns plain data. Reading files, running
`claude -p` and writing results happen only in the scripts beside it (run.py, judge.py, ...).
"""
from __future__ import annotations

import json
import math
import re
import unicodedata

# --- Case schema -------------------------------------------------------------------------------

TIERS = ("replay", "reask")
CATEGORIES = ("term", "depth", "language", "visual", "requirement", "status", "cost")
LANGUAGES = ("en", "vi")
ROLES = ("user", "assistant", "tool_call", "tool_result")
REQUIRED = ("id", "tier", "in_sample", "category", "language", "source", "message", "follow_up", "flaw")

# The only fields the session under test may see. The follow-up, the flaw, the original reply and
# the points a full answer covers are the answer key: they reach the judge and nothing else.
ARM_VISIBLE = ("workspace", "files", "conversation", "message", "work", "note")

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,80}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")

# A follow-up shorter than this ("done?") can legitimately appear earlier in a replayed
# conversation, so only longer follow-ups are checked for leaking into what the session sees.
LEAK_MIN_CHARS = 20


def _norm(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "")).strip().lower()


def arm_visible_text(case):
    """All text the session under test can see, joined: the leak check searches this."""
    parts = [case.get("message") or "", case.get("note") or ""]
    for item in (case.get("conversation") or []) + (case.get("work") or []):
        parts.append(item.get("text") or "")
    for entry in case.get("files") or []:
        parts.append(entry.get("content") or "")
    return "\n".join(parts)


def safe_relative_path(path):
    """True when `path` stays inside the folder it is written to: relative, no `..`, no empty part."""
    if not isinstance(path, str) or not path or path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", path):
        return False
    return all(part not in ("", ".", "..") for part in re.split(r"[\\/]+", path))


def validate_case(case):
    """Every reason `case` cannot be used. An empty list means the case is sound."""
    if not isinstance(case, dict):
        return ["case is not a JSON object"]
    missing = [key for key in REQUIRED if key not in case]
    if missing:
        return [f"missing field(s): {', '.join(missing)}"]
    errors = []
    if not isinstance(case["id"], str) or not ID_RE.match(case["id"]):
        errors.append("id must be a lowercase slug (a-z, 0-9, '-')")
    if case["tier"] not in TIERS:
        errors.append(f"tier must be one of {TIERS}")
    if not isinstance(case["in_sample"], bool):
        errors.append("in_sample must be true or false")
    if case["category"] not in CATEGORIES:
        errors.append(f"category must be one of {CATEGORIES}")
    if case["language"] not in LANGUAGES:
        errors.append(f"language must be one of {LANGUAGES}")
    for key in ("message", "follow_up", "flaw"):
        if not isinstance(case[key], str) or not case[key].strip():
            errors.append(f"{key} must be non-empty text")

    source = case["source"]
    if not isinstance(source, dict):
        errors.append("source must be an object")
    else:
        if source.get("kind") not in ("transcript", "history"):
            errors.append("source.kind must be 'transcript' or 'history'")
        if not isinstance(source.get("session"), str) or not source.get("session"):
            errors.append("source.session must name the session")
        if not isinstance(source.get("date"), str) or not DATE_RE.match(source.get("date") or ""):
            errors.append("source.date must be YYYY-MM-DD")

    if case.get("tier") == "replay":
        if not isinstance(case.get("original_reply"), str) or not case["original_reply"].strip():
            errors.append("a replay case needs original_reply: the reply the follow-up reacted to")
        if isinstance(source, dict) and source.get("kind") != "transcript":
            errors.append("a replay case comes from a transcript")
    if case.get("tier") == "reask":
        if case.get("original_reply"):
            errors.append("a reask case has no original_reply: the transcript is gone")
        if case.get("conversation") or case.get("work"):
            errors.append("a reask case has no conversation or work: only the message survives")

    for key in ("conversation", "work"):
        items = case.get(key) or []
        if not isinstance(items, list):
            errors.append(f"{key} must be a list")
            continue
        for item in items:
            if not isinstance(item, dict) or item.get("role") not in ROLES or not isinstance(item.get("text"), str):
                errors.append(f"every {key} item needs a role in {ROLES} and a text")
                break

    files = case.get("files") or []
    if not isinstance(files, list):
        errors.append("files must be a list")
    else:
        paths = []
        for entry in files:
            if not isinstance(entry, dict) or not isinstance(entry.get("content"), str):
                errors.append("every file needs a path and a text content")
                continue
            if not safe_relative_path(entry.get("path")):
                errors.append(f"file path {entry.get('path')!r} must be relative and stay inside the folder")
            paths.append(entry.get("path"))
        if len(paths) != len(set(paths)):
            errors.append("file paths must be unique")

    workspace = case.get("workspace")
    if workspace is not None:
        if not isinstance(workspace, dict) or not isinstance(workspace.get("repo"), str) \
                or not COMMIT_RE.match(workspace.get("commit") or ""):
            errors.append("workspace must be null or {repo, commit} with a hex commit")
        elif not safe_relative_path(workspace["repo"]):
            errors.append("workspace.repo must be a folder name under the repos root")

    for key in ("terms", "must_cover"):
        value = case.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
            errors.append(f"{key} must be a list of text")

    if not errors:
        visible = _norm(arm_visible_text(case))
        if _norm(case["flaw"]) in visible:
            errors.append("the flaw text appears in what the session sees: the answer key leaks")
        follow_up = _norm(case["follow_up"])
        if len(follow_up) >= LEAK_MIN_CHARS and follow_up in visible:
            errors.append("the follow-up appears in what the session sees: the answer key leaks")
    return errors


# --- What the session under test receives -----------------------------------------------------

_LABELS = {"user": "user", "assistant": "assistant", "tool_call": "tool call", "tool_result": "tool result"}

REPLAY_FRAME = """This is a working session that was already in progress. Below are the conversation so far, the user's newest message, and the tool calls you already made for that message, with their results. Continue the session: write your reply to the newest message, as you would in that session. The repositories, network and background processes of that session are not available now, so base the reply on what is shown here.

<conversation>
{conversation}
</conversation>

<newest_message>
{message}
</newest_message>

<work_already_done_for_this_message>
{work}
</work_already_done_for_this_message>"""


def render_items(items):
    return "\n\n".join(f"[{_LABELS[item['role']]}]\n{item['text']}" for item in items)


def build_session_prompt(case):
    """The exact text the session under test is given, read from stdin."""
    if case["tier"] == "reask":
        note = case.get("note")
        return case["message"] + (f"\n\n{note}" if note else "")
    return REPLAY_FRAME.format(
        conversation=render_items(case.get("conversation") or []) or "(this is the first message)",
        message=case["message"],
        work=render_items(case.get("work") or []) or "(none)",
    )


# The session under test runs the way the owner runs Claude Code: in bypassPermissions mode, with
# the shell. Three walls keep a replayed session ("pr then merge") from touching anything real,
# each verified with a live session on 2026-10-03:
# - the shell runs in Claude Code's sandbox: it writes only to its own folder and the temp area
#   (`touch` inside ~/repos/tribe: "Operation not permitted");
# - the sandbox denies every network domain (curl, gh, git over https and ssh all failed);
# - deny rules stop the file tools from writing anywhere under the owner's home folder.
# Without bypass mode, Claude Code refuses compound shell commands such as the Todd way style's own
# tool lookup, which would handicap the style in a way the owner never sees.
SESSION_TOOLS = "Bash,Read,Glob,Grep,Write,Edit,Agent"
SESSION_ENV = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
# The name Claude Code reports in its init event when no output style is selected.
NO_STYLE = "default"


def session_settings(style_name, home):
    """The project settings every session runs under: the sandbox, the deny rules, and the arm's
    style if it has one. `home` is the owner's home folder, which the file tools may not write in."""
    settings = {
        "sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True, "allowUnsandboxedCommands": False,
                    "network": {"allowedDomains": [], "deniedDomains": ["*"]}},
        "permissions": {"deny": [f"{tool}(/{home}/**)" for tool in ("Write", "Edit", "NotebookEdit")]
                        + ["WebFetch", "WebSearch"]},
    }
    if style_name:
        settings["outputStyle"] = style_name
    return settings


def build_session_command(model, effort, max_budget_usd):
    """argv for one session under test. The prompt goes in on stdin, so a message that starts
    with '-' is never read as a flag."""
    return [
        "claude", "-p",
        "--model", model,
        "--effort", effort,
        "--setting-sources", "project",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--tools", SESSION_TOOLS,
        "--permission-mode", "bypassPermissions",
        "--permission-prompts", "none",
        "--exclude-dynamic-system-prompt-sections",
        "--output-format", "stream-json", "--verbose",
        "--max-budget-usd", str(max_budget_usd),
    ]


# --- Reading a session's stream-json output ----------------------------------------------------

RATE_LIMIT_RE = re.compile(r"hit your (?:session|usage|weekly|daily|org)[a-z ]* limit|rate.?limit|overloaded", re.I)


def parse_stream(lines):
    """Pull what the eval needs from `claude -p --output-format stream-json` lines.

    `reply` is every text block the main thread wrote, in order: what the owner would have seen
    in the terminal. Subagent text (the blind reader) carries a parent_tool_use_id and is left out.
    """
    parsed = {"init": {}, "texts": [], "tool_uses": {}, "result": None}
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            parsed["init"] = {key: event.get(key) for key in
                              ("model", "output_style", "permissionMode", "tools", "claude_code_version")}
        elif kind == "assistant":
            subagent = bool(event.get("parent_tool_use_id"))
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use":
                    name = block.get("name") or "?"
                    parsed["tool_uses"][name] = parsed["tool_uses"].get(name, 0) + 1
                elif block.get("type") == "text" and not subagent and (block.get("text") or "").strip():
                    parsed["texts"].append(block["text"])
        elif kind == "result":
            parsed["result"] = {key: event.get(key) for key in
                                ("subtype", "is_error", "result", "total_cost_usd", "duration_ms", "num_turns",
                                 "api_error_status", "modelUsage")}
    parsed["reply"] = "\n\n".join(parsed["texts"]).strip()
    return parsed


def classify_session(parsed, expected_style, expected_model):
    """('ok' | 'rate_limited' | 'error' | 'isolation_breach', reason).

    A breach is a session that ran under a different output style or model than its arm names:
    its reply measures the wrong thing, so it is never graded.
    """
    result = parsed.get("result")
    if result is None:
        return "error", "no result event: the session did not finish"
    text = result.get("result") or ""
    if result.get("is_error"):
        if result.get("api_error_status") == 429 or RATE_LIMIT_RE.search(text):
            return "rate_limited", text[:200]
        return "error", f"{result.get('subtype')}: {text[:200]}"
    init = parsed.get("init") or {}
    if init.get("output_style") != expected_style:
        return "isolation_breach", f"output style {init.get('output_style')!r}, expected {expected_style!r}"
    if init.get("model") != expected_model:
        return "isolation_breach", f"model {init.get('model')!r}, expected {expected_model!r}"
    if not parsed.get("reply"):
        return "error", "the session wrote no reply text"
    return "ok", ""


# --- The judge -----------------------------------------------------------------------------------

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "follow_up_needed": {"type": "boolean"},
        "evidence": {"type": "string"},
        "answers_message": {"type": "boolean"},
        "wrong_claims": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["follow_up_needed", "evidence", "answers_message", "wrong_claims"],
    "additionalProperties": False,
}

JUDGE_FRAME = """You are grading one reply that an AI coding assistant wrote to its owner. The owner is a senior software engineer whose first language is Vietnamese. The question is whether this reply would have spared the owner a follow-up message they really had to send.

{context}

## What really happened
After reading the assistant's original reply (not shown to you), the owner sent this follow-up:
<follow_up>
{follow_up}
</follow_up>
What that follow-up shows the original reply lacked: {flaw}
{must_cover}
## The reply to grade
<reply>
{reply}
</reply>

## Answer these
1. follow_up_needed: after reading THIS reply, would the owner still need to send that follow-up, or another message with the same intent? A reply that avoids the problem altogether (for example, it never uses the term the owner asked about, and is still clear) does not need the follow-up.
2. evidence: quote the words in this reply that settle question 1, or name exactly what is missing.
3. answers_message: does this reply answer the message it was written for?
4. wrong_claims: statements in this reply that are false, or that contradict what is shown above. List a claim only when you can tell it is wrong from what is shown or from well-known facts; never list a claim only because you cannot check it. Use an empty list when there are none."""

JUDGE_CONTEXT_CHARS = 40000
JUDGE_FILE_CHARS = 8000


def _clip(text, limit):
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    return f"{text[:head]}\n[... {len(text) - limit} characters cut ...]\n{text[-(limit - head):]}"


def _tail_items(items, limit):
    """The most recent items that fit in `limit` characters, oldest first."""
    kept, total = [], 0
    for item in reversed(items):
        size = len(item["text"]) + 20
        if kept and total + size > limit:
            break
        kept.append(item)
        total += size
    return list(reversed(kept))


def build_judge_context(case):
    if case["tier"] == "replay":
        budget = JUDGE_CONTEXT_CHARS - len(case["message"])
        work = render_items(case.get("work") or [])
        work = _clip(work, max(budget // 2, 2000)) if work else "(none)"
        conversation = render_items(_tail_items(case.get("conversation") or [], max(budget - len(work), 2000)))
        return ("## The conversation before the message (most recent part)\n<conversation>\n"
                f"{conversation or '(this was the first message)'}\n</conversation>\n\n"
                f"## The message the reply answers\n<message>\n{case['message']}\n</message>\n\n"
                "## Tool calls the assistant made for that message, with their results\n"
                f"<work>\n{work}\n</work>")
    parts = [f"## The message the reply answers\n<message>\n{case['message']}\n</message>"]
    if case.get("note"):
        parts.append(f"Note given to the assistant with the message: {case['note']}")
    workspace = case.get("workspace")
    if workspace:
        parts.append(f"The assistant could read the repository `{workspace['repo']}` at commit "
                     f"`{workspace['commit']}`. You cannot see it, so do not call a claim about that "
                     "repository wrong unless it contradicts something shown here.")
    for entry in case.get("files") or []:
        parts.append(f"File the assistant could read, `{entry['path']}`:\n<file>\n"
                     f"{_clip(entry['content'], JUDGE_FILE_CHARS)}\n</file>")
    return "\n\n".join(parts)


def build_judge_prompt(case, reply):
    must_cover = ""
    if case.get("must_cover"):
        must_cover = "Points a complete answer covers:\n" + "\n".join(f"- {p}" for p in case["must_cover"]) + "\n"
    return JUDGE_FRAME.format(context=build_judge_context(case), follow_up=case["follow_up"],
                              flaw=case["flaw"], must_cover=must_cover, reply=reply)


def build_judge_command(model, effort):
    """argv for one judge session: no tools, no customization, structured output."""
    return ["claude", "-p", "--model", model, "--effort", effort, "--safe-mode", "--tools", "",
            "--no-session-persistence", "--output-format", "json",
            "--json-schema", json.dumps(JUDGE_SCHEMA)]


def parse_verdict(payload):
    """The judge's verdict from `claude -p --output-format json` output, or ValueError."""
    if isinstance(payload, dict) and isinstance(payload.get("structured_output"), dict):
        verdict = payload["structured_output"]
    elif isinstance(payload, dict) and isinstance(payload.get("result"), str):
        text = payload["result"].strip()
        fenced = re.search(r"\{.*\}", text, re.S)
        if not fenced:
            raise ValueError("the judge returned no JSON object")
        verdict = json.loads(fenced.group(0))
    else:
        raise ValueError("unrecognised judge output")
    if not isinstance(verdict, dict):
        raise ValueError("the verdict is not an object")
    if not isinstance(verdict.get("follow_up_needed"), bool) or not isinstance(verdict.get("answers_message"), bool):
        raise ValueError("follow_up_needed and answers_message must be true or false")
    if not isinstance(verdict.get("evidence"), str):
        raise ValueError("evidence must be text")
    claims = verdict.get("wrong_claims")
    if not isinstance(claims, list) or not all(isinstance(c, str) for c in claims):
        raise ValueError("wrong_claims must be a list of text")
    return {"follow_up_needed": verdict["follow_up_needed"], "evidence": verdict["evidence"],
            "answers_message": verdict["answers_message"], "wrong_claims": claims}


def is_win(verdict):
    """A reply wins only when the follow-up is not needed, the message is answered, and nothing is wrong."""
    return (not verdict["follow_up_needed"]) and verdict["answers_message"] and not verdict["wrong_claims"]


STUB_FRAME = """Below is a message an engineer sent to their AI coding assistant. Write the assistant's reply. The reply must be plausible and on topic, but it must have exactly this flaw: {flaw}. Write only the reply text.

<message>
{message}
</message>"""


def build_stub_prompt(case):
    """A deliberately flawed reply for a case. The judge must say the follow-up is still
    needed; a judge that passes the stub cannot tell the flaw apart, so that case is not scored."""
    return STUB_FRAME.format(flaw=case["flaw"], message=case["message"])


def build_stub_command(model, effort):
    return ["claude", "-p", "--model", model, "--effort", effort, "--safe-mode", "--tools", "",
            "--no-session-persistence", "--output-format", "json"]


# --- Mechanical measures of a reply ----------------------------------------------------------------

VI_LETTERS = set("ăâđêôơưàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ")
LABEL_RE = re.compile(r"\b[A-Z]{1,3}-?\d{1,2}(?:-\d{1,2})?\b")
FENCE_RE = re.compile(r"```.*?```", re.S)


def vi_ratio(text):
    letters = [c for c in unicodedata.normalize("NFC", text).lower() if c.isalpha()]
    return sum(c in VI_LETTERS for c in letters) / len(letters) if letters else 0.0


def detect_language(text):
    """'vi' when Vietnamese letters make up at least 4% of letters. Vietnamese prose sits near
    20%, English with a few borrowed Vietnamese words below 1%."""
    return "vi" if vi_ratio(text) >= 0.04 else "en"


def bare_labels(text):
    """Labels like D1, N5, R2-1, CU-3 not followed by a gloss in parentheses or after a colon."""
    prose = FENCE_RE.sub("", text)
    count = 0
    for match in LABEL_RE.finditer(prose):
        after = prose[match.end():match.end() + 3]
        if not re.match(r"\s?[(:—]", after):
            count += 1
    return count


def sentences(text):
    prose = FENCE_RE.sub(" ", text)
    lines = [re.sub(r"^\s*(?:[-*+]|\d+\.|#+)\s+", "", line) for line in prose.splitlines()
             if line.strip() and not line.lstrip().startswith("|")]
    return [s for s in re.split(r"(?<=[.!?])\s+", " ".join(lines)) if len(s.split()) >= 3]


STATUS_LINE_RE = re.compile(r"^(?:done|not done|xong|chưa xong)\b", re.I)


def reply_metrics(text, case):
    found = sentences(text)
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    language = detect_language(text)
    return {
        "words": len(text.split()),
        "language": language,
        "language_match": language == case["language"],
        "bare_labels": bare_labels(text),
        "long_sentence_share": round(sum(len(s.split()) > 25 for s in found) / len(found), 3) if found else 0.0,
        # Markdown emphasis or a heading marker in front of "Done" still counts as a status line.
        "status_line": bool(STATUS_LINE_RE.match(first_line.strip().lstrip("*_`#> ").strip())),
    }


# --- Statistics ------------------------------------------------------------------------------------


def wilson(wins, total, z=1.96):
    """95% Wilson score interval for a rate; (0, 0) when nothing was graded."""
    if total == 0:
        return (0.0, 0.0)
    p = wins / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    spread = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return (max(0.0, (centre - spread) / denominator), min(1.0, (centre + spread) / denominator))


def binom_tail_ge(k, n):
    """P(X >= k) for X ~ Binomial(n, 1/2)."""
    if n == 0:
        return 1.0
    return sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n


def mcnemar_exact(only_a, only_b):
    """Two-sided exact McNemar p-value from the two discordant counts."""
    n = only_a + only_b
    if n == 0:
        return 1.0
    return min(1.0, 2 * binom_tail_ge(max(only_a, only_b), n))


def cell_win(run_wins):
    """A cell wins when more than half of its graded runs win: 2 of 3, 1 of 1. None when no run was
    graded. A tie (1 of 2) is not a win."""
    graded = [w for w in run_wins if w is not None]
    if not graded:
        return None
    return sum(graded) * 2 > len(graded)


def rate_summary(wins):
    """wins: list of bool for graded cells."""
    k, n = sum(wins), len(wins)
    low, high = wilson(k, n)
    return {"wins": k, "graded": n, "rate": round(k / n, 3) if n else None,
            "ci95": [round(low, 3), round(high, 3)]}


def paired(cell_a, cell_b):
    """Compare two arms on the cases both graded. cell_x: {case_id: bool|None}."""
    common = sorted(c for c in cell_a if cell_a[c] is not None and cell_b.get(c) is not None)
    only_a = sum(1 for c in common if cell_a[c] and not cell_b[c])
    only_b = sum(1 for c in common if cell_b[c] and not cell_a[c])
    return {"cases": len(common), "both": sum(1 for c in common if cell_a[c] and cell_b[c]),
            "only_a": only_a, "only_b": only_b,
            "neither": sum(1 for c in common if not cell_a[c] and not cell_b[c]),
            "p_two_sided": round(mcnemar_exact(only_a, only_b), 4)}


RATCHET_P = 0.05


def ratchet(baseline, candidate):
    """Compare one arm's cell wins between a committed baseline and a new run, case by case.

    REGRESSION when significantly more cases flipped from win to loss than the other way
    (one-sided exact binomial, p < 0.05); IMPROVED in the mirror case; otherwise HOLD. Cases in only
    one of the two runs are listed, never scored, so adding new cases cannot fake a change.
    """
    common = sorted(c for c in baseline if baseline[c] is not None and candidate.get(c) is not None)
    lost = [c for c in common if baseline[c] and not candidate[c]]
    gained = [c for c in common if candidate[c] and not baseline[c]]
    n = len(lost) + len(gained)
    p_worse = binom_tail_ge(len(lost), n) if lost else 1.0
    p_better = binom_tail_ge(len(gained), n) if gained else 1.0
    if len(lost) > len(gained) and p_worse < RATCHET_P:
        status = "REGRESSION"
    elif len(gained) > len(lost) and p_better < RATCHET_P:
        status = "IMPROVED"
    else:
        status = "HOLD"
    before = sum(1 for c in common if baseline[c])
    after = sum(1 for c in common if candidate[c])
    return {"status": status, "cases": len(common), "before": before, "after": after,
            "lost": lost, "gained": gained, "p_worse": round(p_worse, 4), "p_better": round(p_better, 4),
            "only_in_baseline": sorted(c for c in baseline if c not in candidate),
            "only_in_candidate": sorted(c for c in candidate if c not in baseline)}


# --- Mining: where in the owner's history a reply did not land -------------------------------------

SIGNALS = (
    ("dont-understand", r"\b(?:don'?t|do not|dont|didn'?t|can'?t|cannot|still (?:don'?t|dont|not))\s+(?:understand|get it|follow)\b"
                        r"|không hiểu|ko hiểu|chưa hiểu|hổng hiểu|hk hiểu|hiẻu"),
    ("what-is", r"\bwhat(?:'s| is| are| does| do you mean| was your)\b.{0,80}|là gì|nghĩa là gì|là sao|cái gì vậy"),
    ("explain-more", r"\b(?:explain|elaborate)\b|giải thích|nói rõ|chi tiết hơn|cặn kẽ|\bmore context\b|surrounding context|ngữ cảnh"),
    ("vietnamese", r"tiếng việt|tiếng vịet|dịch ra|dịch đoạn|\bin vietnamese\b"),
    ("example-visual", r"\bexamples?\b|\bvisuali[sz]e\b|\bdiagram\b|ví dụ|vẽ (?:flow|ra)|capture image"),
    ("status", r"^\s*(?:all |everything )?done\s*\??\s*$|\bdone\?|\bwhat is the progress\b|\bnext step\b|\bwhat have you done\b"),
    ("frustration", r"\bwtf\b|what the (?:fuck|hell)|\?\?\?|\bclgt\b"),
)
_SIGNAL_RES = tuple((name, re.compile(pattern, re.I)) for name, pattern in SIGNALS)
PASTE_RE = re.compile(r"\[Pasted text #(\d+)(?: \+\d+ lines)?\]")


def signals(text):
    """Names of every barrier signal in one owner message, in SIGNALS order."""
    return [name for name, regex in _SIGNAL_RES if regex.search(text or "")]


def expand_pasted(display, pasted, paste_cache):
    """Replace '[Pasted text #N +M lines]' with the pasted text. `pasted` is the history entry's
    pastedContents; `paste_cache` maps a content hash to its text (the files in
    ~/.claude/paste-cache). A paste whose text is gone stays a placeholder."""
    def replace(match):
        entry = (pasted or {}).get(match.group(1)) or {}
        if entry.get("content"):
            return entry["content"]
        cached = paste_cache.get(entry.get("contentHash") or "")
        return cached if cached is not None else match.group(0)
    return PASTE_RE.sub(replace, display or "")


def history_candidates(entries, paste_cache):
    """Owner messages in ~/.claude/history.jsonl that carry a barrier signal, each with the
    message before it in the same session (the one the lost reply answered)."""
    by_session = {}
    for entry in entries:
        if entry.get("sessionId"):
            by_session.setdefault(entry["sessionId"], []).append(entry)
    found = []
    for session, prompts in by_session.items():
        prompts.sort(key=lambda e: e.get("timestamp") or 0)
        previous = None
        for index, entry in enumerate(prompts):
            display = entry.get("display") or ""
            if display.startswith(("/", "!")):
                continue
            text = expand_pasted(display, entry.get("pastedContents"), paste_cache)
            names = signals(text)
            if names and previous is not None:
                found.append({"session": session, "index": index, "timestamp": entry.get("timestamp"),
                              "project": entry.get("project"), "message": previous, "follow_up": text,
                              "signals": names})
            previous = text
    found.sort(key=lambda c: c["timestamp"] or 0)
    return found


# --- Turning a transcript into replay items ----------------------------------------------------

REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
SKIP_USER_PREFIXES = ("<local-command", "<command-", "Caveat:")


def _tool_result_text(block):
    content = block.get("content")
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
    return content if isinstance(content, str) else ""


def _tool_call_text(block):
    name = block.get("name") or "?"
    data = block.get("input") or {}
    for key in ("command", "description", "file_path", "pattern", "prompt", "url", "query"):
        if isinstance(data.get(key), str):
            return f"{name}: {data[key]}"
    return f"{name}: {json.dumps(data, ensure_ascii=False)[:300]}"


def transcript_items(entries):
    """The main thread of a Claude Code transcript as replay items, in order.

    Kept: the owner's text (and task notifications, which arrive as user messages), assistant
    text, tool calls and tool results. Dropped: subagent threads, meta entries, thinking, local
    command echoes and injected system reminders.
    """
    items = []
    for entry in entries:
        if entry.get("isSidechain") or entry.get("isMeta") or entry.get("type") not in ("user", "assistant"):
            continue
        content = (entry.get("message") or {}).get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if entry["type"] == "user" and kind == "text":
                text = REMINDER_RE.sub("", block.get("text") or "").strip()
                if text and not text.startswith(SKIP_USER_PREFIXES):
                    items.append({"role": "user", "text": text, "uuid": entry.get("uuid")})
            elif entry["type"] == "user" and kind == "tool_result":
                items.append({"role": "tool_result", "text": _tool_result_text(block), "uuid": entry.get("uuid")})
            elif entry["type"] == "assistant" and kind == "text" and (block.get("text") or "").strip():
                items.append({"role": "assistant", "text": block["text"], "uuid": entry.get("uuid")})
            elif entry["type"] == "assistant" and kind == "tool_use":
                items.append({"role": "tool_call", "text": _tool_call_text(block), "uuid": entry.get("uuid")})
    return items


def clip_item(item, limit):
    return {"role": item["role"], "text": _clip(item["text"], limit)}


def replay_parts(items, follow_up_index, conversation_chars=40000, item_chars=6000, result_chars=2500):
    """Split a transcript at the owner's follow-up into the parts of a replay case.

    The message is the last user item before the follow-up; the original reply is every assistant
    text between the two (what the owner read); the work is the tool calls and results in between.
    The conversation is everything before the message, user and assistant text only, the most
    recent part that fits, always keeping the session's first message (it states the task).
    """
    if not (0 < follow_up_index < len(items)) or items[follow_up_index]["role"] != "user":
        raise ValueError("follow_up_index must point at a user item after the first")
    message_index = max((i for i in range(follow_up_index) if items[i]["role"] == "user"), default=None)
    if message_index is None:
        raise ValueError("no user message before the follow-up")
    turn = items[message_index + 1:follow_up_index]
    reply = "\n\n".join(item["text"] for item in turn if item["role"] == "assistant").strip()
    if not reply:
        raise ValueError("the turn before the follow-up has no assistant text")
    work = [clip_item(item, result_chars if item["role"] == "tool_result" else 600)
            for item in turn if item["role"] in ("tool_call", "tool_result")]
    earlier = [clip_item(item, item_chars) for item in items[:message_index] if item["role"] in ("user", "assistant")]
    conversation = []
    if earlier:
        first, rest = earlier[0], earlier[1:]
        conversation = [first] + _tail_items(rest, max(conversation_chars - len(first["text"]), 0))
    return {"conversation": conversation, "message": items[message_index]["text"], "work": work,
            "original_reply": reply, "follow_up": items[follow_up_index]["text"]}


# --- Secrets never enter a case file -------------------------------------------------------------------

SECRET_PATTERNS = (
    r"sk-ant-[A-Za-z0-9_-]{10,}",
    r"sk-[A-Za-z0-9]{20,}",
    r"gh[pousr]_[A-Za-z0-9]{20,}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"AKIA[0-9A-Z]{16}",
    r"xox[abposr]-[A-Za-z0-9-]{10,}",
    r"AIza[0-9A-Za-z_-]{30,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    r"(?i)(?:password|passwd|pwd|secret|token|api[_-]?key)(\s*[:=]\s*)['\"]?[^\s'\"]{6,}",
)
_SECRET_RES = tuple(re.compile(p, re.S) for p in SECRET_PATTERNS)


def scrub(text, literals=()):
    """`text` with known secret shapes, and every literal the curator names, replaced by [REDACTED]."""
    for literal in literals:
        if literal:
            text = text.replace(literal, "[REDACTED]")
    for regex in _SECRET_RES:
        text = regex.sub(lambda m: (m.group(0)[:m.start(1) - m.start(0)] + m.group(1) + "[REDACTED]")
                         if m.re.groups else "[REDACTED]", text)
    return text


def scrub_case(case, literals=()):
    """A copy of `case` with every text field scrubbed."""
    def walk(value):
        if isinstance(value, str):
            return scrub(value, literals)
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        return value
    return walk(case)


# --- The report ------------------------------------------------------------------------------------

CALIBRATION_GATE = 0.9
COVERAGE_GATE = 0.9


def _median(values):
    values = sorted(values)
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


def summarize_run(cases, sessions, verdicts, arms, pairs, runs):
    """One run folder's results as plain data: report.py renders it, ratchet.py compares two.

    cases: the case dicts; sessions: every session record (stubs excluded); verdicts: every verdict
    record from one judge model, calibration replies included; arms: arm names in display order;
    pairs: (arm_a, arm_b) comparisons; runs: runs per cell the experiment planned.

    A case is scored only when the judge said "follow-up still needed" for its stub — a reply
    written to have the flaw. A judge that passes stubs is lenient, and the calibration gate fails.
    A replay case also needs the judge to fail its original reply: when the judge finds the original
    clear, the owner's follow-up was probably about something other than wording (impatience,
    a new need), and the case cannot tell styles apart. That excludes the case but does not count
    against the judge.
    """
    meta = {c["id"]: c for c in cases}
    by_key = {}
    for v in verdicts:
        by_key[(v["case"], v["arm"], v["run"])] = v

    excluded, scored = {}, []
    stubs_graded = stubs_caught = originals_clear = 0
    for case_id in sorted(meta):
        stub = by_key.get((case_id, "stub", 1))
        if stub is None or stub.get("verdict") is None:
            excluded[case_id] = "no graded stub: the judge is uncalibrated on this case"
            continue
        stubs_graded += 1
        if not stub["verdict"]["follow_up_needed"]:
            excluded[case_id] = "the judge passed the stub, a reply written to have the flaw"
            continue
        stubs_caught += 1
        if meta[case_id]["tier"] == "replay":
            original = by_key.get((case_id, "original", 1))
            if original is None or original.get("verdict") is None:
                excluded[case_id] = "the original reply was not graded"
                continue
            if not original["verdict"]["follow_up_needed"]:
                originals_clear += 1
                excluded[case_id] = ("the judge found the original reply clear: the owner's follow-up was "
                                     "probably not about its wording")
                continue
        scored.append(case_id)

    cells = {}
    for arm in arms:
        cells[arm] = {}
        for case_id in scored:
            wins = [by_key[(case_id, arm, run)]["win"] if (case_id, arm, run) in by_key else None
                    for run in range(1, runs + 1)]
            cells[arm][case_id] = cell_win(wins)

    def subset(arm, predicate):
        return rate_summary([w for c, w in cells[arm].items() if w is not None and predicate(meta[c])])

    summary = {}
    for arm in arms:
        summary[arm] = {
            "all": subset(arm, lambda c: True),
            "out_of_sample": subset(arm, lambda c: not c["in_sample"]),
            "in_sample": subset(arm, lambda c: c["in_sample"]),
            "by_tier": {t: subset(arm, lambda c, t=t: c["tier"] == t) for t in TIERS},
            "by_category": {k: subset(arm, lambda c, k=k: c["category"] == k) for k in CATEGORIES
                            if any(meta[x]["category"] == k for x in scored)},
        }

    compared = []
    for a, b in pairs:
        out = [c for c in scored if not meta[c]["in_sample"]]
        compared.append({"a": a, "b": b,
                         "all": paired(cells[a], cells[b]),
                         "out_of_sample": paired({c: cells[a][c] for c in out}, {c: cells[b][c] for c in out})})

    session_stats = {}
    for arm in arms:
        records = [s for s in sessions if s["arm"] == arm and s["case"] in meta]
        ok = [s for s in records if s["status"] == "ok"]
        status_cases = [s for s in ok if meta[s["case"]]["category"] == "status"]
        session_stats[arm] = {
            "planned": len(meta) * runs,
            "ok": len(ok),
            "error": sum(1 for s in records if s["status"] == "error"),
            "isolation_breach": sum(1 for s in records if s["status"] == "isolation_breach"),
            "rate_limited": sum(1 for s in records if s["status"] == "rate_limited"),
            "cost_usd": round(sum(s.get("cost_usd") or 0 for s in ok), 2),
            "mean_cost_usd": round(sum(s.get("cost_usd") or 0 for s in ok) / len(ok), 3) if ok else None,
            "median_seconds": round((_median([s.get("duration_ms") or 0 for s in ok]) or 0) / 1000, 1),
            "median_words": _median([s["metrics"]["words"] for s in ok if s.get("metrics")]),
            "mean_bare_labels": round(sum(s["metrics"]["bare_labels"] for s in ok if s.get("metrics")) / len(ok), 2) if ok else None,
            "language_match_rate": round(sum(1 for s in ok if s.get("metrics") and s["metrics"]["language_match"]) / len(ok), 3) if ok else None,
            "status_line_rate_on_status_cases": round(sum(1 for s in status_cases if s["metrics"]["status_line"]) / len(status_cases), 3) if status_cases else None,
            "subagent_rate": round(sum(1 for s in ok if (s.get("tool_uses") or {}).get("Agent") or (s.get("tool_uses") or {}).get("Task")) / len(ok), 3) if ok else None,
        }

    graded_cells = sum(1 for arm in arms for w in cells[arm].values() if w is not None)
    planned_cells = len(scored) * len(arms)
    breaches = sum(s["isolation_breach"] for s in session_stats.values())
    sensitivity = stubs_caught / stubs_graded if stubs_graded else 0.0
    gates = [
        {"gate": "isolation", "pass": breaches == 0,
         "detail": f"{breaches} session(s) ran under a style or model other than their arm's"},
        {"gate": "judge calibration", "pass": stubs_graded > 0 and sensitivity >= CALIBRATION_GATE,
         "detail": f"the judge caught {stubs_caught} of {stubs_graded} stubs written to have the flaw "
                   f"({sensitivity:.0%}); needs {CALIBRATION_GATE:.0%}"},
        {"gate": "coverage", "pass": planned_cells > 0 and graded_cells / planned_cells >= COVERAGE_GATE,
         "detail": f"{graded_cells} of {planned_cells} scored cells graded; needs {COVERAGE_GATE:.0%}"},
    ]
    judge_models = sorted({v.get("judge_model") for v in verdicts if v.get("judge_model")})
    return {"judge_model": judge_models[0] if len(judge_models) == 1 else judge_models, "runs": runs,
            "arms": list(arms), "cases": len(meta), "scored_cases": scored, "excluded": excluded,
            "originals_judged_clear": originals_clear,
            "gates": gates, "cells": cells, "summary": summary, "pairs": compared, "sessions": session_stats,
            "judge_cost_usd": round(sum(v.get("cost_usd") or 0 for v in verdicts), 2)}


def _rate(s):
    if not s or not s["graded"]:
        return "—"
    return f"{s['wins']}/{s['graded']} = {s['rate']:.0%} (95% CI {s['ci95'][0]:.0%}–{s['ci95'][1]:.0%})"


def render_report_md(report):
    """The report as Markdown: gates first (can the numbers be trusted?), then results."""
    lines = [f"# Barrier eval report — judge `{report['judge_model']}`, {report['runs']} run(s) per cell", ""]
    trusted = all(g["pass"] for g in report["gates"])
    lines += ["## Gates", "", "| Gate | Result | Detail |", "| --- | --- | --- |"]
    lines += [f"| {g['gate']} | {'PASS' if g['pass'] else 'FAIL'} | {g['detail']} |" for g in report["gates"]]
    lines += ["", "All gates pass: the numbers below can be trusted." if trusted else
              "**A gate failed: do not trust the numbers below until it passes.**", ""]
    lines += [f"{len(report['scored_cases'])} of {report['cases']} cases scored. A cell wins when the judge says the "
              "owner's real follow-up would not have been needed, the message is answered, and nothing is wrong.", ""]
    lines += ["## Win rate per arm", "", "| Arm | All scored | Out of sample | In sample |", "| --- | --- | --- | --- |"]
    for arm in report["arms"]:
        s = report["summary"][arm]
        lines.append(f"| {arm} | {_rate(s['all'])} | {_rate(s['out_of_sample'])} | {_rate(s['in_sample'])} |")
    lines += ["", "*In sample* = cases from the 10 sessions Part C was written from; the verdict on Part C rests on "
              "the out-of-sample column.", ""]
    lines += ["## Paired comparisons (same cases)", "",
              "| A vs B | Cases | Only A won | Only B won | Both | Neither | p (two-sided) |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for pair in report["pairs"]:
        for label in ("all", "out_of_sample"):
            p = pair[label]
            lines.append(f"| {pair['a']} vs {pair['b']} ({label.replace('_', ' ')}) | {p['cases']} | {p['only_a']} | "
                         f"{p['only_b']} | {p['both']} | {p['neither']} | {p['p_two_sided']} |")
    lines += ["", "## By kind of barrier", ""]
    categories = sorted({k for arm in report["arms"] for k in report["summary"][arm]["by_category"]})
    lines += ["| Kind | " + " | ".join(report["arms"]) + " |", "| --- |" + " --- |" * len(report["arms"])]
    for k in categories:
        lines.append(f"| {k} | " + " | ".join(_rate(report["summary"][arm]["by_category"].get(k)) for arm in report["arms"]) + " |")
    lines += ["", "## Sessions", "",
              "| Arm | OK / planned | Errors | Breaches | Cost (USD) | Median s | Median words | Bare labels / reply | Language match | Status line (status cases) | Used a subagent |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for arm in report["arms"]:
        s = report["sessions"][arm]
        lines.append(f"| {arm} | {s['ok']} / {s['planned']} | {s['error']} | {s['isolation_breach']} | {s['cost_usd']} | "
                     f"{s['median_seconds']} | {s['median_words']} | {s['mean_bare_labels']} | {s['language_match_rate']} | "
                     f"{s['status_line_rate_on_status_cases']} | {s['subagent_rate']} |")
    lines += ["", f"Judge cost: {report['judge_cost_usd']} USD.", ""]
    if report["excluded"]:
        lines += ["## Cases not scored", ""] + [f"- `{c}`: {why}" for c, why in sorted(report["excluded"].items())] + [""]
    return "\n".join(lines)
