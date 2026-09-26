<div align="center">

# baton

**Know when to pass a Claude Code session to a fresh one, before context gets expensive.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)
![Claude Code hook](https://img.shields.io/badge/Claude%20Code-Stop%20hook-C4562F.svg)
![Powered by Jev](https://img.shields.io/badge/powered%20by-Jev-6B7BD1.svg)

</div>

Long Claude Code sessions get expensive, because everything in context is re-read on every turn. Starting fresh fixes that, but only if you stop at the right moment. Stop mid-task and you lose your place.

baton watches your session. Once it gets big, it asks [Jev](https://typesafe.ai) from TypeSafe whether this looks like a natural stopping point, and nudges you when it does.

```
Context nudge: 260k/200k (130%). Jev p=0.81 (need 0.45). This looks like a good point to save your progress and start a fresh session before starting anything new.
```

<p align="center"><img src="diagram.png" alt="How baton decides when to nudge" width="560"></p>

## Quick start

You need Python 3.8+ and a [TypeSafe](https://typesafe.ai) API key. No other dependencies.

```sh
# 1. Install the hook
mkdir -p ~/.claude/hooks
curl -fsSL https://raw.githubusercontent.com/VaidikV/baton/main/baton.py -o ~/.claude/hooks/baton.py
chmod +x ~/.claude/hooks/baton.py

# 2. Save your key outside any repo
echo 'TYPESAFE_API_KEY=your-key-here' > ~/.claude/typesafe.env
chmod 600 ~/.claude/typesafe.env
```

```jsonc
// 3. Register it in ~/.claude/settings.json
{
  "hooks": {
    "Stop": [
      { "hooks": [{ "type": "command", "command": "~/.claude/hooks/baton.py" }] }
    ]
  }
}
```

That's it. Once a session is big and at a good breakpoint, you'll see a nudge after the reply.

## How it works

1. **Free local checks.** Is the session past 150K tokens, and has it been 15 minutes since the last nudge? If not, baton exits.
2. **One question to Jev.** Is this a natural checkpoint, or is work still in progress?
3. **A sliding bar.** At 150K, Jev must be 85% sure. At 200K and beyond, 45% is enough. The fuller the context, the easier it is to nudge.

### Privacy

Only numbers and yes/no flags are sent: context size, turn count, elapsed time, recent tool-call counts, and whether the last reply sounds finished. **No message text, code, or file content leaves your machine.**

## Configuration

Set any of these in the `env` block of your settings file:

| Variable | Default | Description |
| --- | --- | --- |
| `BATON` | `1` | Set to `0` to turn baton off (for example, in one project's `.claude/settings.json`) |
| `BATON_BUDGET` | `200000` | Your working context budget in tokens |
| `BATON_MIN_TOKENS` | `150000` | Skip all checks below this size |
| `BATON_COOLDOWN` | `900` | Seconds between nudges in one session |
| `BATON_COMMAND` | none | Your handoff command (for example `/handoff`), named in the nudge |
| `BATON_DEBUG` | `0` | Set to `1` to print the reasoning to stderr |

## What's a handoff?

baton decides *when*. What you do next is up to you. A good handoff writes the current state, decisions, and next steps to a file, then starts a new session that reads it. `/compact` or a quick handwritten note also works.

## Troubleshooting

- **Nudges say "heuristic only" even with a key set.** Python may be missing SSL certificates. Run `pip3 install certifi`, and use `BATON_DEBUG=1` to confirm.
- **No nudge ever shows up.** The session may still be under `BATON_MIN_TOKENS`. Without a key, baton only warns once you're over budget.
- baton fails quietly by design. Any error exits without output, so it never breaks your session.

## Credits

Inspired by [compact-adviser](https://github.com/kunchenguid/compact-adviser) by kunchenguid, which uses Jev to time `/compact`.

## License

[MIT](LICENSE)
