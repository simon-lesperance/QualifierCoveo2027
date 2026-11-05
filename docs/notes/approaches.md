# Competitive approaches

> **Lab notes, kept as history.** They were written as the work happened, so earlier sections can be wrong or out of date, and later sections correct them. For current conclusions, see [results.md](../results.md) and [status.md](../status.md).

Nothing here has been built yet. **Score ranges are judgment calls**, not measurements. The only measured bots are the starter (mean 2,859) and an idle bot (mean 1,086), over 12 games each.

## What the leaderboard tells us

| Rank | Score |
|---|---|
| 1 | 204,657 |
| 2 | 165,224 |
| 3 | 81,238 |
| 5 | 65,987 |
| 10 | 39,514 |
| 20 | 19,334 |
| 30 | 8,596 |
| 40 | 3,303 (about the starter bot) |

- **Rank 1 averages 341 points per meteor slot.** The starter bot gets 4.8.
- **Single kills cannot reach this.** At age 5 it would take 1,462 kills, with about 600 meteors available.
- **The field is stronger than expected.** Ranks 1-2 are about double rank 3, which suggests they found a mechanism and not only better tuning.
- Scores are best-of-N, so they include some luck, and biome seems to matter. Both bots scored best on `mountain_range`.

To reach 204,657 you would need one of:

| Kills per event | Age | Events needed | Dinosaurs needed |
|---|---|---|---|
| 3 | 10 | 277 | 832 |
| 5 | 10 | 111 | 555 |
| 10 | 10 | 30 | 303 |
| 10 | 0 | 23 | 227 |
| 20 | 30 | 12 | 238 |

Reaching several hundred dinosaurs requires clearing the map to trigger early waves, since timer waves alone give only 100. So the game comes down to two numbers: **how many dinosaurs die per kill event, and how fast waves are cleared.**

## Search results so far

See [Max Score Estimate](max-score.md). Foresight beam search found 100,000-137,000 and showed that tempo matters most: clear a wave about every 18 ticks. Scoring by actual game score beat hand-made rewards. It still falls short of rank 1, most likely because it rarely builds large multi-kills.

## Live bot lessons

- Rollout horizon must be at least 4, the meteor delay. At 3, the score fell from 39,000 to 16,000.
- Volcanoes add about 10,000.
- Beam 30 and radius 2 gave the best results; beam width stops mattering around there.
- Averaging over several RNG guesses hurt, because it splits the budget.
- Rewarding a cleared map or fewer living dinosaurs changed nothing.
- More search barely helps, so the next gain needs something else.
- Unknown tie-breaks cost about 19,000 ([Hidden Information Test](hidden-information.md)). Open-loop MCTS beats beam search by 6-8% by averaging over them, and it is now the live bot.

Training idea from Simon: give a bonus reward for large group kills early in training.

## Mechanisms to test first

These are untested hypotheses from reading the engine. They matter more than which search algorithm we pick.

1. **Spawn-zone control.** Spawns must connect to the bottom row. A lava wall across the map could confine a wave to a small strip, where one 13-tile blast hits many dinosaurs at age 0.
2. **Spawn-tick strikes.** Impacts resolve after spawning, so a meteor timed to a wave lands with no warning. Clearing the map makes the next wave's timing exactly predictable.
3. **Stegosaurus convergence.** They all walk to one shared point, so they cluster naturally. Half of all wave rows are mostly Stegosaurus.
4. **Lava fronts.** Lava enters all equal-low neighbours at once, so a wide front may kill several at once, and these share the multiplier with a meteor landing that tick.
5. **Edge kills.** Frame exits also share the multiplier. Dinosaurs pinned against the top edge might be timed to a shift. The idle bot's 2,134 on `mountain_range` suggests this happens by itself.
6. **Meteor fencing.** Footprints act as walls before they land, so they can herd dinosaurs together or into corners.

## What recent competitions used

Most of these are adversarial, so they only partly apply. A solo game has no opponent to overfit to, and the reward is just the score, which makes learning easier.

| Contest | Result | Run-time search | Neural network | Training |
|---|---|---|---|---|
| CodinGame Winter 2026 | #1, frictionless | MCTS per snake, depth 12, about 50k turns per 50 ms | None | Static evaluation |
| CodinGame Winter 2026 | #5, sZoom | MCTS, 4-6k iterations per 45 ms | Small value net | Not read |
| CodinGame Winter 2026 | #17, jacek | MCTS, about 1,500 iterations | Value net | Self-play positions |
| CodinGame Winter 2026 | #91 | Beam search | Tried CNN, then PPO; dropped | RL regressed, beam shipped |
| CodinGame Winter 2024 | #1, reCurse | Alpha-beta, depth 2-3, about 100 evals per 50 ms | 98k-parameter ResNet | PPO self-play |
| CodinGame Summer 2024 | #2, reCurse | MCTS with PUCT; many short searches, majority vote | 39k-parameter net, distilled from 6M teacher | PPO self-play |
| CodinGame Fall 2023 | #1, reCurse | None | 156k-parameter MLP + GRU | PPO, self-play |
| Lux AI S3 (Kaggle) | #1, Flat Neurons | Not mentioned | ResNet + ConvLSTM + Transformer | IMPALA, 8 x H100, 3-4 days |
| Lux AI S3 (Kaggle) | #3 and #8 | Not mentioned | Yes | Imitation learning |
| Orbit Wars (Kaggle 2026) | #13 | Not stated | Yes | Imitation, then PPO league |
| Orbit Wars (Kaggle 2026) | #220 of 4,730 | Heuristic planner | None | Optuna tuning |

I found no first-place Orbit Wars writeup, no Coveo Blitz 2026 writeup, and no good primary source on AtCoder Heuristic Contests, which would be the closest solo analogue.

### Patterns

- **A fast, exact simulator comes first.** Winners nearly always built their own.
- **Search plus a small value net is a repeating winner** where a simulator exists. The 2026 #1 needed no net at all.
- **Policy-only bots can win, but reCurse found them insufficient** in both later contests.
- **RL has a real failure rate on short timelines.** The #91 entry shipped beam search after RL regressed.
- **Simple rewards beat shaped ones** in Lux S3.
- **Several short seeded searches beat one deep one** under randomness (reCurse, Summer 2024). This suits us, since tie-breaks and spawns are unknown.
- **Gumbel AlphaZero** works with very few simulations, which fits a tight budget.
- **Stochastic MuZero** is designed for a learned model; we have an exact one, so it adds little.
- **RHEA vs MCTS** evidence is mixed, and comes mainly from GVGAI general game playing.

## Options

Every option except the first needs the exact simulator, which is about 2 days of the work. Complexity below is the additional work on top of it.

| # | Approach | Added complexity | Estimated score | Main risk |
|---|---|---|---|---|
| 1 | Reactive heuristics, no simulator | Low (hours) | 10-30k | Dead end; cannot plan traps |
| 2 | Predictive greedy: simulate each candidate target and pick the best | Low (0.5 day) | 20-50k | Plans one meteor at a time |
| 3 | **Rolling-horizon beam search** with hand-tuned evaluation | Medium (1 day) | 50-150k | Horizon misses long setups like lava walls |
| 4 | Open-loop MCTS / RAVE with heuristic rollouts | Medium (1-1.5 days) | 50-130k | Few rollouts per move for a large action space |
| 5 | **Option 3 or 4 plus a learned value net** | Medium-high (+1-2 days) | 100-250k | Needs good data; inference cost cuts search depth |
| 6 | PPO or IMPALA policy, no search | High (2-3 days) | 20-150k, high variance | Precise trap timing is hard to learn; may never find mass kills |
| 7 | AlphaZero / Gumbel: policy + value + search | Very high (4+ days) | 100-300k if it works | Unlikely to finish in the time left |
| 8 | Offline per-seed solver (last year's approach) | Medium | Not for live play: no seed repeated in 6 server games | The bot cannot know the seed in a live game |

### How they compare

- **Beam search vs MCTS.** The simulator came in about 10x faster than estimated, so this is closer than I first thought. About 6,500 simulated ticks per move is roughly 325 depth-20 rollouts, which makes MCTS workable. I still lean to beam search first, because a meteor's outcome is mostly decided within 4-10 ticks, but both can now share one simulator and evaluation, so we can test them head to head.
- **Simon's MCTS + model approach** is option 5 with MCTS as the search. It suits this game well, and it adds the learned part after a working bot exists.
- **Where RAVE fits.** RAVE shares value across moves, and a target tile is a natural unit to share. It could help if rollouts stay thin, but it matters less than the evaluation.
- **Why not pure RL.** Mass kills need several exact steps with no reward until the end, which is the hard case for exploration.
- **The offline solver still has a use.** With perfect knowledge of a seed it gives an upper bound, shows what mass kills look like, and can produce imitation data.

## Recommendation

Build in layers, keeping a submittable bot at each one. **Registration closes in about 3 days**, so option 5 is the realistic ceiling.

1. **Simulator in C or C++**, built as a `.so` for the bot and natively for training, so both share one codebase. A prebuilt library runs on the server and beat Numba there. Test it tick by tick against the real engine on recorded games, starting with a random-action bot.
2. **Offline exploration** of the mechanisms above, on known seeds. This should show what ranks 1-2 are doing.
3. **Predictive greedy**, then upload to secure a good score early.
4. **Beam search**, with evaluation weights tuned by Optuna or CMA-ES on the local 12-thread CPU.
5. **Value net**, if time allows: fit it to outcomes from our own games, then retrain as the bot improves. This is the "train as we play" loop Simon described.

### Which map to train on

Scoring takes the best single game, not a sum, so we only need one great run. In 2026 the server also ran one map per upload, and a solver built for one map meant resubmitting until it came up. If that still holds, **training should focus on the map or biome with the highest scoring potential**, not spread across all three.

Two things decide this, and neither is known yet:

- **How maps are chosen on the server.** Now answered: six server games gave six different seeds and a random biome each, so each game re-rolls the map. We can't pick a biome, but practice games are a cheap way to roll one. There is no fixed map to solve, so training targets a biome.
- **Which biome pays best.** With planning, `deep_crevasse` leads ([Max Score Estimate](max-score.md)). The simple bots suggested the opposite: `mountain_range` led for both baselines (starter 3,647 vs 2,849 and 2,082; idle 2,134 vs 524 and 600), but that is 4 games per biome from bots that do not plan, so it may not hold for a strong bot. Compare biomes again once the first planning bot works.

Decide before training, because a biome-specific net is cheaper to train and likely stronger on that biome.

### Hardware

The RTX 3080 and Ryzen 5600 cover a small value net. Game generation is limited by CPU, so if we rent anything from the $50 budget, a many-core CPU instance for a day of tuning or data generation would help more than a GPU. Decide after measuring simulator speed.

## Sources

See [References](../references.md).
