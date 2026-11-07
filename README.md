# lesson-engine

Claude Code forgets. This turns what goes wrong in your sessions into a list of lessons that loads into
every future session. It mines past session transcripts for corrections and dead ends, proposes rules
with their evidence, tracks whether each rule is actually being followed, and prunes the ones that stop
earning their place.

The engine holds no lessons. They live wherever you point it, so you can share either one without the
other, and your project repos contain no trace of either.

## Set up

```bash
uv tool install git+https://github.com/aepstein96/lesson-engine

mkdir ~/lessons && cd ~/lessons && git init && touch LESSONS.md   # your store, ideally a private repo
export LESSONS_DIR=~/lessons                                      # or: lesson-engine setup ~/lessons

lesson-engine add ~/code/my-project                               # do this for each repo
```

`add` does three things, all local to your clone, so nothing the repo tracks changes:
- Links the list in as a gitignored `CLAUDE.local.md`, so every session there loads it.
- Installs the commands below.
- Registers the repo for mining, with a session-start reminder when sessions are waiting.

`lesson-engine remove <repo>` undoes it, and `lesson-engine list` shows what's added.

## Use

| In Claude Code | What it does |
|---|---|
| `/lesson` | Review this session and save anything worth keeping to the inbox |
| `/mine-transcripts` | Mine past sessions across all added repos for mistakes nobody wrote down |
| `/harvest-lessons` | The editorial pass: review the inbox, add what clears the bar, prune |

`/mine-transcripts` can run a local model through ollama to cheaply flag the turns worth reading first.

## What's in a store

```
LESSONS.md    the list; every session reads it, as instructions
inbox/        proposed lessons, one file per person; no session reads these
mined/        mining results and the ledger of how each lesson is doing; excerpts of past sessions
```

## Working with others

Keep the list read-only for everyone but its owner. It loads as instructions into everyone's sessions, so
anyone who can write it can steer everyone's agents. Proposals go through the inbox instead, and a person
reviews them.

On a shared machine, give everyone the same store through `LESSONS_DIR` or `/etc/lesson-engine/lessons-dir`,
and run this on a timer from an account that can push to the store's repo:

```bash
lesson-engine sync --checkout <clone of your store> --publish <folder everyone reads>
```

It publishes the reviewed list and collects the inbox into the repo. It never publishes `mined/`, because
that holds excerpts of your sessions.

## License

MIT
