#!/usr/bin/env python3
"""
Claude Code Stop hook: nudge toward a handoff (save state, start a fresh
session) at a good breakpoint, instead of letting context grow past a
working budget.

Inspired by github.com/kunchenguid/compact-adviser, which uses TypeSafe's Jev
to time `/compact`. This version times a handoff instead: durable state in a
file plus a fresh session, not an in-context summary. Two stages:
  1. Local gates (free): opt-in check, token floor, cooldown.
  2. Jev scoring (one Noul call): is this a natural checkpoint?

Privacy: only *derived, structured* signals are sent to Jev -- token counts,
tool-call tallies, elapsed time, and boolean flags computed *locally* from the
last assistant message. Raw message text, code, and file content never leave
the machine.

Opt in with `CC_HANDOFF_NUDGE=1` in ~/.claude/settings.json env.
Opt a project out with its `.claude/settings.json`:
    { "env": { "CC_HANDOFF_NUDGE": "0" } }

Optional env:
  CC_CTX_BUDGET            working budget in tokens (default 200000)
  CC_HANDOFF_NUDGE_MIN     don't even evaluate below this many tokens (default 150000)
  CC_HANDOFF_NUDGE_COOLDOWN  seconds between nudges in one session (default 900)
  CC_HANDOFF_NUDGE_COMMAND your handoff command, e.g. "/handoff" (default: none,
                           the nudge just says to save progress and start fresh)
  CC_HANDOFF_NUDGE_DEBUG   "1" prints reasoning to stderr

TYPESAFE_API_KEY, or a `TYPESAFE_API_KEY=...` line in ~/.claude/typesafe.env
(a file outside any git repo -- never put this key in a project's
.claude/settings.json, which can end up in a company-shared repo). Without a
key, falls back to a flat "over budget" heuristic instead of Jev-scored
breakpoint timing.

Fails open: any error here exits 0 silently. A nudge tool must never break
the session it's trying to protect.
"""
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TYPESAFE_ENDPOINT = "https://api.typesafe.ai/v1/systemone"

NEXT_STEP_RE = re.compile(
    r"\b(next step|still need|todo|to-?do|not yet|remaining|left to do|"
    r"in progress|need to (?:also|still)|haven'?t (?:yet )?)\b", re.I,
)
COMPLETION_RE = re.compile(
    r"\b(done|complete|finished|committed|merged|deployed|verified|"
    r"wrapped up|all set|that'?s it)\b", re.I,
)


def debug(msg):
    if os.environ.get("CC_HANDOFF_NUDGE_DEBUG") == "1":
        print(f"[handoff-nudge] {msg}", file=sys.stderr)


def load_api_key():
    if os.environ.get("TYPESAFE_API_KEY"):
        return os.environ["TYPESAFE_API_KEY"]
    env_path = Path.home() / ".claude" / "typesafe.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("TYPESAFE_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def scan_transcript(path):
    """Single pass: last usage entry, assistant-turn count, tool tally over
    the last ~40 entries, and session elapsed minutes. Never returns text."""
    usage = None
    assistant_turns = 0
    first_ts = last_ts = None
    tail = []
    try:
        with open(path, "r", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = entry.get("timestamp")
                if ts:
                    first_ts = first_ts or ts
                    last_ts = ts
                if entry.get("type") == "assistant":
                    assistant_turns += 1
                    u = (entry.get("message") or {}).get("usage")
                    if u:
                        usage = u
                tail.append(entry)
                if len(tail) > 40:
                    tail.pop(0)
    except OSError:
        return None

    tool_tally = {}
    for entry in tail:
        if entry.get("type") != "assistant":
            continue
        for block in (entry.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name", "?")
                tool_tally[name] = tool_tally.get(name, 0) + 1

    minutes_elapsed = None
    if first_ts and last_ts:
        try:
            from datetime import datetime
            fmt = "%Y-%m-%dT%H:%M:%S.%f%z" if "." in first_ts else "%Y-%m-%dT%H:%M:%S%z"
            t0 = datetime.strptime(first_ts.replace("Z", "+0000"), fmt)
            t1 = datetime.strptime(last_ts.replace("Z", "+0000"), fmt)
            minutes_elapsed = round((t1 - t0).total_seconds() / 60, 1)
        except ValueError:
            minutes_elapsed = None

    return {
        "usage": usage or {},
        "assistant_turns": assistant_turns,
        "tool_tally": tool_tally,
        "minutes_elapsed": minutes_elapsed,
    }


def threshold_for_pct(pct):
    """Stricter far from budget, more lenient approaching/over it -- mirrors
    compact-adviser's dynamic threshold, inverted for our direction."""
    if pct >= 100:
        return 0.45
    if pct <= 75:
        return 0.85
    return 0.85 - (pct - 75) / 25 * 0.35


def call_jev(api_key, state):
    body = {
        "state": state,
        "model": "jev-latest",
        "questions": {
            "good_breakpoint": {
                "type": "noul",
                "instructions": (
                    "A coding agent session is judged for whether now is a good moment to "
                    "suggest the user hand off to a fresh session (write durable state, then "
                    "restart) instead of continuing this one. `state` holds only derived, "
                    "content-free signals: token usage against budget, recent tool-call mix, "
                    "elapsed time, and booleans for whether the assistant's last message reads "
                    "as wrapping up versus describing unfinished work. Judge true if this looks "
                    "like a natural checkpoint; false if it looks like active mid-task work a "
                    "handoff would interrupt."
                ),
                "criteria": {
                    "true": "Natural checkpoint: last message reads as completion/summary, "
                            "little pending-work language, recent tool calls lean toward "
                            "reading/verifying rather than active editing.",
                    "false": "Mid-task: last message reads as describing next steps or "
                             "unfinished work, and/or recent tool calls show active editing "
                             "(Edit/Write/Bash) in progress.",
                },
            }
        },
    }
    try:
        ctx = None
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            pass
        req = urllib.request.Request(
            TYPESAFE_ENDPOINT,
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read())
        return data.get("answers", {}).get("good_breakpoint", {}).get("noul")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        debug(f"Jev call failed: {e}")
        return None


def main():
    if os.environ.get("CC_HANDOFF_NUDGE") != "1":
        return  # on globally; a project can set CC_HANDOFF_NUDGE=0 to opt out

    raw = sys.stdin.read()
    hook_in = json.loads(raw)
    session_id = hook_in.get("session_id", "unknown")
    transcript_path = hook_in.get("transcript_path")
    last_message = hook_in.get("last_assistant_message") or ""

    if not transcript_path or not os.path.exists(transcript_path):
        debug("no transcript_path yet")
        return

    scan = scan_transcript(transcript_path)
    if not scan:
        return
    usage = scan["usage"]
    tokens = (usage.get("input_tokens", 0) or 0) \
        + (usage.get("cache_read_input_tokens", 0) or 0) \
        + (usage.get("cache_creation_input_tokens", 0) or 0)
    if tokens == 0:
        debug("no usage data yet")
        return

    budget = int(os.environ.get("CC_CTX_BUDGET", "200000"))
    min_tokens = int(os.environ.get("CC_HANDOFF_NUDGE_MIN", "150000"))
    if tokens < min_tokens:
        return
    pct = tokens / budget * 100

    state_dir = Path.home() / ".claude" / "state" / "handoff-nudge"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / f"{session_id}.json"
    cooldown = int(os.environ.get("CC_HANDOFF_NUDGE_COOLDOWN", "900"))
    now = time.time()
    if state_file.exists():
        try:
            last = json.loads(state_file.read_text()).get("last_nudge_ts", 0)
            if now - last < cooldown:
                debug(f"cooldown: {now - last:.0f}s < {cooldown}s")
                return
        except (json.JSONDecodeError, OSError):
            pass

    mentions_next_step = bool(NEXT_STEP_RE.search(last_message))
    mentions_completion = bool(COMPLETION_RE.search(last_message))

    api_key = load_api_key()
    nudge = False
    detail = ""
    if api_key:
        state = {
            "context_pct_of_budget": round(pct, 1),
            "tokens_used": tokens,
            "budget": budget,
            "assistant_turns_this_session": scan["assistant_turns"],
            "minutes_elapsed_this_session": scan["minutes_elapsed"],
            "recent_tool_call_counts": scan["tool_tally"],
            "last_message_mentions_next_step_language": mentions_next_step,
            "last_message_mentions_completion_language": mentions_completion,
            "last_message_length_chars": len(last_message),
        }
        noul = call_jev(api_key, state)
        if noul is not None:
            thresh = threshold_for_pct(pct)
            nudge = noul >= thresh
            detail = f"Jev p={noul:.2f} (need {thresh:.2f})"
            debug(f"pct={pct:.0f} state={state} -> {detail} -> nudge={nudge}")
        else:
            api_key = None  # fall through to heuristic below

    if not api_key:
        nudge = pct >= 100
        detail = "heuristic only -- set TYPESAFE_API_KEY for Jev-scored timing"

    if not nudge:
        return

    state_file.write_text(json.dumps({"last_nudge_ts": now}))
    command = os.environ.get("CC_HANDOFF_NUDGE_COMMAND", "").strip()
    action = f"run {command}" if command else "save your progress and start a fresh session"
    msg = (
        f"Context nudge: {tokens // 1000}k/{budget // 1000}k ({pct:.0f}%). "
        f"{detail}. This looks like a good point to {action} before starting anything new."
    )
    # systemMessage must be top-level: Stop has no hookSpecificOutput, and a nested
    # systemMessage is silently ignored.
    print(json.dumps({"systemMessage": msg}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # fail open -- never break the session over a nudge
        debug(f"unhandled error: {e}")
    sys.exit(0)
