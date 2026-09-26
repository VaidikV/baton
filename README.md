# jev-handoff-nudge

A Claude Code hook that tells you when it's a good moment to save your progress and start a fresh session. It asks [Jev](https://typesafe.ai) from TypeSafe to make the call.

![How it works](diagram.png)

## Why

Long Claude Code sessions get expensive, because everything in context is re-read on every turn. Starting fresh fixes that, but only if you stop at the right moment. Stop in the middle of a task and you lose your place.

A flat token limit doesn't know whether you're mid-edit or just wrapped something up. So once a session gets big, this hook asks Jev one question: does this look like a natural stopping point? The fuller the context, the less sure Jev has to be before you get a nudge.

```
Context nudge: 333k/200k (167%). Jev p=0.63 (need 0.45). This looks like a good point to run /handoff before starting anything new.
```

## What gets sent to Jev

Only numbers and yes/no flags, all worked out on your machine:

- how full the context is, against your budget
- number of turns and minutes in the session
- counts of recent tool calls (for example `Edit: 4, Read: 2`)
- whether the last reply sounds finished or talks about next steps, and its length

No message text, code, or file content leaves your machine.

## Setup

You need Python 3 (standard library only) and a TypeSafe API key.

1. Save your key outside any repo:

   ```sh
   echo 'TYPESAFE_API_KEY=your-key-here' > ~/.claude/typesafe.env
   chmod 600 ~/.claude/typesafe.env
   ```

2. Copy the hook:

   ```sh
   mkdir -p ~/.claude/hooks
   cp handoff-nudge.py ~/.claude/hooks/
   chmod +x ~/.claude/hooks/handoff-nudge.py
   ```

3. Turn it on in `~/.claude/settings.json`:

   ```json
   {
     "env": {
       "CC_HANDOFF_NUDGE": "1"
     },
     "hooks": {
       "Stop": [
         {
           "hooks": [
             { "type": "command", "command": "~/.claude/hooks/handoff-nudge.py" }
           ]
         }
       ]
     }
   }
   ```

To turn it off for one project, set `"CC_HANDOFF_NUDGE": "0"` in that project's `.claude/settings.json`.

## Settings

All optional, set in the `env` block:

| Variable | Default | What it does |
| --- | --- | --- |
| `CC_CTX_BUDGET` | `200000` | Your working context budget in tokens |
| `CC_HANDOFF_NUDGE_MIN` | `150000` | Don't check at all below this many tokens |
| `CC_HANDOFF_NUDGE_COOLDOWN` | `900` | Seconds between nudges in one session |
| `CC_HANDOFF_NUDGE_COMMAND` | none | Your handoff command, for example `/handoff`, shown in the nudge |
| `CC_HANDOFF_NUDGE_DEBUG` | off | Set to `1` to print the reasoning to stderr |

How sure Jev needs to be slides with context size: 85% at 75% of budget, down to 45% at 100% and above.

## What a handoff is

This hook only decides *when*. What you do next is up to you. I use a `/handoff` command that writes the current state, decisions, and next steps to a file, then I start a new session that reads that file. You could also just run `/compact`, or write a quick note by hand.

## Good to know

- Jev is only called once the session passes `CC_HANDOFF_NUDGE_MIN`. After a nudge, it waits out the cooldown before asking again.
- Without a key, it falls back to a plain "over budget" warning.
- If nudges only ever say "heuristic only" even with a key set, Python may be missing SSL certificates. Run with `CC_HANDOFF_NUDGE_DEBUG=1` to check, and `pip3 install certifi` usually fixes it.
- It fails quietly. Any error exits without output, so it never breaks your session.

## Credit

The idea comes from [compact-adviser](https://github.com/kunchenguid/compact-adviser) by kunchenguid, which uses Jev to time `/compact`. This is a Python take on the same approach, aimed at handoffs.

## License

MIT
