# Server limits

Measured with `experiments/probe_bot` on 2026-11-03 unless noted.

## Scoring and maps

- The team's score is its best single server game played to the end, and practice games count. A kick or crash voids that game.
- **Each game re-rolls the map.** Every seed seen was different, and the biome changes within one upload. So there's no fixed map to solve.
- All 22 seeds seen are version 4 UUIDs. The bot never sees the seed during a game.
- Server games replay identically on the local engine, for frame position, score and dinosaurs.

## Environment

| Item | Value |
|---|---|
| System | Linux x86_64, glibc 2.41, Python 3.13 |
| CPU | Xeon Platinum 8259CL at 2.5 GHz. 2 vCPU reported |
| Parallelism | **None: plan for one core.** Threads and processes both ran slower |
| Memory | 3.9 GB |
| Python speed | About a third to a half of a desktop |
| Compilers | None, but a prebuilt `.so` loads through `ctypes` |
| Filesystem | `/worker/bot` read-only, `/tmp` writable |
| Upload | 10 MB zip. `*.py`, `requirements.txt` and runtime files, with no build output, venv or secrets |

Cross-compile with Zig for `x86_64-linux-gnu.2.31`, which `tools/build_sim.py --linux` does.

## Timing

- **Tick limit: 150 ms.** A stall of 145 ms was on time and 170 ms was late.
- **The bot uses 95 ms** for search, set by `TIME_BUDGET_MS` in `bots/search_bot/bot.py`, less any delay in a tick's arrival.
- Spikes hit even a trivial bot on the server: 8-14 ticks per game took 20 ms or more, and the worst took 49-65 ms. We haven't measured spikes on a local machine.
- With 95 ms the slowest replies reached about 150 ms. On 47 games with the late-tick fix, 2 ticks were late in all.
- With 120 ms, 21-25 ticks per game were late, against the kick limit of 50, because stalls of 50-60 ms during the search pass the limit.
- A late reply only loses that tick's action. 50 late ticks gets the bot kicked.
- Ticks 1-10 get an extra 1,000 ms.
- `sim_live` budgets in simulated ticks, not time, so its tests never include late ticks.

The 95 ms leaves room for one stall, so don't raise it much. Going to 120 ms added about 25% more search, but it lost about 2.3% of moves and didn't raise scores in 3 games.

## Startup

- A connection must send REGISTER within 5 s, or the server closes it and the game is lost.
- `application.py` connects, builds `Bot()`, and only then registers, so `Bot.__init__` must stay fast.
- `bot.py` is imported before connecting, so slow setup belongs at module level. 19-22 s of startup worked. The bound is the 120 s game-start wait, less about 4 s of container boot.
- Don't use `multiprocessing` at import time, because workers re-import `bot.py`.

## Etiquette

Run one server command at a time, never poll or loop, and space games out. Three games about 80 s apart caused no trouble. Rate limits are unknown.
