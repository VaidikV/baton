<div align="center">

# baton

**Know when to pass a Claude Code session to a fresh one, before context gets expensive.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)
![Claude Code hook](https://img.shields.io/badge/Claude%20Code-Stop%20hook-C4562F.svg)
![Backend](https://img.shields.io/badge/backend-Jev%20%7C%20Laya%20%7C%20Kev-6B7BD1.svg)

</div>

Long Claude Code sessions get expensive, because everything in context is re-read on every turn. Starting fresh fixes that, but only if you stop at the right moment. Stop mid-task and you lose your place.

baton watches your session. Once it gets big, it asks a decision model whether this looks like a natural stopping point, and nudges you when it does. The backend is your choice: [Jev](https://typesafe.ai) from TypeSafe (hosted) by default, or a self-hosted open-weights alternative like [Laya](https://github.com/NandhaKishorM/laya), Kev, or Von via any Jev-compatible server.

```
🏁 baton: good point to save your progress and start a fresh session.
Context 260k/200k (130%), Jev score 0.81 (need 0.45).
```

<p align="center"><img src="diagram.png" alt="How baton decides when to nudge: local checks, two questions to Jev or your own model, a sliding score bar, and a local fallback rule" width="560"></p>

## Quick start

You need Python 3.8+. No other dependencies.

### Try it in 2 minutes (no signup)

```sh
# 1. Install the hook
mkdir -p ~/.claude/hooks
curl -fsSL https://raw.githubusercontent.com/VaidikV/baton/main/baton.py -o ~/.claude/hooks/baton.py
chmod +x ~/.claude/hooks/baton.py
```

```jsonc
// 2. Register it in ~/.claude/settings.json
// (merge into your existing "hooks" section, don't overwrite the file)
{
  "hooks": {
    "Stop": [
      { "hooks": [{ "type": "command", "command": "~/.claude/hooks/baton.py" }] }
    ]
  }
}
```

```sh
# 3. Verify it
~/.claude/hooks/baton.py --check
```

That's it. Out of the box baton runs in heuristic mode with no network calls: once a session passes your budget, it nudges unless local signals say work is mid-flight (a task in progress, a failing test, background jobs). Past 125% it nudges regardless.

### Add smart timing

Heuristic mode only knows "over budget." For breakpoint-aware nudges, give it a backend.

**Option A: TypeSafe Jev (hosted).** Get a key at [typesafe.ai](https://typesafe.ai), then save it outside any repo:

```sh
echo 'TYPESAFE_API_KEY=your-key-here' > ~/.claude/typesafe.env
chmod 600 ~/.claude/typesafe.env
```

**Option B: Open weights (self-hosted, no key).** Start any Jev-compatible server (for example Laya's `laya-serve`), then point baton at it:

```jsonc
// ~/.claude/settings.json
{
  "env": {
    "BATON_ENDPOINT": "http://localhost:8000/v1/systemone",
    "BATON_MODEL": "laya-typed-decisions"
  },
  "hooks": {
    "Stop": [
      { "hooks": [{ "type": "command", "command": "~/.claude/hooks/baton.py" }] }
    ]
  }
}
```

Any server speaking the `POST /v1/systemone` shape and answering `choice` questions works: Laya (`laya-serve`), Kev, Von, Rizzo Flow, and others. The `laya-typed-decisions` checkpoint is the best pick for this kind of judgment call; the base Laya checkpoints score near chance on typed decisions.

Run `baton.py --check` again after either option to confirm the backend gives a valid answer.

## How it works

1. **Free local checks.** Is the session past 150K tokens, has it been 15 minutes since the last nudge, and is this a reply baton hasn't judged yet? If not, baton exits. Below the floor it reads only the end of the transcript.
2. **One request, two questions.** Is the latest unit of work *finished* (waiting on you counts), and was it *hands-on* work or coordination? The score is `P(finished) × (0.5 + 0.5 × P(hands_on))`. This question design comes from compact-adviser, which measured it against what users actually asked next. compact-adviser feeds it conversation text; baton feeds it derived signals only, and that combination hasn't been measured yet.
3. **A sliding bar.** At 150K the score must reach 0.85. At 200K and beyond, 0.45 is enough. The fuller the context, the easier it is to nudge.

### Privacy

compact-adviser sends redacted conversation text to Jev. baton doesn't. It reads the whole transcript locally and sends only what it derives:

- **Context:** size against budget, compactions so far, turn count, elapsed time.
- **Latest turn:** tool calls by kind (read, edit, shell, delegate, web, MCP), files edited (a count), calls since the last edit, tool errors, and whether tests ran and failed, a commit or push happened, or a PR was opened.
- **Todos:** pending, in-progress, and completed counts.
- **Last reply and prompt:** length, and yes/no flags for completion or next-step language, a stated blocker, and whether the reply ends with a question, offers options, or hands work back to you.
- **Background tasks** still running.

**No message text, code, commands, tool output, file paths, or MCP tool names leave your machine.** To see exactly what would be sent for any session, run `baton.py --show path/to/transcript.jsonl`. Self-host the backend (e.g. Laya) and nothing leaves the machine at all.

## Configuration

Set any of these in the `env` block of your settings file:

| Variable | Default | Description |
| --- | --- | --- |
| `BATON` | `1` | Set to `0` to turn baton off (for example, in one project's `.claude/settings.json`) |
| `BATON_BUDGET` | `200000` | Your working context budget in tokens |
| `BATON_MIN_TOKENS` | `150000` | Skip all checks below this size |
| `BATON_COOLDOWN` | `900` | Seconds between nudges in one session |
| `BATON_COMMAND` | none | Your handoff command (for example `/handoff`), named in the nudge |
| `BATON_ENDPOINT` | TypeSafe Jev | Decision-model endpoint. Point at a local Jev-compatible server (e.g. `laya-serve`) to use an open-weights backend instead |
| `BATON_MODEL` | `jev-latest` | Model name sent to the endpoint (e.g. `laya-typed-decisions`) |
| `BATON_NOTIFY` | `0` | Set to `1` to also get a desktop notification with a sound (macOS, or Linux with `notify-send`). The in-chat nudge is a single dim line that's easy to miss |
| `BATON_DEBUG` | `0` | Set to `1` to print the reasoning to stderr |

## What's a handoff?

baton decides *when*. What you do next is up to you. A good handoff writes the current state, decisions, and next steps to a file, then starts a new session that reads it. `/compact` or a quick handwritten note also works.

## Troubleshooting

- **Nudges say "heuristic, backend unavailable" with a key set.** Check the key, then run `baton.py --check`: it probes the backend and shows which TLS certificates are in use. baton uses `certifi` if installed, then Python's own certificates, then the OS bundle (such as macOS's `/etc/ssl/cert.pem`, which covers python.org builds that were never given certificates). If none is found, run `pip3 install certifi`. `BATON_DEBUG=1` shows the exact error. After a failure baton backs off (up to 5 minutes) before asking again.
- **Nudges say "heuristic, backend unavailable" with a local backend.** The server isn't reachable at `BATON_ENDPOINT`, or doesn't answer `choice` questions. Check it's running, the URL ends at the `/v1/systemone` route, and `baton.py --check` passes.
- **No nudge ever shows up.** The session may still be under `BATON_MIN_TOKENS`. Without a reachable backend, baton only warns once you're over budget.
- baton fails quietly by design. Any error exits without output, so it never breaks your session.

## Development

```sh
python3 -m unittest discover -s tests   # stdlib only; uses a local mock backend
```

## Credits

Inspired by [compact-adviser](https://github.com/kunchenguid/compact-adviser) by kunchenguid, which uses Jev to time `/compact`. baton's two-question design and score composition are adapted from it.

## License

[MIT](LICENSE)
