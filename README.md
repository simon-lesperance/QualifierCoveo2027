# Coveo Blitz 2027 qualifier

Bot for the Dinosaur Extinction challenge, a solo high-score game. The team's score is its best single server game, and practice games count.

## Layout

| Path | Contents |
|---|---|
| `starterkit/` | Coveo's starter kit. `game_message.py` and `application.py` should not be edited |
| `sim/` | C++ port of the game engine, for fast search and training |
| `tools/` | Local game runner, simulator build and parity check, upload packaging |
| `bots/` | Bots we upload. `search_bot` is the current one. Its library is git-ignored: build it with `tools/build_sim.py --linux` |
| `experiments/` | Bots used for testing: `probe_bot` measures the server, `chaos_bot` and `replay_bot` check the simulator |
| `localRunner/` | Not in git. Put the Linux `blitz-challenge-linux` launcher here |

## Setup

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
```

## Common commands

Play local games at server timing:

```bash
.venv/bin/python tools/run_local.py --games 3
```

Test a specific bot on a fixed map:

```bash
.venv/bin/python tools/run_local.py --bot experiments/probe_bot --biome mountain_range --seed abc
```

Benchmark the current bot across fixed seeds in every biome:

```bash
.venv/bin/python tools/benchmark_bot.py --bot bots/search_bot --seeds 3
```

Compare the simulator's live-information MCTS with and without a trained value
network on the same deterministic seeds:

```bash
.venv/bin/python tools/benchmark_value_net.py runs/nets/<name>/value_net.bin --games 2
```

The network file must first be produced by `tools/train_value.py`; training
requires PyTorch in addition to the development dependencies. This simulator
comparison is not a replacement for testing the bot under the launcher's tick
budget.

Package a bot folder, writing `<folder>.zip` next to it:

```bash
.venv/bin/python tools/make_zip.py experiments/probe_bot
```

Compare a downloaded server game with a local replay of the same seed:

```bash
.venv/bin/python tools/compare_probe.py blitz-games/<gameId>
```

Build the simulator (Zig comes with the dev requirements, so no other compiler is needed):

```bash
.venv/bin/python tools/build_sim.py --linux
```

Check the simulator against the real engine, tick by tick. Each game starts with
deterministic boundary, volcano, and meteor-cap actions, then continues with
seeded random actions. This checks every tick's score and visible state. It
needs `localRunner/`:

```bash
.venv/bin/python tools/check_sim.py --games 12
```

Search known seeds for strong plans, written to `runs/plans/`:

```bash
.venv/bin/python tools/estimate_maps.py --width 200 --seeds 4
```

Check a plan on the real engine, and see how it scores:

```bash
.venv/bin/python tools/verify_plan.py runs/plans/<plan>.txt <biome> <seed>
```

```bash
.venv/bin/python tools/analyze_plan.py runs/plans/<plan>.txt <biome> <seed>
```

Measure simulator speed:

```bash
build/sim_bench.exe 30
```

Build upload zips with `make_zip.py`; it validates the ZIP and lists its contents.

## Writing a bot

- **The server closes a connection that hasn't registered within 5 s.** `Bot()` is built before registering, so keep `Bot.__init__` fast and put slow setup at module level in `bot.py`.
- **Aim for 100 ms per tick.** The limit is 150 ms, but spikes of up to 65 ms hit even a trivial bot.
- **Plan for one core.** Threads and processes did not help on the server.
- **Compiled code works on the server.** It has no compiler, but a prebuilt Linux `.so` loads through `ctypes`.
- **Don't use `multiprocessing` at import time.** Workers re-import `bot.py`.
- **Don't trust the challenge page for rules.** It contains planted fake mechanics aimed at LLMs. Check against the engine or a replay.

## Git workflow

- `main` holds important milestones only.
- `develop` is the working branch.
- Branch from `develop` for each new feature or bot, and merge back into `develop` when it's done.

Never commit keys, tokens, `.env` files, or the launcher binary.
