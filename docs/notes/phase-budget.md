# Phase-aware search budget

Branch `rich/phase-budget`. Code: `bots/search_bot/phase_budget.py`, gap recording in
`sim/agent.cpp`, simulator `tools/simulate_phase_budget.py`, freeze emulator
`tools/emulate_freezes.py`, tests `tools/test_phase_budget.py`.

## Why

The fixed 95 ms budget leaves 55 ms of the 150 ms tick limit unused on most ticks, as insurance
against CPU freezes. [freeze-analysis.md](freeze-analysis.md) found the freezes are bursts on a
5 s wall-clock cycle. If the bot can predict them, it can spend the insurance on the ticks where
no freeze can make the reply late.

## What the freezes look like (new findings)

**Freezes are not tied to game events.** In probe game 24273, burst onsets are 4,994-5,012 ms
apart but 72-81 ticks apart. No event (spawn, launch, impact, volcano, shift, kill, idle) is
enriched at burst onset at lags 0-3 ticks: 28 tests, smallest p ≈ 0.05, none survive correction.

**Inside a burst the CPU is gone on a fixed 100 ms wall-clock grid.** In game 24273, 81 of 108
stalls start within -6..0 ms of a grid point and end at +48.5 to +49.3 ms (Rayleigh R = 0.93 at
period 100.0 ms). The grid's phase holds for the whole game. In production games, late replies
fall at the same millisecond modulo 100 too: 28 of 36 games significant, median R = 1.0,
Fisher-combined p ≈ 7e-53. This looks like CFS bandwidth throttling of a competing cgroup.

So a burst is 8 or 9 freezes of about 50 ms, each starting just before a grid point, and the
bursts repeat every 5,000 ms (SD 5.5 ms). Both phases differ per game and are learned in game.

**A freeze only makes a reply late when the reply waits for it.** A freeze in the middle of the
search just delays the deadline check; the search stops when the CPU returns. The reply is late
only when the deadline falls inside a freeze that ends past 150 ms after the server sent the tick.

## Policy

All times are on one monotonic clock (`perf_counter`; the C++ `steady_clock` matches it to
0.005 ms, and the offset is measured at import).

- **Exact freezes.** With a deadline set, the MCTS loop already read the clock every iteration.
  It now also records any jump of 5 ms or more between two reads (up to 16 per decision),
  available through `sim_agent_gaps`. Only freezes of 30 ms or more count. A late tick arrival
  (lag of 30 ms or more) counts as a freeze between reply and arrival.
- **Shared CPU is not a freeze.** On the server the bot often loses 20-60 ms of a decision in
  slices shorter than 5 ms (off-CPU time is wall time minus `time.thread_time()`). That can't make
  a reply late, because the deadline is checked continuously. Off-CPU time the loop didn't see as
  one pause only counts when the decision also overran its deadline by 30 ms or more.
- **The wheel.** A 5,000 ms cycle in 20 ms bins. A freeze marks its bins, padded by the free part
  of a grid period, as stalled; a clean stretch of 60 ms or more marks bins as seen. A bin is hot
  when 25% or more of its observations stalled. Bursts on the server lasted 0.75-1.2 s.
- **The grid.** The circular mean of the end times of freezes of 30 ms or more, modulo 100 ms,
  with the freeze length from the second-largest recent one (clamped to 40-70 ms). It is trusted
  once 3 freezes give R ≥ 0.9.
- **The budget.** Each tick gets the big budget (120 ms), unless a burst is possible near the
  deadline (a hot bin, or a freeze in the last 150 ms) **and** the predicted grid freeze containing
  the deadline would end later than 142 ms after the tick was sent, less 4 ms of reply overhead.
  Then the deadline moves to just before that freeze. If a burst is possible but the grid isn't
  known yet, the budget is 85 ms. For the first 6 s (1.2 cycles) the cap is the base 95 ms.
- **Guard.** Three decisions of 140 ms or more on budgets above 95 turn the extension off for the
  rest of the game.

The arrival-lag correction and the opt-in PSI cut still apply on top, through `min()`.

Settings: `BOT_PHASE_BIG_MS` (0 = off, the default), `BOT_PHASE_CUT_MS` (85),
`BOT_PHASE_TRACE=1` prints one `PHASE` line per tick. The `BUDGET` line gains
`phase=on big= moved= base= cut= stalls= slow= overruns= hot= grid=phase/R/length`.

## Offline results

`tools/simulate_phase_budget.py` replays ticks against synthetic server freezes (bursts, grid,
isolated 20-40 ms stalls) or the freezes recorded in probe game 24273. Search ms per searched
tick, 40 games each:

| Policy | Synthetic | Timeouts/game | Probe replay | Timeouts | No bursts |
|---|---:|---:|---:|---:|---:|
| Fixed 95 | 85.4 | 1.80 | 84.9 | 0.80 | 93.7 |
| Fixed 85 | 76.9 | 0.03 | 76.2 | 0 | 83.8 |
| Phase, big 95 | 85.1 | 0.05 | 84.4 | 0 | 93.7 |
| **Phase, big 120** | **101.6** | 0.10 | **100.6** | 0.30 | **116.0** |
| Oracle, big 120 | 107.3 | 0.07 | 106.8 | 0 | 118.5 |

The simulator is pessimistic for fixed 95: the real baseline had 0 timeouts in 6 games, because
our ~100 ms tick rhythm beat-locks with the 100 ms grid. Big 125 gave 104.5 ms and 0.19
timeouts; 130 gave 107.3 and 0.35. Tuning that mattered: the "recent freeze" window (900 → 150 ms)
and the warmup (2 → 1.2 cycles). Raising the freeze threshold from 15 to 30 ms (after the first
server game) left these numbers unchanged. Requiring two freezes before a bin is hot cost 0.24
timeouts per game, because the second burst of a game then went unpredicted, so one is enough.

**Local emulation.** `tools/emulate_freezes.py` SIGSTOPs the real bot for 50 ms on a 100 ms grid,
8-9 times every 5 s, during a local launcher game. Two games, same maps and freeze phases:

| Run | Score | Late | Median simulations (last 100 ticks) | Max move |
|---|---:|---:|---:|---:|
| Fixed 95, em1 | 46,836 | 0 | 16,029 | 124.3 |
| Phase 120, em1 | 48,472 | 0 | 18,897 | 120.3 |
| Fixed 95, em2 | 61,115 | 0 | 14,505 | 116.2 |
| Phase 120, em2 | 65,041 | 0 | 21,009 | 120.3 |

With phase 120, the grid was learned exactly (R = 1.00, length 50 ms). 67% of ticks got 120 ms,
24% were moved (mean 98 ms), and 9% were warmup at 95 ms. Mean budget was 112.5 ms against 95,
with no reply over 133 ms. Scores here are two games and say nothing about strength.

## Server results

2026-11-06, practice games, one at a time. All builds: server-tuned, simulator PGO profile
retrained on the gap-recording code (1.63% ±0.15% faster locally, identical games). The phase
variant is `bots/search_bot` with `BOT_PHASE_BIG_MS` defaulting to 120 and `BOT_PHASE_TRACE` to 1.
The control is the same archive with the phase budget off (fixed 95 ms).

| Game | Variant | Score | Late | Mean of per-100-tick median simulations | Worst move (ms) | Mean budget | Search CPU per searched tick |
|---|---|---:|---:|---:|---:|---:|---:|
| 24316 | Phase 120, first version | 44,930 | 0 | 7,359 | 132.7 | 105.1 | 90.7 |
| 24318 | Phase 120 | 51,913 | 0 | 9,788 | 128.6 | 112.4 | 100.5 |
| 24323 | Phase 120 | 58,363 | 0 | 9,045 | 126.7 | 112.1 | 100.6 |
| 24324 | Phase 120 | 49,593 | 0 | 9,265 | 129.5 | 112.2 | 99.9 |
| 24320 | Control, fixed 95 | 51,450 | 0 | 7,256 | 143.4 | 95 | - |
| 24321 | Control, fixed 95 | 54,592 | 0 | 6,829 | 142.4 | 95 | - |

Earlier fixed-95 games 24278-24280 and 24291-24293 (older, untuned build) had means of
6,580-7,305 simulations, 0 timeouts and worst moves of 133-146 ms.

- **About a third more search.** The final version averaged 9,366 simulations against 7,043 for
  the two controls (7,041 over all eight fixed-95 games). Search CPU per searched tick was about
  100 ms; at a fixed 95 ms budget it is about 85 ms after reply overhead and lost CPU.
- **No late replies, and fewer near misses.** Phase games never passed 130 ms, while the controls
  reached 142-143 ms. Moving the deadline out of predicted freezes protects the ticks fixed 95 lost
  most time on.
- **The first version moved too often.** Game 24316 had a lot of sliced CPU sharing outside the
  bursts, which it counted as freezes: its wheel became 30-70% hot, and 267 of 490 moved ticks
  fell outside the real bursts. Replaying the trace offline with the final rules gives 23 such
  ticks and a mean budget of 111.6 ms. The final version's wheel stayed at 19% hot in all games.
- Scores are four and two games and say nothing about strength. At the measured slope of about
  2,700 points per doubling of live search, a third more search is worth about 1,100 points.

`bots/search_bot` keeps the phase budget **off by default** (`BOT_PHASE_BIG_MS=0`), so local
games and tests are unchanged. To ship it, set the default to 120 in `bot.py` before packaging.
The server can't take environment variables.

## Reproduction

```bash
python tools/simulate_phase_budget.py --games 40 --big 120 --probe   # needs runs/freeze-analysis
python tools/simulate_phase_budget.py --big 120 --trace runs/phase-server/24318/bot.log
python tools/emulate_freezes.py --seed abc                             # fixed 95, emulated bursts
BOT_PHASE_BIG_MS=120 BOT_PHASE_TRACE=1 python tools/emulate_freezes.py --seed abc --log phase.log
python -m unittest discover -s tools -p "test_phase_budget.py"
```

`PHASE` trace lines hold `start`, `arrived`, `budget`, `end`, `off` and `gaps` per tick, enough to
replay the policy offline against a server game.
