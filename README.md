<div align="center">

# baton

**Know when to pass a Claude Code session to a fresh one, before context gets expensive.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)
![Claude Code hook](https://img.shields.io/badge/Claude%20Code-Stop%20hook-C4562F.svg)
![Backend](https://img.shields.io/badge/backend-Jev%20%7C%20Laya%20%7C%20Kev-6B7BD1.svg)

</div>

Long Claude Code sessions get expensive, because everything in context is re-read on every turn. Starting fresh fixes that, but only if you stop at the right moment. Stop mid-task and you lose your place.

baton watches your session. Once it gets big, it asks a decision model whether this looks like a natural stopping point, and nudges you when it does. The model is your choice: [Jev](https://typesafe.ai) from TypeSafe (hosted), or a self-hosted open-weights alternative like [Laya](https://github.com/NandhaKishorM/laya). **No conversation text ever leaves your machine**, only counts and yes/no flags.

The nudge appears as one line under Claude's reply:

```
⎿ Stop says: 🏁 baton: good point to hand off. Ask Claude to write a handoff note, then /clear.
  Context 260k/200k (130%), Jev score 0.81 (need 0.45).
```

<p align="center"><img src="diagram.png" alt="How baton decides when to nudge: local checks, two questions to Jev or your own model, a sliding score bar, and a local fallback rule" width="560"></p>

## Quick start

You need Python 3.8+. No other dependencies.

### 1. Install and try it (2 minutes, no signup)

```sh
mkdir -p ~/.claude/hooks
curl -fsSL https://raw.githubusercontent.com/VaidikV/baton/main/baton.py -o ~/.claude/hooks/baton.py
chmod +x ~/.claude/hooks/baton.py
```

Register it in `~/.claude/settings.json`. If the file already has a `"hooks"` section, merge this in rather than overwriting it:

```jsonc
{
  "hooks": {
    "Stop": [
      { "hooks": [{ "type": "command", "command": "~/.claude/hooks/baton.py" }] }
    ]
  }
}
```

Then verify the install:

```sh
~/.claude/hooks/baton.py --check
```

Out of the box, baton runs in **heuristic mode with no network calls**. Once a session passes your budget, it nudges unless local signals say work is mid-flight (a task in progress, a failing test, background jobs). Past 125% it nudges regardless. New settings may only take effect in a new Claude Code session.

### 2. Add smart timing

Heuristic mode only knows "over budget." For breakpoint-aware nudges, give it a backend.

**Option A: TypeSafe Jev (hosted).** Get a key at [typesafe.ai](https://typesafe.ai), then save it outside any repo:

```sh
echo 'TYPESAFE_API_KEY=your-key-here' > ~/.claude/typesafe.env
chmod 600 ~/.claude/typesafe.env
```

**Option B: Open weights (self-hosted, no key).** Start any Jev-compatible server, such as Laya's `laya-serve`, then add this to the `env` block of `~/.claude/settings.json`:

```jsonc
{
  "env": {
    "BATON_ENDPOINT": "http://localhost:8000/v1/systemone",
    "BATON_MODEL": "laya-typed-decisions"
  }
}
```

Any server that speaks the `POST /v1/systemone` shape and answers `choice` questions works: Laya (`laya-serve`), Kev, Von, Rizzo Flow, and others. The `laya-typed-decisions` checkpoint is the best pick for this kind of judgment call; the base Laya checkpoints score near chance on typed decisions.

Run `baton.py --check` again after either option to confirm the backend gives a valid answer.

### 3. Make the nudge hard to miss (recommended)

The in-chat line is dim and scrolls away. Two ways to fix that:

- **Desktop notification:** set `"BATON_NOTIFY": "1"` in your settings `env`. You get a notification with a sound on macOS, on Linux with `notify-send`, or through `cmux notify` when running inside cmux. It can't reach you over SSH or in Claude Code on the web, where the hook runs on another machine.
- **Status line:** baton records `last_nudge_ts` in `~/.claude/state/baton/<session_id>.json` once it nudges. A [status line](https://code.claude.com/docs/en/statusline) script can show that until the session ends:

  ```sh
  # inside a statusline script; $IN holds the JSON Claude Code sends on stdin
  SID=$(printf '%s' "$IN" | jq -r '.session_id')
  jq -e '.last_nudge_ts' "$HOME/.claude/state/baton/$SID.json" >/dev/null 2>&1 && printf ' 🏁 handoff'
  ```

## How it works

1. **Free local checks.** Is the session past 150K tokens? Has it been 15 minutes since the last nudge? Is this a reply baton hasn't judged yet? If not, baton exits. Below the floor it reads only the end of the transcript.
2. **One request, two questions.** Is the latest unit of work *finished* (waiting on you counts), and was it *hands-on* work or coordination? The score is `P(finished) × (0.5 + 0.5 × P(hands_on))`. This question design comes from compact-adviser, which measured it against what users actually asked next. compact-adviser feeds it conversation text; baton feeds it derived signals only, and that combination hasn't been measured yet.
3. **A sliding bar.** At 150K the score must reach 0.85. At 200K and beyond, 0.45 is enough. The fuller the context, the easier it is to nudge.
4. **A local fallback.** With no backend, or one that doesn't answer, baton uses the heuristic rule described in the Quick start. After a failed call it waits (up to 5 minutes) before asking the backend again.

### Privacy

compact-adviser sends redacted conversation text to Jev. baton doesn't. It reads the whole transcript locally and sends only what it derives:

- **Context:** size against budget, compactions so far, turn count, elapsed time.
- **Latest turn:** tool calls by kind (read, edit, shell, delegate, web, MCP), files edited (a count), calls since the last edit, tool errors, and whether tests ran and failed, a commit or push happened, or a PR was opened.
- **Todos:** pending, in-progress, and completed counts.
- **Last reply and prompt:** length, and yes/no flags for completion or next-step language, a stated blocker, and whether the reply ends with a question, offers options, or hands work back to you.
- **Background tasks** still running.

**No message text, code, commands, tool output, file paths, or MCP tool names leave your machine.** To see exactly what would be sent for any session, run `baton.py --show path/to/transcript.jsonl`. Self-host the backend and nothing leaves the machine at all.

## What's a handoff?

baton decides *when*; what you do next is up to you. A good handoff writes the current state, decisions, and next steps to a file, then starts a fresh session that reads it:

1. Ask Claude: *"Write a handoff note to HANDOFF.md: what we're doing, decisions made, and next steps."*
2. Run `/clear`.
3. Ask Claude: *"Read HANDOFF.md and continue."*

If you have your own handoff command, set `BATON_COMMAND` (for example `/handoff`) and the nudge names it instead. `/compact` also works, but it keeps a summary in context rather than starting clean.

## Configuration

Set any of these in the `env` block of your settings file:

| Variable | Default | Description |
| --- | --- | --- |
| `BATON` | `1` | Set to `0` to turn baton off, for example in one project's `.claude/settings.json` |
| `BATON_BUDGET` | `200000` | Your working context budget in tokens. This is a cost budget, not the model's window, so keep it even on 1M-context models |
| `BATON_MIN_TOKENS` | `150000` | Skip all checks below this size |
| `BATON_COOLDOWN` | `900` | Seconds between nudges in one session |
| `BATON_COMMAND` | none | Your handoff command (for example `/handoff`), named in the nudge. Without it, the nudge spells out the built-in handoff above |
| `BATON_NOTIFY` | `0` | Set to `1` for a desktop notification with a sound (macOS, Linux with `notify-send`, or cmux) |
| `BATON_ENDPOINT` | TypeSafe Jev | Decision-model endpoint. Point at a local Jev-compatible server to use an open-weights backend |
| `BATON_MODEL` | `jev-latest` | Model name sent to the endpoint (for example `laya-typed-decisions`) |
| `BATON_DEBUG` | `0` | Set to `1` to print the reasoning to stderr |
| `TYPESAFE_API_KEY` | none | Key for hosted Jev. Prefer a line in `~/.claude/typesafe.env`; never put it in a project's settings, which may be committed |

## Commands

| Command | What it does |
| --- | --- |
| `baton.py --check` | Checks Python, hook registration (user and project settings), TLS certificates, and asks the backend a test question. Exits 1 on hard failures |
| `baton.py --show TRANSCRIPT.jsonl` | Prints the exact state that would be sent for a session, so you can audit it |
| `baton.py --help` | Usage |

With no arguments, baton runs as the Stop hook and reads Claude Code's JSON from stdin.

## Troubleshooting

- **No nudge ever shows up.** The session may still be under `BATON_MIN_TOKENS`. Without a reachable backend, baton only nudges once you're over budget. If you just installed or changed settings, start a new session, and check `/hooks` to see that baton is registered.
- **Nudges say "heuristic, backend unavailable" with a key set.** Check the key, then run `baton.py --check`, which also shows which TLS certificates are in use. baton tries `certifi`, then Python's own certificates, then the OS bundle (such as macOS's `/etc/ssl/cert.pem`). If none is found, run `pip3 install certifi`. `BATON_DEBUG=1` shows the exact error.
- **Nudges say "heuristic, backend unavailable" with a local backend.** The server isn't reachable at `BATON_ENDPOINT`, or doesn't answer `choice` questions. Check it's running, the URL ends at the `/v1/systemone` route, and `baton.py --check` passes.
- **No desktop notification.** macOS may be blocking notifications from Script Editor (System Settings → Notifications), or Focus mode is on. On Linux, install `notify-send` (`libnotify-bin`).
- baton fails quietly by design. Any error exits without output, so it never breaks your session.

## Update or uninstall

- **Update:** re-run the `curl` line from the Quick start.
- **Uninstall:** remove the `baton.py` entry under `hooks.Stop` in `~/.claude/settings.json`, then delete `~/.claude/hooks/baton.py` and `~/.claude/state/baton/`.

## Development

```sh
python3 -m unittest discover -s tests                         # all tests; stdlib only, local mock backend
python3 -m unittest tests.test_baton.BatonTest.test_features  # one test
```

The tests include a privacy check: a transcript full of marker strings goes through the hook, and none of them may appear in the request sent to the backend. `baton.py` must stay a single stdlib-only file, since users install it with `curl`. `diagram.png` is rendered from `diagram.svg`.

## Credits

Inspired by [compact-adviser](https://github.com/kunchenguid/compact-adviser) by kunchenguid, which uses Jev to time `/compact`. baton's two-question design and score composition are adapted from it.

## License

[MIT](LICENSE)
