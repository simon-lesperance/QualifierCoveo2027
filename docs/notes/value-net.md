# Value net notes

> **Lab notes, kept as history.** They were written as the work happened, so earlier sections can be wrong or out of date, and later sections correct them. For current conclusions, see [results.md](../results.md) and [status.md](../status.md).

Started 2026-11-03 night. Tests are simulated, charge the net's time, and use fresh seeds unless noted.

## How it works

The net scores the position each MCTS iteration ends in. An iteration's value is the points it earned plus `weight * net`. It predicts points over the next 100 ticks, from 14 map channels and 5 global numbers.

- Network: two stride-2 convolutions and two dense layers, 16,293 parameters.
- Inference: hand-written in `sim/value_net.cpp`, matching PyTorch to about 0.000002. It skips zero inputs, which changes nothing mathematically.
- Speed: about 19 µs locally. On the server, ticks per move fell from about 11,000 to about 6,800.

## Results

| Net | Data | Test | Plain MCTS | With net |
|---|---|---|---|---|
| gen0 | 1,000 games, real positions | `deep_crevasse`, 12 games, weight 1 | 53,516 | 50,340 |
| gen0 | | weight 2 | 53,516 | 40,398 |
| gen1 | 2,000 games, leaf positions, 10% forced waits | Tuning seeds, weight 0.5 | 53,516 | 54,825 |
| gen1 | | **36 fresh games, weight 0.3** | **46,708** | **49,200** |
| gen1 | | 36 fresh games, weight 0.5 | 46,708 | 48,762 |
| gen1 | | 36 fresh games, other seeds, weight 0.3 | 45,836 | 49,255 |
| gen2 | gen1 data + 3,000 net-guided games, all biomes | Same 36 games, weight 0.3 | 45,836 | 49,466 |

gen2's R² rose to 0.245, but it plays no better than gen1, so it wasn't shipped.

gen1 was trained only on `deep_crevasse` but also helped `volcanic_plains` (about 10%). `mountain_range` didn't change.

Server games with gen1 (build v8): 61,939 on `volcanic_plains`; 40,024, 38,241 and 34,589 on `mountain_range`.

## What went wrong first

The first net made the bot worse, for two reasons found by checking the data:

- **It was judging unfamiliar positions.** None of 9,000 validation positions lacked meteors in flight, because the bot launches almost every chance it gets. But MCTS evaluates positions after rollouts, when meteors have landed. Training on positions shaped like MCTS leaves fixed this.
- **Noise beats small differences.** The net is well calibrated, so a kill worth 140 now also lowers predicted future points by about 100, as it should. That leaves a difference of about 40, smaller than the net's noise. This is why a low weight works and a high weight fails.

R² is only 0.13-0.16, and a larger net (61,641 parameters) didn't improve it. Most of the next 100 ticks' scoring is random.

## Ideas tested and rejected

| Idea | Result |
|---|---|
| Rewarding a cleared map or fewer living dinosaurs | No change |
| Launch penalty, so it fires less | No change (52,595-53,781) |
| Aiming at predicted positions | Worse (50,790 against 53,516): meteors change where dinosaurs go |
| Choosing volcanoes near dinosaurs | No gain |
| Confining waves with lava | Spawn area stays large; one volcano can't wall off enough |
| Waiting for Triceratops packs to clump | At most 3-4 kills per blast |
| Compiler flags, `-O3`, `-march=haswell` | No change |
| GCC instead of Zig | Zig about 3-4% faster |

## Search budget

`deep_crevasse`, 12 games, plain MCTS:

| Ticks per move | Depth | Mean |
|---|---|---|
| 3,000 | 5 | 44,052 |
| 11,000 | 5 | 53,516 |
| 30,000 | 5 | 55,179 |
| 100,000 | 5 | 57,565 |
| 100,000 | 8 | 59,607 |

Plain search saturates near 60,000, so faster code alone won't close the gap to rank 1.

## Chained meteors

Dinosaurs flee toward the latest impact before they consider distance to safety, so overlapping meteors on consecutive ticks can funnel them. Stegosaurus also converge: their spread falls from 13 to about 3 by tick 40.

A brute-force test of target triples on wave 1 found 5 kills in one tick, 1,035 points, after waiting 35 ticks. That is 345 points per meteor, close to rank 1's rate of 341. Kills stayed capped around 5, and the wait costs tempo.

Rank 1's score doesn't need huge blasts: 3-4 kills per blast on nearly every wave, at our tempo, would be about 4,300 points per wave over 50 waves. Coordinated salvos are the most promising lead.

The experiment tools (`sim_patience`, `sim_triple`, aiming options) are on the `feature/better-targets` branch, not merged.

## Foresight value net (2026-11-05)

Could a value net taught by foresight search beat gen2? Reference: gen2 at weight 1 with flood value, width 200, six held-out maps, mean 164,770.

**fg1.** 55 games at width 50, about 49,000 positions. Candidates were sampled from the beam, half from those it kept and half from all of them. A width-10 foresight beam then played 100 ticks from each, and the points it scored became the label. R² 0.129, overfitting after a few epochs.

| Net | Width-200 mean | Best |
|---|---|---|
| gen2, weight 1 | **164,770** | 178,028 |
| fg1, weight 1 | 137,135 | 153,337 |
| fg1, weight 0.5 | 149,268 | 170,671 |

Why it lost:

- gen2 predicts these labels as well as fg1 does (correlation 0.376 against 0.361) without having seen them, so fg1 wasn't short of data alone.
- The net's job in a beam is to separate candidates at one tick, and neither net can: correlation within a state and tick is about 0.04. Of the label variance of 8.71, only 2.36 lies within a state and tick. The rest differs between ticks, which a beam doesn't need.
- Samples kept by the beam and samples from all candidates had the same mean label (8,560 and 8,544). A 100-tick foresight player recovers from nearly any start, so the label says little about the candidate.

`train_value.py --center` trains on differences within a tick, for later tests.

## Beam ancestry labels

A beam's parent links could give free labels, by reading how each kept state's descendants scored (`sim_search --ancestry`). This doesn't work as it stands: a width-50 beam has only 1.0-1.1 distinct ancestors 20 ticks back. One parent's near-identical children fill the beam.

Capping children per parent did not raise scores, width 200, six maps:

| Limit | Mean | Best |
|---|---|---|
| None | 164,770 | 178,028 |
| 20 | 165,381 | 186,129 |
| 5 | 160,167 | 177,241 |
| 2 | 156,257 | 167,340 |

Noise here is about 10,000. I expected a clear gain and didn't get one.

## Budget against score

Foresight beam, gen2 weight 1, flood 1, every tile, six maps:

| Width | Mean | Best | Waves |
|---|---|---|---|
| 1 | 43,095 | 54,113 | 24 |
| 2 | 61,171 | 71,821 | 33 |
| 3 | 66,254 | 83,597 | 35 |
| 5 | 81,645 | 99,684 | 41 |
| 10 | 98,795 | 114,605 | 47 |
| 20 | 114,243 | 120,372 | 53 |
| 200 | 164,770 | 178,028 | 65 |
| 1,000 | 175,649 | 188,030 | |

Live MCTS, 120 games paired with `fresh0` (seeds 5000+, `deep_crevasse` and `volcanic_plains`). Budget 7,000 mean 54,667:

| Budget | Mean | Best | 80k+ | Against 7,000 |
|---|---|---|---|---|
| 14,000 | 57,863 | 83,962 | 1 | +2,454 ±723 |
| 28,000 | 61,070 | 84,288 | 2 | +5,661 ±702 |
| 56,000 | 63,607 | 104,580 | 6 | +8,198 ±732 |

Width 3 costs about what a live move can afford, and scores 66,254 against 54,667 live. So at live budget, foresight barely helps. But 8 times the budget adds about 50,000 with foresight and only 8,200 without. Budget alone won't close the gap: long plans pay off only when the dice are known, so a faster simulator is worth about 2,700 per doubling.
