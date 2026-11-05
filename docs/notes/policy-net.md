# Policy net notes

> **Lab notes, kept as history.** They were written as the work happened, so earlier sections can be wrong or out of date, and later sections correct them. For current conclusions, see [results.md](../results.md) and [status.md](../status.md).

Started 2026-11-04, about 01:40. The aim is a network that learns setups longer than the search horizon, such as holding fire while dinosaurs clump, without hardcoding them.

## Setup

- `sim/vec_env.cpp`: batched games for Python, built as `build/libvec.dll`. Training only.
- `tools/train_ppo.py`: PPO over the true game. The net sees only what a live bot sees. Reward is game points.
- Network: 148,756 parameters, full-resolution CNN with dilated convolutions, one logit per tile for meteors and for volcanoes, plus one for doing nothing.
- `sim/policy_net.cpp`: hand-written inference. Matches PyTorch to 0.00009 on logits, about 13 ms per evaluation on a loaded CPU.
- `sim/agent.cpp`: the policy can act as a root prior for MCTS (PUCT), and adds its favourite targets to the action list.

## Results

| Run | Time | Fresh-seed score |
|---|---|---|
| PPO from nothing, iter 100 | 0.2 h | 8,874 greedy |
| iter 200 | 0.45 h | 16,384 |
| iter 300 | 0.7 h | 21,245 |
| iter 400 | 0.9 h | 23,799, best game 39,480 |
| Imitating MCTS visits (171,000 samples, 8 epochs) | 7 min | 8,777 greedy |

Imitation was much weaker than PPO: MCTS visits are spread thinly, so a copy learns little from them.

## Other checks

- UUID seeds, so the seed can't be guessed from the terrain.
- Sustained volcanoes, no meteors: lava covers 5-21% of the map and the spawn area stays above 200 tiles. Confining waves with lava alone isn't practical.

## Spawn pockets (found 2026-11-04, about 03:00)

**The biggest finding so far.** A wave spawns only on tiles connected to the bottom row. When lava leaves that area small, all 10 dinosaurs spawn close together, and a meteor landing on the wave's first tick kills them at age 0. They have no time to flee.

- One such tick: 10 kills, **9,020 points**. A normal wave start gives about 50.
- A cleared map brings the next wave on the next tick, so a small area can repeat this every few ticks.
- Game `volcanic_plains` live-408: **51,731 points in 100 ticks**, and 63,217 over the whole streak, about 10 times the usual rate. That game scored 103,717.
- How it forms: a volcano on a high mountain near the bottom. Lava floods every tile reachable downhill, so in flat terrain it covered rows 7-18 within 25 ticks, leaving only the bottom strip to spawn on.
- This is likely how rank 1 gets far past what we estimated. My earlier "no spawn-tick kills without foresight" was wrong for small spawn areas, where spawn tiles don't need foresight.

Earlier rejection of "confining waves with lava" was wrong: it measured plans that weren't trying, and only one volcano placement at a time.

| Test, 36 games, same seeds | Mean | Best |
|---|---|---|
| gen2 value net, no wave planning | 49,466 | 73,950 |
| + simulates waves in search (build v9) | **52,934** | **103,717** |
| + leaf bonus for a small spawn area, weight 0.3 | 52,592 | 99,268 |
| + leaf bonus, weight 1 | 51,556 | 90,810 |
| + lava-only volcano lookahead, search volcanoes on | 53,059 | 103,717 (rarely triggers) |

More tests on the same seeds:

| Test | Mean | Best |
|---|---|---|
| Lookahead, threshold 300, search volcanoes on | 53,403 | 103,717 |
| Lookahead only, search volcanoes off, threshold 1000 | 41,077 | 77,984 |
| Lookahead only, threshold 300 | 43,392 | 90,961 |

At a budget of 7,000 ticks per move, closer to the server:

| Settings | Mean | Best |
|---|---|---|
| **Depth 5, widening 3 (live)** | **49,854** | 102,087 |
| More pocket targets (limit 120, 8 targets) | 49,502 | 77,680 |
| Depth 7 | 48,238 | 85,050 |
| Widening 5 | 46,953 | 69,889 |

A volcano is nearly always already active, and few moments offer a good one: probing every mountain found average outlook over 300 on only 0-13 of 100 checks per game. Pockets depend on terrain, so they stay occasional.

Server, v9: 48,268, 39,348, 48,686, 49,900, **85,231**. Best so far 85,231.

## GPU failure

At about 04:20 the GPU fell off the system: `nvidia-smi` reports "GPU is lost. Reboot the system to recover this GPU". The last checkpoint is from 03:30, and the log suggests it may have stalled around then. It had been at 99-100% for over two hours. PPO stopped at 31,149 on fresh seeds, which was still below MCTS. The machine needs a reboot, which was left for Simon.

## Later results, 2026-11-04 morning

All at 7,000 ticks per move, on 72 games over two seed sets unless noted.

| Bot | Seeds 400 | Seeds 500 | Mean |
|---|---|---|---|
| Wave planning, even volcano spread (v9) | 49,854 | 48,417 | 49,135 |
| Volcanoes ranked by flooding, top 6 (v10) | 51,143 | 50,310 | 50,727 |
| **Top 3 (v11, live)** | **51,648** | **52,448** | **52,048** |
| Top 1 | 50,887 | 50,913 | 50,900 |

The gaps between the ranked versions are within noise, roughly 1,200 on a 72-game mean. Top 3 was picked for having the best mean, and the ranking's gain over v9 is the firmer result.

Ranking tried at 15 and 45 ticks of lookahead on seeds 500: 50,716 and 50,056, against 50,310 at 30.

Not worth shipping, at 7,000 ticks and seeds 400 (baseline 49,854):

| Change | Mean |
|---|---|
| Depth 7 | 48,238 |
| Widening 5 | 46,953 |
| Horizon 6 | 47,744 |
| Exploration 0.3 / 0.8 | 48,144 / 46,941 |
| More pocket targets | 49,502 |
| gen3 value net with spawn-area inputs, 2,300 games from plain search | 46,770 |

gen3 reached R² 0.19. Its data came from unguided search, with few `mountain_range` games, and that biome fell most.

gen4 repeated gen3 with better data: 2,100 games, balanced over biomes, played by the live configuration with the gen2 net. R² 0.20, but it scored 47,935 and 48,055 against 51,648 and 52,448, so it wasn't shipped. Why spawn-area inputs make the net play worse is unknown; R² doesn't predict play.

Server games:

| Build | Scores |
|---|---|
| v9 | 48,268, 39,348, 48,686, 49,900, **85,231**, 50,462, 43,140 |
| v10 | 42,241, 39,871 |
| v11 | 51,945, 50,895, 67,855, 60,551 |

Next steps:

1. Reboot, then continue PPO from `runs/ppo/b/latest.pt`, which has spawn-area inputs. It reached 31,149 and was still rising, but needs to pass 50,000 to help.
2. Retrain the value net with spawn inputs from guided games, once a GPU is back.
3. Look at why some pockets aren't exploited: most streaks earn 10,000-20,000, against 63,000 for the best.

## Flood value, true rows and luck (2026-11-04, morning)

Simon spotted the pattern in the 85,231 game: a volcano on a tall mountain near the bottom, row 18 at elevation 14, placed on tick 431. The spawn area fell from 277 to 47 at tick 442 and to 2-18 by ticks 454-460. Ticks 455-470 earned 22,079 points. If no tile connects to the bottom row, waves scatter instead, so the ideal is a pocket of about 10 tiles.

**Flood value** credits a volcano in search with its long-range pocket value, from 40 ticks of lava alone. Code is on `feature/flood-value`, off by default (`--flood-weight`).

120 paired games on seeds 1000+, `deep_crevasse` and `volcanic_plains`, 7,000 ticks per move:

| Bot | Mean | Best | 70k+ | 80k+ | Against v11 |
|---|---|---|---|---|---|
| v11 | 55,249 | 85,525 | 5 | 2 | |
| Flood value 0.1 | 54,747 | 80,408 | 4 | 1 | -502 +- 403 |
| Flood value, exact wave timer | 54,708 | 80,408 | 3 | 1 | -540 +- 416 |
| True future rows | 57,238 | 82,673 | 10 | 1 | **+1,989 +- 632** |
| True rows and flood value | 58,417 | 83,215 | 13 | 2 | **+3,167 +- 697** |

- The early 103,074 from flood value was noise. It isn't worth shipping on guessed rows.
- Flood value does work with true rows, so its weakness is the repeated bottom row. Earlier tests showing no value in true rows predate volcano ranking.
- The exact wave timer adds nothing.
- Perfect row knowledge is an upper limit of about 6%, and it more than doubles 70k+ games. A real predictor would get part of that.

**Map or dice:** 15 maps, 4 dice rolls each, by keeping the terrain seed and changing the game's RNG seed (`--rng-tag`). `deep_crevasse`: dice sd 4,076, map sd 4,861, 41% from dice. `volcanic_plains`: dice sd 6,259, map sd 3,931, 72% from dice. One map ranged from 50,283 to 73,782.

**Spread of v11, 120 games:** `deep_crevasse` mean 54,285, sd 5,390, 41,820-68,209. `volcanic_plains` mean 56,213, sd 8,857, 44,264-85,525. All six best games were on `volcanic_plains`, each with 20,000-26,000 points in its best 100 ticks, against a typical 8,000-9,000.

Seeds: all 22 seen are version 4 UUIDs, and the engine uses `crypto.randomUUID()`, so they can't be predicted.

## Experiments 1-3 (2026-11-04, about 10:40)

These tested three guesses at how rank 1 scores 200,000+. All three failed as tested. Code is on `feature/pocket-experiments`.

**1. Pocket value** (`sim_pocket`, spawn area of 30 tiles or fewer, 100 ticks from the same true game):

| Continuation | `deep_crevasse`, 40 events | `volcanic_plains`, 61 events |
|---|---|---|
| Search only | 8,239 | 10,175 |
| Always fire at the pocket | 7,404 (-835 +- 296) | 8,531 (-1,644 +- 436) |
| Fire with 2 or fewer alive | 7,787 (-452 +- 217) | 9,079 (-1,096 +- 346) |
| Fire when all alive are in the blast | 8,239 (+0) | 10,026 (-149 +- 134) |

Search beat the simple rules. An average pocket is worth about 8,000-10,000 over 100 ticks, about twice a normal stretch. The 50,000 stretch was an extreme case, not typical.

**2. Carving limit** (`sim_carve`, lava only, true rows, best volcano plus up to 6 greedy meteors, one position per 100 ticks, about 30 games per biome). Mean outlook with 40 ticks of lookahead:

| Biome | Best volcano | + 6 meteors | Positions over 1,000 |
|---|---|---|---|
| `deep_crevasse` | 55 | 82 | 0.7% to 1.1% |
| `volcanic_plains` | 49 | 59 | 0% to 0% |
| `mountain_range` | 7 | 10 | 0% to 0% |

60 ticks of lookahead was lower still. Greedy carving adds almost nothing. This doesn't rule out longer, coordinated carving: greedy can't plan a multi-step channel, and rare windows can be missed between samples.

**3. Wider targets** (`--grid 1`, 120 paired games): 51,188 against 55,249, -4,061 +- 735. Extra options dilute search at this budget. It says nothing about whether those shots could be good.

Remaining leads: true rows, capped at +3,167 +- 697, and a long foresight search to show what 200,000 play looks like.

## Search tests: depth, caching, RAVE (2026-11-04)

Code is on `feature/search-tests`. All options are off by default, and nothing here shipped. 120 paired games (seeds 1000+, `deep_crevasse` and `volcanic_plains`, budget 7000) against v11 at 55,249:

| Test | Mean | Difference | >=70k | Best |
|---|---|---|---|---|
| Depth 4 | 54,608 | -640 +- 747 | 4 | 81,936 |
| Depth 6 | 54,394 | -854 +- 660 | 5 | 81,477 |
| Cache, 8 guesses | 54,065 | -1,183 +- 584 | 6 | 82,300 |
| Cache, 16 guesses | 56,588 | +1,339 +- 752 | 7 | 94,676 |
| Cache, 32 guesses | 54,438 | -810 +- 592 | 3 | 75,495 |
| RAVE k=30 | 53,255 | -1,993 +- 583 | 5 | 79,824 |
| RAVE k=30, ordering | 53,913 | -1,335 +- 617 | 4 | 83,282 |
| RAVE k=300, ordering | 54,247 | -1,001 +- 703 | 7 | 86,705 |

**The 16-guess result was noise.** Replicated on 240 fresh paired games (seeds 5000+): 54,586 against 54,667, -80 +- 469, >=70k 9 against 8, best 84,575 against 89,737. Pooled over all 360 games, the effect is about +390 +- 400.

Wall time at budget 7000 is the same for both (93 s against 94 s for 4 games on one thread). The budget charges only real steps, so a cache hit buys more iterations in the same time, not a shorter move.

- The removed line `if (!game.step(turn, action)) game.step(turn, Action{});` was never a live bug. With it gone and caching off, all 120 games matched `tail_base` exactly.
- Depth 5 stays.
- RAVE lowers the score here, probably because a shot's value depends heavily on what came before it. The same action at the same tick in another line is weak evidence.

Depth, widening, horizon, exploration, target count, caching and RAVE have all now failed to beat v11 by a clear margin. Tuning this search looks exhausted at about 55,000. True future rows remain the one measured gain (+3,167 +- 697), and that is a cap, not something the bot can reach yet.

## Imitating foresight plans (2026-11-05)

Why try: today's budget tests suggest live search can't reach long plans, because the dice are hidden. A policy net could learn good habits offline and sees only what a live bot sees, which is oracle guiding.

`sim_search --plan-data` replays the best plan and writes each position with its chosen move. 222 games at width 50 in 4 hours, all three biomes, mean teacher score 131,126, 222,000 positions.

| Run | Fresh-seed greedy | Best |
|---|---|---|
| Imitating MCTS visits (old) | 8,777 | |
| **Imitating foresight plans**, 20 epochs, 2.4 min | **20,825** | 38,678 |
| PPO from nothing, iter 300 | 21,245 | |
| PPO run `b`, last eval | 31,149 | 45,434 |

Validation cross-entropy 2.425, train 2.33, so little overfitting. Sampled play scores 9,299 and greedy 20,825, so the net's distribution is still broad. PPO continues from these weights in `runs/ppo/oracle1`, using `runs/ppo/oracle_run.sh`.

`policy_net.hpp` still has the older 26 input planes, and `train_ppo.py` uses 30. It needs updating before a PPO bot can run on the server.
