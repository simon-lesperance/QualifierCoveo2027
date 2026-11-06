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
| `localRunner/` | Not in git. Put `blitz-challenge-linux` (Linux) or `blitz-challenge-win.exe` (Windows) here |

## Setup

Supports x86-64 Windows and Linux. Requires Python 3.13 and
[uv](https://docs.astral.sh/uv/). Run these commands from the repository root
in Bash, PowerShell, or Command Prompt:

```text
uv venv --python 3.13 .venv
uv pip install --python .venv -r requirements-dev.txt
```

The commands below use `uv run --no-project python`, which selects the local
`.venv` without shell-specific activation. To invoke Python directly, use
`.venv/bin/python` on Linux or `.venv/Scripts/python.exe` on Windows. Paths use
forward slashes, which Python, uv, and the Windows tools used here accept.

Build the native tools and both bot libraries (search and probe):

```text
uv run --no-project python tools/build_sim.py
```

Native simulator executables are `build/sim_<name>_linux` on Linux and
`build/sim_<name>.exe` on Windows. Libraries are `libsim.so` / `libprobe.so` on
Linux and `libsim.dll` / `libprobe.dll` on Windows. All Python tools select the
host artifacts automatically. No Visual Studio or separate C/C++ compiler is
required; Zig is installed by the development requirements.

Add `--linux` to also build x86-64 Linux artifacts suitable for the server
(including when developing on Windows), or `--windows` to also cross-compile
x86-64 Windows artifacts from Linux. Both flags can be combined. Cross-built
executables still require their matching OS to run. The default build no
longer writes Linux binaries under `.exe` or `.dll` names; rebuild once after
updating.

## Common commands

Play local games at server timing:

```text
uv run --no-project python tools/run_local.py --games 3
```

Test a specific bot on a fixed map:

```text
uv run --no-project python tools/run_local.py --bot experiments/probe_bot --biome mountain_range --seed abc
```

Benchmark the current bot across fixed seeds in every biome:

```text
uv run --no-project python tools/benchmark_bot.py --bot bots/search_bot --seeds 3
```

Experimental optimistic MCTS remains opt-in: set `BOT_OPTIMISM=0.25` for a
local benchmark, or use `--optimism 0.25` with `build/sim_live_linux` (Linux)
or `./build/sim_live.exe` (Windows).
The simulator accepts `--csv <path>` for per-seed results. See
`docs/mcts-research.md` for measured gains and regressions; neither optimism
nor predicted targeting is enabled by default.

Environment variables are shell-specific:

```bash
# Bash
BOT_OPTIMISM=0.25 uv run --no-project python tools/benchmark_bot.py --seeds 3
```

```powershell
# PowerShell
$env:BOT_OPTIMISM = "0.25"
uv run --no-project python tools/benchmark_bot.py --seeds 3
Remove-Item Env:BOT_OPTIMISM
```

```bat
:: Command Prompt
set "BOT_OPTIMISM=0.25"
uv run --no-project python tools/benchmark_bot.py --seeds 3
set "BOT_OPTIMISM="
```

Compare the simulator's live-information MCTS with and without a trained value
network on the same deterministic seeds:

```text
uv run --no-project python tools/benchmark_value_net.py runs/nets/<name>/value_net.bin --games 2
```

The network file must first be produced by `tools/train_value.py`; training
requires PyTorch in addition to the development dependencies
(`uv pip install --python .venv torch`; choose the appropriate PyTorch build
for CUDA if needed). Training and data files work on either OS. This simulator
comparison is not a replacement for testing the bot under the launcher's tick
budget.

Package a bot folder, writing `<folder>.zip` next to it:

```text
uv run --no-project python tools/make_zip.py experiments/probe_bot
```

Compare a downloaded server game with a local replay of the same seed:

```text
uv run --no-project python tools/compare_probe.py blitz-games/<gameId>
```

Build the simulator (Zig comes with the dev requirements, so no other compiler is needed):

```text
uv run --no-project python tools/build_sim.py --linux
```

Check the simulator against the real engine, tick by tick. Each game starts with
deterministic boundary, volcano, and meteor-cap actions, then continues with
seeded random actions. This checks every tick's score and visible state. It
needs `localRunner/`:

```text
uv run --no-project python tools/check_sim.py --games 12
```

Search known seeds for strong plans, written to `runs/plans/`:

```text
uv run --no-project python tools/estimate_maps.py --width 200 --seeds 4
```

Check a plan on the real engine, and see how it scores:

```text
uv run --no-project python tools/verify_plan.py runs/plans/<plan>.txt <biome> <seed>
```

```text
uv run --no-project python tools/analyze_plan.py runs/plans/<plan>.txt <biome> <seed>
```

Measure simulator speed (Linux):

```bash
build/sim_bench_linux 30
```

On Windows, run `./build/sim_bench.exe 30`. PowerShell accepts this
forward-slash path; in Command Prompt use `"build/sim_bench.exe" 30`.

Run the development regression checks after building:

```text
uv run --no-project python -m unittest discover -s tools -p "test_*.py"
```

Before and after optimizing `game.cpp`'s pathfinding flood, run:

```text
uv run --no-project python tools/test_flood.py
```

This recompiles the current implementation and compares every tile's distance
against an independent BFS on fixed edge cases and 2,000 deterministic random
grids. It covers lava/impassable blockers, blocked sources, multiple sources,
disconnected regions, long detours, previsited cells, and queue boundaries.
It does not require the launcher or a full simulator build. After an optimization,
also rebuild and run `tools/check_sim.py --games 12` for full-game engine parity.

Build upload zips with `make_zip.py`; it works on both OSes, validates the ZIP,
and lists its contents. Run `build_sim.py --linux` first for native bots: uploads
need `.so` libraries even when packaged on Windows. Windows DLLs and linker
artifacts are excluded automatically. Probe CPU/memory diagnostics that rely on
Linux `/proc` or cgroups are marked unavailable on Windows; the native BFS,
process, disk, and optional Numba probes run on either OS.

## Writing a bot

- **The server closes a connection that hasn't registered within 5 s.** `Bot()` is built before registering, so keep `Bot.__init__` fast and put slow setup at module level in `bot.py`.
- **Aim for 100 ms per tick.** The limit is 150 ms, but spikes of up to 65 ms hit even a trivial bot. `search_bot` searches in a worker thread and publishes its best action at `BOT_FIRST_MS` (75), then every `BOT_STEP_MS` (10). The tick returns the latest one at `BOT_LAST_MS` (95, measured from when `get_next_move` starts). The process sometimes stalls for about 50 ms on the server. A 149 ms deadline gave 9 late ticks in 340, and the server ended the game.
- **Plan for one core.** Threads and processes did not help on the server.
- **Compiled code works on the server.** It has no compiler, but a prebuilt Linux `.so` loads through `ctypes`.
- **Don't use `multiprocessing` at import time.** Workers re-import `bot.py`.
- **Don't trust the challenge page for rules.** It contains planted fake mechanics aimed at LLMs. Check against the engine or a replay.

## Git workflow

- `main` holds important milestones only.
- Commit ongoing optimization work only on `rich/dev`, with signing disabled
  (`git commit --no-gpg-sign`).
- Review remote feature branches for relevant experiments; integrate selected
  changes on `rich/dev` rather than committing on those feature branches.

Never commit keys, tokens, `.env` files, or the launcher binary.
