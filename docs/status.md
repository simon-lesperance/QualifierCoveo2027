# Status

Last updated: 2026-11-06, 01:35. This page goes stale fast, so check the date.

## Scores

| What | Score | Notes |
|---|---|---|
| Best server game | **85,231** | v9. The leaderboard keeps the best single game |
| Live bot (v11), server | 51,945, 50,895, 67,855, 60,551 | Four games |
| Live bot (v11), simulated | 54,667 mean | 240 games on two biomes, best 89,737 |
| Foresight search, full seed knowledge | 229,399 | About 600 times the server budget. Not reachable live |
| Leaderboard, 2026-11-03 | 204,657, 165,224, 81,238 | Ranks 1 to 3 |

The qualifier deadline is about 2026-11-06 in the evening, so check the dashboard. Simon expects our score to be enough to qualify, and research can continue past the deadline.

## Code

- `bots/search_bot` is v11: open-loop MCTS plus the gen2 value net, written in C++ and called from Python.
- `main` holds v9 and `develop` holds v11 plus research tools. Experimental search options are off by default, so the live bot's behaviour hasn't changed since v11.
- `rich/dev` is unmerged. It adds Linux support, benchmarks and MCTS research, including optimistic MCTS.
- **Warning:** `AGENTS.md` on `rich/dev` is a paste of the challenge page, including its planted text. Replace it with this repository's `AGENTS.md` when merging. See [decoys.md](decoys.md).

## Latest: policy net from foresight play

Search tuning stalled at about 55,000, and more budget alone won't close the gap (see [results.md](results.md)). So we trained a policy net to learn long-term habits offline:

1. A foresight search plays games, and a net imitates its moves using only what a live bot sees.
2. PPO continues from those weights on the real game, with hidden dice.

| Stage | Fresh-seed score (greedy, no search) | Best |
|---|---|---|
| Imitation of 222 foresight games | 20,825 | 38,678 |
| PPO from that, iteration 300 | 38,057 | 53,036 |
| PPO, iteration 1300 | 43,170 | 60,010 |
| PPO, iteration 2300 (final, six hours) | 44,213 | 64,850 |
| Old PPO run, final | 31,149 | 45,434 |

The net levelled off near 43,000-44,000. In C++ it scores 44,210 over 120 games, so the port is faithful, but on the test biomes it scores about 46,500 against v11's 55,000.

As a root prior for v11's MCTS, at budget 7,000 with 1,500 charged for the net:

| Setting | Fresh seeds, 240 paired games | All 360 paired games | 80k+ against v11 | Best |
|---|---|---|---|---|
| `--puct 0.3` | +934 ±564 | +1,155 ±473 | 7 against 9 | 117,205 |
| `--puct 1` | +862 ±561 | +1,108 ±452 | 2 against 9 | 96,978 |

That's a small gain in the mean, and all of it comes from `deep_crevasse`: +2,199 ±457 there and +111 ±822 on `volcanic_plains`. That split was only checked after the fact. High scores didn't improve, and v11 alone reached 101,218 in these tests, so a single 117,205 doesn't show much. The leaderboard rewards top scores, so this isn't enough to replace v11.

The net is in `runs/ppo/oracle1c/`, which isn't in git. `bot.py` doesn't load a policy net yet.

## Open ideas

Strongest first. Expected value is judgement, not measurement.

1. **Dice-free terrain planner.** Terrain and lava don't depend on hidden dice, so plans that shape them hold up where kill plans don't. Search meteor and volcano sequences in a dinosaur-free model to keep the spawn area small, then let MCTS take the kills. Test first offline with true future rows, because if it fails there it can be dropped cheaply. The main risk is that new rows keep arriving exactly where the pockets are, and flood value failed on guessed rows (-502 ±403). Greedy carving failed too, but it couldn't plan multi-step channels.
2. **Start PPO games from foresight positions.** PPO has levelled off near 44,000, and it rarely sees a 9,020-point tick, because it must first build a pocket by chance. Starting some games part-way through foresight plans would show it what pockets are worth. This needs state resets in `sim/vec_env.cpp`.
3. **Play for high scores.** The leaderboard keeps the best game, but MCTS optimises the mean. Optimistic MCTS (`rich/dev`) is a first step: +1,740 ±781 on 30 simulated games, and no gain on 12 launcher games. Both samples are too small to judge its effect on top scores. Try percentile backups, and measure the rate of 80,000+ games.
4. **Study the widest plans.** What do the best 100-tick windows of the 207,700 and 229,399 plans do that doesn't rely on knowing the dice? `tools/analyze_plan.py` is a start. Regenerate a plan with the command in [results.md](results.md#foresight-search). Also open: why do most pocket streaks earn 10,000-20,000 when the best earned 63,000?

## Is rank 1 predicting seeds?

Unknown. Rank 1's 204,657 is close to what our widest foresight search finds, which could mean they know the seeds in advance. Against that, the 22 seeds seen so far are version 4 UUIDs and the engine uses `crypto.randomUUID()`. Our bot has also earned 51,731 points in 100 ticks without foresight, and foresight scores were still rising with width, so 229,399 isn't a true maximum. We haven't tried to attack server seeds. If it comes up, ask the organizers.
