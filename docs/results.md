# Results

What we've learned, with the evidence. Before trying an idea, check the tables below. Full detail is in [notes/](notes/).

## Main findings

1. **Multi-kills and tempo are the game.** A tick's points scale with kills, and clearing the map brings the next wave a tick later. The best plans clear a wave every 14 ticks or so.
2. **Spawn pockets are the biggest single mechanism.** Waves spawn only on tiles connected to the bottom row without crossing lava. If lava leaves a small area, a meteor landing on the wave's first tick can kill all 10 at age 0 for 9,020 points. One game earned 51,731 points in 100 ticks this way. An average pocket is worth 8,000-10,000 per 100 ticks, about twice normal play.
3. **Hidden dice cost more than hidden terrain.** Dinosaur tie-breaks come from an RNG the bot can't see. Revealing them to an older bot added about 19,000, while true future rows added little at that stage. Today's bot gains at most +3,167 ±697 from true rows.
4. **Tuning this search is exhausted at about 55,000.** Depth, widening, horizon, exploration, targets, caching and RAVE have all failed to beat v11 clearly.
5. **More budget alone won't close the gap.** See [the budget tables](#budget-against-score).
6. **Imitating foresight plays far better than imitating MCTS,** and PPO improves quickly from that start, but it levels off near 44,000 alone. As an MCTS prior it adds +2,327 ±336 at the server's capacity, and more 80k+ games ([status.md](status.md)).

## Bot history

| Version | What changed | Server best |
|---|---|---|
| v6 | Open-loop MCTS replaces beam search | 45,610 |
| v8 | gen1 value net | 61,939 |
| **v9** | Search simulates waves at guessed tiles | **85,231** |
| v10 | Volcanoes ranked by flooding | 42,241 |
| v11 | Only the three best-flooding volcanoes, gen2 net | 67,855 |
| v12 | x86-64-v3 library, late-tick fix | 67,461 |
| v12 + PPO prior (live) | PPO net as a root prior | 77,532 |

Server scores are noisy, and 85,231 was a lucky game. On paired simulated games v11 is the strongest version.

## Simulator speed

Two changes from `rich/dev` make the simulator faster without changing any result: the early-exit safety search (`nearest_safe`) and an RC4 key index without a modulo. `sim/safety_test.cpp` checks the safety search against an independent BFS.

| Check | Result |
|---|---|
| 18 games at the live settings with the PPO net, 4 alternating rounds | 124.5 s to 108.4 s, about 15% more search per second |
| Same seeds, old and new build | Every game identical |
| `tools/check_sim.py`, 12 games | Identical to the engine |
| 360 paired games, budget 7,240 against 6,300 | +123 ±377, with 4 games at 80,000+ against 6 |

The extra search has no measurable effect on the mean, and the best game of 108,598 is one game. The speedups still cost nothing, so they belong in the next upload. Their effect on the server hasn't been measured.

## Budget against score

Foresight search with the gen2 net, flood value and every tile as a target. Six maps up to width 200, fewer beyond that:

| Width | Mean | Best | Waves |
|---|---|---|---|
| 1 | 43,095 | 54,113 | 24 |
| 2 | 61,171 | 71,821 | 33 |
| 3 | 66,254 | 83,597 | 35 |
| 5 | 81,645 | 99,684 | 41 |
| 10 | 98,795 | 114,605 | 47 |
| 20 | 114,243 | 120,372 | 53 |
| 200 | 164,770 | 178,028 | 65 |
| 1,000 (4 maps) | 175,649 | 188,030 | |
| 5,000 (2 maps) | 218,550 | 229,399 | 74-78 |

Width 3 costs about what a live move can afford. Width 5,000 needs about 600 times that, on top of knowing the seed.

Live MCTS, 120 games paired with the 7,000 baseline (mean 54,667, 240 games):

| Budget | Mean | Best | 70k+ | 80k+ | Difference |
|---|---|---|---|---|---|
| 14,000 | 57,863 | 83,962 | 2% | 1% | +2,454 ±723 |
| 28,000 | 61,070 | 84,288 | 9% | 2% | +5,661 ±702 |
| 56,000 | 63,607 | 104,580 | 16% | 5% | +8,198 ±732 |

Each doubling adds about 15,000 with foresight and about 2,700 without. Neither curve has flattened, but long plans only pay off when the dice are known. A faster simulator is therefore worth little by itself. High scores do improve faster than the mean, though, and the leaderboard rewards them.

## Phase-aware time budget

The server's CPU freezes come in bursts every 5,000 ms, on a 100 ms wall-clock grid inside each
burst. They don't depend on game events. Learning both phases during a game and searching 120 ms
except where a predicted freeze would make the reply late gave about a third more simulations
than fixed 95 ms on the server (9,366 against 7,043, three and two games), with no late replies and
no move over 130 ms. Opt-in through `BOT_PHASE_BIG_MS`. See [phase-budget.md](notes/phase-budget.md).

## Build throughput

### Initial Cascade Lake-tuned experiment

Local Ryzen 5 3600 measurements on 2026-11-05, Zig 0.16 / Clang 21, `-O2`,
x86-64-v3 with Cascade Lake tuning. Each candidate trained on three complete
live-information games, one per biome, then played three held-out games twice in
alternating baseline/candidate order. All used budget 7,000, the shipped value
net and the live search settings. Percentages below are changes in elapsed
runtime against an **unprofiled, already tuned v3 baseline**, with standard
error over the two timing pairs; negative is faster.

| Profile | Training seed | Held-out seed | Runtime change |
|---|---|---|---|
| IR, all functions | `live-81000` | `live-91000` | +8.22% +/- 0.39% |
| Front-end, all functions | `live-82000` | `live-92000` | +8.03% +/- 0.03% |
| **IR, excluding `ValueNet`** | `live-83000` | `live-93000` | **-1.65% +/- 0.10%** |

Sampling implicated `ValueNet::evaluate` in the full-profile regression: its share
of cycles rose from about 30% to 38%, while total cycles also increased.
Excluding `ValueNet` records at merge time preserves the ordinary vector-loop
code generation and leaves PGO to the branchy search/simulator. This exclusion
is the default in `tools/build_pgo.py`, which refuses to install a locally slower
candidate. The specific compiler decision causing the regression has not been
isolated.

The final paired runs took 85.760 / 84.255 seconds and 85.645 / 84.315 seconds
(baseline / PGO, three games each). Every per-tick trace matched, not just the
final scores: paired score difference **0 +/- 0 SE** across the three held-out
games. This verifies fixed-work equivalence, not a playing-strength gain. The
small local speedup is below the hoped-for 10-30%; server throughput and scores
have not been measured.

The tuned library was initially installed at `bots/search_bot/libsim.so`. The profile,
isolated builds, traces and timing report are in `build/pgo-final/` (ignored).
Rebuilding with the then-current tuned flags and `--pgo-use
build/pgo-final/sim.profdata` reproduced the same library hash. The artifact has
no instrumentation sections or AVX-512 registers and needs at most glibc 2.29
symbols, within the glibc 2.31 build target. Reproduction instructions and the
Zig tuning caveat are in [architecture.md](architecture.md#linux-pgo-builds).

### Untuned PGO isolation

Removed Cascade Lake scheduling from all C++ build stages, retaining
`-march=x86_64_v3`, `-O2` and `-ffp-contract=off`. Retrained IR profiles on
`live-84000`, one full live-information game per biome. Both selective and
full-library profiles came from the **same raw training data**, and all three
builds played `live-94000` across the three biomes at budget 7,000.

The order was baseline / selective / full, then full / selective / baseline.
The baseline here is unprofiled **and untuned**. Runtime changes are relative to
that baseline; standard errors describe the two repeated timing comparisons,
not uncertainty across a broad population of seeds.

| Build | Round 1, seconds | Round 2, seconds | Runtime change |
|---|---|---|---|
| Untuned, unprofiled | 83.582 | 83.668 | reference |
| **Untuned IR PGO, excluding `ValueNet`** | **81.680** | **81.704** | **-2.31% +/- 0.04% SE** |
| Untuned IR PGO, full library | 89.996 | 90.039 | +7.64% +/- 0.03% SE |

Both PGO variants matched every baseline per-tick trace. Paired score difference
was **0 +/- 0 SE** across the three held-out games. Removing explicit CPU tuning
therefore did **not** eliminate the full-library PGO slowdown on this Ryzen.
It still does not identify which compiler optimization caused it, or establish
whether the server Xeon behaves the same way. The tuned and untuned experiments
used different seeds/profiles, so their percentages do not isolate the effect
of tuning itself.

The installed `bots/search_bot/libsim.so` is now the untuned, selective-PGO
library. All profiles, traces and results are in `build/pgo-untuned/` (ignored).
Rebuilding it with:

```bash
.venv/bin/python tools/build_sim.py --linux --only libsim \
  --pgo-use build/pgo-untuned/sim.profdata
```

reproduced the candidate hash. `tools/build_pgo.py --compare-full` reproduces the
three-way comparison in a fresh output directory with new disjoint seed ranges.
No server game was run.

## Tried and rejected

Live tests use 120 paired games at budget 7,000 against v11 unless noted.

### Search

| Idea | Result |
|---|---|
| Depth 4 / 6 | -640 ±747 / -854 ±660 |
| Depth 7, widening 5, horizon 6 | All lost, on smaller samples |
| Exploration 0.3 / 0.8 | Both lost, on smaller samples |
| Horizon below 4 | Falls from 39,000 to 16,000, because meteors take 4 ticks to land |
| Caching rollouts by dice guess | 16 guesses gave +1,339 ±752, but -80 ±469 on 240 fresh games |
| RAVE | -1,001 to -1,993 |
| Every tile as a target | -4,061 ±735. Good shots, but the search can't afford them |
| More pocket targets | No gain |
| Aiming at predicted positions | Lost, because meteors change where dinosaurs go |
| Several RNG guesses voting | Lost, because it splits the budget |
| Rewarding a cleared map, or fewer living dinosaurs | No change |
| Wave-clear bonus (`rich/dev`) | 48,194 against 48,596 on fresh seeds |
| Better target generation (`rich/dev`) | Fixed-work gain didn't survive the real deadline |
| 120 ms search on the server | 21-25 late ticks per game, and 3 games averaged 43,340 |

### Pockets and volcanoes

| Idea | Result |
|---|---|
| Fixed rules for firing at pockets | Search beat all of them |
| Leaf bonus for a small spawn area | No gain |
| Flood value on guessed rows | -502 ±403 |
| Flood value with true rows | +3,167 ±697, a cap the bot can't reach yet |
| Greedy carving to steer lava | Adds almost nothing. Doesn't rule out planned, multi-step channels |
| Waiting for Triceratops packs to clump | At most 3-4 kills per blast |

### Nets

| Idea | Result |
|---|---|
| gen3, gen4 value nets with spawn-area inputs | Lost about 3,000-4,400, for reasons unknown, despite good R² |
| Value net from foresight rollouts (fg1) | 137,135-149,268 against 164,770 at width 200 |
| Beam ancestry labels | One parent's children fill the beam: about 1 ancestor 20 ticks back |
| Capping children per parent | 156,257-165,381 against 164,770, within noise |
| Imitating MCTS visit counts | 8,777, far weaker than PPO |
| PPO net as an MCTS prior, at about 31,000 strength | No gain |

Why the fg1 value net lost: within one position and tick, both nets correlate only about 0.04 with outcomes. A value net is mainly learning which ticks are rich, which doesn't help choose between moves.

## How to test a change

Build first. Do this while no training is running, because the build replaces `build/libvec.dll`:

```bash
.venv/Scripts/python.exe tools/build_sim.py
```

Then play paired games with the live settings, once for each version, changing only what you're testing. The log folder must exist first:

```bash
mkdir -p runs/tests/my_test
```

```bash
build/sim_live.exe 60 --budget 7000 --mcts 1 --radius 2 --depth 5 --plan-waves 1 --net bots/search_bot/value_net.bin --value-weight 0.3 --volcano-rank 1 --volcanoes 3 --threads 11 --first 5000 --biome volcanic_plains --log runs/tests/my_test
```

`--log` writes one csv per game. The last row's second column is the final score, so match games by file name to get paired differences. Without `--plan-waves 1 --volcano-rank 1 --volcanoes 3` it isn't the live bot.

Reference: this configuration averaged 54,667 on seeds 5000+ over `deep_crevasse` and `volcanic_plains`.

## Foresight search

`sim_search` knows the seed, so it shows what is possible and supplies training data. Width 5,000 takes about 100 minutes on 11 threads, and width 200 about 3 minutes:

```bash
build/sim_search.exe volcanic_plains est-0 200 plan.txt --threads 11 --cluster 0 --spread 0 --grid-step 1 --net bots/search_bot/value_net.bin --net-weight 1 --flood 1
```

Plans replay identically on the real engine, on every tick:

```bash
.venv/Scripts/python.exe tools/verify_plan.py plan.txt volcanic_plains est-0
```

## Noise guide

| Test | Typical noise |
|---|---|
| 120 paired live games | Standard error 600-750 |
| 72 unpaired games | About 1,200 |
| Foresight width 200, six maps | About 10,000. A beam is chaotic |
| One map, live bot | Dice alone: sd 4,000-6,000 |
