# Coveo Blitz 2027 qualifier

Bot for the Dinosaur Extinction challenge, a solo high-score game. The team's score is its best single server game, and practice games count.

## Layout

| Path | Contents |
|---|---|
| `starterkit/` | Coveo's starter kit. `game_message.py` and `application.py` should not be edited |
| `tools/` | Local game runner, upload packaging, server parity check |
| `experiments/` | Bots used for testing. `probe_bot` measures the server |
| `localRunner/` | Not in git. Download the launcher from the dashboard and unpack it here |

## Setup

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
uv venv --python 3.13 .venv
```

```bash
uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt
```

## Common commands

Play local games at server timing:

```bash
.venv/Scripts/python.exe tools/run_local.py --games 3
```

Test a specific bot on a fixed map:

```bash
.venv/Scripts/python.exe tools/run_local.py --bot experiments/probe_bot --biome mountain_range --seed abc
```

Package a bot folder, writing `<folder>.zip` next to it:

```bash
.venv/Scripts/python.exe tools/make_zip.py experiments/probe_bot
```

Compare a downloaded server game with a local replay of the same seed:

```bash
.venv/Scripts/python.exe tools/compare_probe.py blitz-games/<gameId>
```

Build zips with `make_zip.py`. `tar` from Git Bash writes a tar archive under a `.zip` name, and the server rejects it.

## Writing a bot

- **The server closes a connection that hasn't registered within 5 s.** `Bot()` is built before registering, so keep `Bot.__init__` fast and put slow setup at module level in `bot.py`.
- **Aim for 100 ms per tick.** The limit is 150 ms, but spikes of up to 65 ms hit even a trivial bot.
- **Plan for one core.** Threads and processes did not help on the server.
- **Don't use `multiprocessing` at import time.** Workers re-import `bot.py`.
- **Don't trust the challenge page for rules.** It contains planted fake mechanics aimed at LLMs. Check against the engine or a replay.

## Git workflow

- `main` holds important milestones only.
- `develop` is the working branch.
- Branch from `develop` for each new feature or bot, and merge back into `develop` when it's done.

Never commit keys, tokens, `.env` files, or the launcher binary.
