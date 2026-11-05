# Maximum score per biome

> **Lab notes, kept as history.** They were written as the work happened, so earlier sections can be wrong or out of date, and later sections correct them. For current conclusions, see [results.md](../results.md) and [status.md](../status.md).

Run on 2026-11-03 with a beam search that knows the seed. It explores real game outcomes in the C++ simulator, ranking states by the score after in-flight meteors land.

> **What these numbers are:** they are **scores the search found**, so they are lower bounds on each map's true maximum. They are not what a live bot will get: the search sees future spawns, rows and tie-breaks.

## Results

| Biome | Beam 200, mean of 4 seeds | Beam 200, no spawn-tick kills | Beam 1000, one seed | Beam 1000, no spawn-tick kills |
|---|---|---|---|---|
| `deep_crevasse` | **119,320** | **102,256** | **136,675** | **116,272** |
| `volcanic_plains` | 107,642 | 94,572 | 128,315 | 108,401 |
| `mountain_range` | 103,204 | 91,284 | 118,654 | 101,573 |

- Best single plan: **137,186** (`deep_crevasse`, seed est-2, beam 200).
- Seed-to-seed spread at beam 200: 4,000-12,000. The 137k game is an outlier.
- Compare: starter bot about 2,900; rank 3 is 81,238; rank 1 is 204,657.

## How far is the true maximum?

**We haven't reached it.** A 5x wider beam adds about 20% (12-22% across runs), with no sign of levelling off. And a live team reached 204,657, so at least one map allows far more than we found.

My rough guess is that the true maximum is above 200,000 on every biome, but that is an extrapolation.

## Which biome to target

**`deep_crevasse`, with moderate confidence.** It led in 3 of 4 seeds both with and without spawn-tick kills, by about 8-16% on average. Four seeds is few, and it had the largest spread.

It reverses the simple bots' order: the starter did best on `mountain_range`. Why is unknown; it completes more waves (54-60 against 50-53).

The server seems to pick biomes at random, so `deep_crevasse` should come up about a third of the time. Nine games gave 3.

## How the plans score

Taken from the three beam-1000 plans:

| Measure | Value |
|---|---|
| Waves | 53-60, never waiting for the 100-tick timer |
| Wave clear time | Median 16-19 ticks |
| Age at death | Median 7-9 ticks |
| Alive at once | Mean 3.6 |
| Meteors used | About 550 of about 600 possible |
| Kills in one tick | At most 5. Ticks with 2-3 kills give 56-63% of the points |
| Spawn-tick kills | 134-167 of 535-602 |
| Lava kills | 38-78; volcanoes triggered 12-21 times |

- **Tempo wins.** The plans clear a wave about every 18 ticks. Each wave starts 10 fresh dinosaurs at full value.
- **Simple scoring beat hand-made rewards.** Ranking by actual score alone gave 83,359 at beam 40. Rewarding clusters gave 13,000-79,000, and penalising living dinosaurs did no better.
- **Volcanoes help.** At beam 40 on one seed, 83,359 with them and 66,438 without.
- **Spawn-tick strikes are worth about 12-14%.** That is the fall when they are ruled out. It is less than their 20-28% share of kills, because the search finds other kills instead.

## What this doesn't explain

Our plans average about 225 points per kill, and never kill more than 5 in a tick. Rank 1 reached 204,657 **without** foresight, so it must be doing something this search misses, most likely much larger multi-kills. A 4-tick rollout can't see setups that take longer to pay off.

The next thing to look for is how to build kills of 8 or more. Ideas in [Approaches](approaches.md) such as confining a wave with lava remain untested.

## Checks

- All 3 beam-1000 plans and 1 beam-200 plan replayed **identically on the real engine**, on every tick, and scored the same. The largest had 602 kills. This is the first parity test on intense play.
- Search results are deterministic across runs and thread counts.
- Spawn-tick-free runs use a simulator option that does not exist in the real game, so those plans cannot be replayed on the engine.
- The no-spawn-kill figures still benefit from seeing the future, so they are not a live-bot estimate.

## Live bot results

The live bot replans each tick from what the server sends. These are the beam search results; see below for MCTS.

| Test | `deep_crevasse` | `volcanic_plains` | `mountain_range` |
|---|---|---|---|
| Simulated, 4 games each, 16,000 ticks per move | 50,762 | 42,857 | 40,292 |
| Server, 4 games | Not rolled yet | 42,605, 37,003 | 39,052, 35,171 |

The 37,003 game used the earlier settings (beam 12, radius 3).

The live bot gets about 35 waves, against 50-60 with foresight. [Hidden Information Test](hidden-information.md) traced most of the gap to dinosaur tie-breaks.

### MCTS

Open-loop MCTS re-guesses the tie-breaks on every iteration.

| Test | Overall | `deep_crevasse` | `volcanic_plains` | `mountain_range` |
|---|---|---|---|---|
| Beam 30, 24 fresh games | 44,267 | 48,659 | 43,310 | 40,831 |
| MCTS depth 5, 24 fresh games | 46,875 | 51,855 | 46,898 | 41,872 |
| MCTS depth 6 | 47,530 | 51,764 | 47,915 | 42,910 |
| MCTS depth 4 | 47,666 | 51,075 | 47,459 | 44,465 |

Depths 4-6 are a tie. Depths of 12 or more got worse. Exploration barely mattered.

Server games with MCTS (v6): 45,610 on `deep_crevasse`; 45,471 and 42,819 on `volcanic_plains`. MCTS manages about 11,000 simulated ticks per move on the server, less than the 16,000 used in these tests, so its overhead is worth reducing.

## Reproduce

Run from `Code/`:

```bash
.venv/Scripts/python.exe tools/estimate_maps.py --width 200 --seeds 4
```

```bash
.venv/Scripts/python.exe tools/estimate_maps.py --width 200 --seeds 4 --shield
```

Check a plan on the real engine, then break down how it scored:

```bash
.venv/Scripts/python.exe tools/verify_plan.py runs/plans/deep_crevasse-est-0-w1000.txt deep_crevasse est-0
```

```bash
.venv/Scripts/python.exe tools/analyze_plan.py runs/plans/deep_crevasse-est-0-w1000.txt deep_crevasse est-0
```

## Ceiling check (2026-11-04)

The foresight beam search now reaches **229,399**, above rank 1's 204,657. Code is `sim/search_main.cpp` on `feature/ceiling-check`. All of this uses full knowledge of the seed, so a live bot can't match it.

Replaying the 178,028, 207,700 and 229,399 plans on the real engine matched on every tick.

Width 200, 6 maps (3 seeds each of `deep_crevasse` and `volcanic_plains`), `--cluster 0 --spread 0`:

| Ranking and targets | Mean | Best | Waves |
|---|---|---|---|
| Score alone (old default) | 114,210 | 127,403 | 54 |
| + pocket value, weight 1 | 115,311 | 128,450 | 54 |
| + flood value, weight 1 | 119,090 | 132,393 | 55 |
| + gen2 value net, weight 0.3 | 129,288 | 157,401 | 58 |
| + gen2 value net, weight 1 | 136,693 | 146,215 | 58 |
| Every tile a target (`--grid-step 1`) | 137,487 | 161,808 | 59 |
| Every 2nd tile + net 1 | 141,050 | 155,556 | 60 |
| Every tile + net 1 | 158,938 | 174,012 | 64 |
| Every tile + net 2 | 129,075 | 142,592 | 55 |
| Every tile + net 4 | 86,229 | 91,358 | 40 |
| **Every tile + net 1 + flood 1** | **164,770** | **178,028** | **65** |

A sweep on 4 maps gave net 0.5: 157,906, net 0.75: 148,868, net 1.25: 149,928 (all flood 1), and net 1 with flood 2: 159,510. These don't form a pattern, so differences of about 10,000 at width 200 are noise. A beam is chaotic.

Width scaling with every tile, net 1, flood 1:

| Map | 200 | 1000 | 5000 |
|---|---|---|---|
| `deep_crevasse` est-0 | 168,740 | 177,478 | 207,700 |
| `deep_crevasse` est-1 | 143,565 | 162,374 | |
| `volcanic_plains` est-0 | 172,170 | 188,030 | 229,399 |
| `volcanic_plains` est-1 | 152,684 | 174,716 | |

The step from 1000 to 5000 added more than the step from 200 to 1000, so this is still a lower bound and shows no sign of levelling off. Width 5000 took 92-108 minutes per game on 11 threads, about 60 core-seconds per tick, or about 600 times the server's 95 ms, on top of knowing everything.

Average ticks per game by kills in that tick:

| Search | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| Score alone, width 200 | 209 | 91 | 40 | 7.3 | 1 | 0.2 | | |
| Combined, width 200 | 191 | 96 | 47 | 20 | 6.2 | 2.3 | 0.8 | 0.2 |
| Combined, width 1000 | 194 | 85 | 51 | 26 | 8 | 1.8 | 0.8 | 0.2 |
| Combined, width 5000 | 199 | 88 | 46 | 33 | 14 | 7 | | 1 |

Ticks of 4 or more kills go from about 8 to about 30 to about 55. Ticks of 9 or 10 kills never appear.

The 229,399 plan: 78 waves, median gap 14 ticks, never reached the 100-tick timer, 783 kills, 302 of them (39%) on the spawn tick, mean 3.3 alive. The 207,700 plan: 74 waves, 235 of 744 kills on the spawn tick (32%). The 178,028 plan: 68 waves, 203 of 682 (30%).

What it means:

- **Our plans are not capped near 137,000.** That figure came from ranking by score alone and a sparse target grid.
- **200,000 does not need pockets of 10.** These plans get there on tempo plus many 4-6 kill ticks. Rank 1 may play differently, since it has no foresight.
- **The value net helps a lot despite never seeing foresight play.** It is a far better ranking than score alone, which suggests a net trained on these plans could do better still.
- **Spawn-tick kills are 30-39% of kills here**, and they rely on knowing spawn tiles, so the live gap will not close by search width alone.
- **Offering every tile helps at width 200 with foresight, but lost 4,061 live at budget 7000.** The shots are good and the live search can't afford them, which is the case for a small net that picks targets.

Reproduce (about 90 minutes):

```bash
build/sim_search.exe volcanic_plains est-0 5000 plan.txt --threads 11 --cluster 0 --spread 0 --grid-step 1 --net runs/nets/gen2/value_net.bin --net-weight 1 --flood 1
```
