# Status

Last updated: 2026-11-06, 11:00. This page goes stale fast, so check the date.

## Scores

| What | Score | Notes |
|---|---|---|
| Best server game | **85,231** | v9. The leaderboard keeps the best single game |
| Live bot (v11), server | 51,945, 50,895, 67,855, 60,551 | Four games |
| Live bot (v12), server | 48,486 mean | 47 games, best 67,461 |
| v12 with PPO prior, server | 52,186 mean | 40 games, best 77,532 |
| Live bot (v11), simulated | 54,667 mean | 240 games on two biomes, best 89,737 |
| Foresight search, full seed knowledge | 229,399 | About 600 times the server budget. Not reachable live |
| Leaderboard, 2026-11-03 | 204,657, 165,224, 81,238 | Ranks 1 to 3 |

The qualifier deadline is about 2026-11-06 in the evening, so check the dashboard. Simon expects our score to be enough to qualify, and research can continue past the deadline.

## Code

- `bots/search_bot` is v11: open-loop MCTS plus the gen2 value net, written in C++ and called from Python. The live build, v12, adds a library built for x86-64-v3, shortens the search when a tick arrives late, and can load a policy net as a root prior.
- `main` holds v9 and `develop` holds v11 plus research tools. Experimental search options are off by default, so the live bot's behaviour hasn't changed since v11.
- `rich/dev` isn't merged into `develop`, though the late-tick fix, the x86-64-v3 flag and two simulator speedups were copied from it. It adds Linux support, PGO builds, a server freeze analysis and MCTS research. A test merge conflicts only in `docs/status.md`. Its `AGENTS.md` no longer has the planted text.

## Server games on 2026-11-06

90 games, none beating 85,231:

| Build | Games | Mean | Best | 60k+ games | Late ticks per game |
|---|---|---|---|---|---|
| 120 ms | 3 | 43,340 | 46,301 | 0 | 23.3 |
| 95 ms, v3 library, late-tick fix (v32, v34) | 47 | 48,486 | 67,461 | 2 | 0.04 |
| The same with the PPO prior (v33, v35) | 40 | 52,186 | 77,532 | 7 | 0.6 |
| The small tree net on every depth-1 node, with the speedups (v36) | 21 | 51,507 | 68,418 | 0 | 1.2 |
| **The small tree net from a node's second visit (v37)** | **49** | **55,434** | **79,302** | **3** | **1.0** |

- **The PPO prior helps.** With biomes weighted equally it's +3,526 ±1,447 on the server. The first simulated test charged the net 21% of the budget and found +1,155 ±473. At the server's real capacity, 6,300 ticks per move and 700 for the net, it's **+2,327 ±336** on 360 fresh paired games, with 6 games at 80k+ against 0 and 19 at 70k+ against 7. It gained on all three biomes.
- **120 ms is too long.** Server stalls run 50-60 ms, so a stall during the search passes the 150 ms limit. At 95 ms, the same stall still arrives in time.
- **The server scores below simulation at budget 7,000.** Test at `--budget 6300`, which is what the server gets today, and charge the policy `--policy-cost 700`.

- **The small tree net helps, if it's used sparingly.** It's a 16-channel copy of the PPO net that orders moves one level down. Used on every depth-1 node (v36), it left about 700 simulated ticks per move on the server, against about 5,000 for v35, and gained nothing. From a node's second visit (v37), it evaluates about 5 nodes per move and keeps about 4,700 ticks. With biomes weighted equally, v37 scored 54,283 ±897 on the server against 52,010 ±1,946 for v35, so +2,273 ±2,143.
- **Local tests must charge server costs.** At a charge of 75 ticks per small-net evaluation, every depth-1 node looked like +2,553 ±382. At 460 ticks and 1,370 for the root net it's -1,638 ±677, which matches the server, while the second visit is +1,230 ±609 and the third +1,382 ±734, on 120 paired games. `simulated` in the BUDGET log includes a flat 1,500 charge for the root net, so subtract it.
- **Stronger nets alone don't make better priors.** Paired against `oracle1c` as the root prior: the DAgger PPO net +466 ±336 on 360 games (45,684 alone), plan-start PPO +1,140 ±775 on 120 games (45,470 alone), and the `volcanic_plains` net -708 ±850 on 120 volcanic games (48,131 alone against 45,854).

The active server build is v37, with the PPO prior and the small tree net. To build it, copy `policy_net.bin` from `runs/ppo/oracle1c/` into `bots/search_bot/`, which is git-ignored, and run `tools/make_zip.py`. Without that file, the bot plays without the prior.

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

These first tests overcharged the net, so they understate the prior. See the server section above.

The net is in `runs/ppo/oracle1c/`, which isn't in git. `bot.py` loads it if `policy_net.bin` is in the bot folder.

## Open ideas

Strongest first. Expected value is judgement, not measurement.

1. **Dice-free terrain planner.** Terrain and lava don't depend on hidden dice, so plans that shape them hold up where kill plans don't. Search meteor and volcano sequences in a dinosaur-free model to keep the spawn area small, then let MCTS take the kills. Test first offline with true future rows, because if it fails there it can be dropped cheaply. The main risk is that new rows keep arriving exactly where the pockets are, and flood value failed on guessed rows (-502 ±403). Greedy carving failed too, but it couldn't plan multi-step channels.
2. **Start PPO games from foresight positions.** PPO has levelled off near 44,000, and it rarely sees a 9,020-point tick, because it must first build a pocket by chance. Starting some games part-way through foresight plans would show it what pockets are worth. This needs state resets in `sim/vec_env.cpp`.
3. **Play for high scores.** The leaderboard keeps the best game, but MCTS optimises the mean. Optimistic MCTS (`rich/dev`) is a first step: +1,740 ±781 on 30 simulated games, and no gain on 12 launcher games. Both samples are too small to judge its effect on top scores. Try percentile backups, and measure the rate of 80,000+ games.
4. **Study the widest plans.** What do the best 100-tick windows of the 207,700 and 229,399 plans do that doesn't rely on knowing the dice? `tools/analyze_plan.py` is a start. Regenerate a plan with the command in [results.md](results.md#foresight-search). Also open: why do most pocket streaks earn 10,000-20,000 when the best earned 63,000?

## Is rank 1 predicting seeds?

Unknown. Rank 1's 204,657 is close to what our widest foresight search finds, which could mean they know the seeds in advance. Against that, the 22 seeds seen so far are version 4 UUIDs and the engine uses `crypto.randomUUID()`. Our bot has also earned 51,731 points in 100 ticks without foresight, and foresight scores were still rising with width, so 229,399 isn't a true maximum. We haven't tried to attack server seeds. If it comes up, ask the organizers.
