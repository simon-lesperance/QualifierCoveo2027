# Research notes, November 2026

What we read for ideas beyond the current search, and what we did with it. Results are in
[../status.md](../status.md).

## Sources and what they suggest

- **Gumbel MuZero** ([Danihelka et al., ICLR 2022](https://mlanthology.org/iclr/2022/danihelka2022iclr-policy)). Plain AlphaZero can fail to improve its policy when it doesn't visit every root action. Gumbel root search spends the budget by sequential halving on actions drawn from the policy, which suits a search of about 70 iterations. Tested here as `--root-halving`.
- **Stochastic Gumbel AlphaZero** ([overview in a Tetris Block Puzzle paper, 2026](https://arxiv.org/abs/2603.18994)). The paper reports strong small-budget play on a single-player stochastic game, but gives no schedule or comparison, so it only confirms the direction.
- **reCurse's CodinGame postmortems** ([Fall 2023](https://gist.github.com/reCurs3/7d4eb5b952790e3009337f61c3341074), [Summer 2024](https://gist.github.com/reCurs3/40793036b9800472eb219136a33f6997), [Winter 2024](https://gist.github.com/reCurs3/fea106bea9c55389706e959b97f956c0)). Winning bots of 39k-156k parameters. Useful points:
  - A big teacher distilled into a small net beat the same small net trained alone.
  - Convolutions are "a real CPU killer", at about 100 evaluations per turn for a 98k net at 50 ms. Measure the evaluations you can afford before picking a net.
  - With few evaluations, the policy as move ordering in a shallow search beat MCTS. This fits our small tree net.
  - Several shallow searches on different seeds beat one deep search in his game. We already tried voting and lost.
  - 8-bit quantization and hand-written AVX2. We already have the AVX2 path, and quantizing is still open.
- **Lux AI Season 3** ([9th place slides](https://speakerdeck.com/player/0671ff5ac7384b8ea6dc836183559c77)). Imitation of top teams with a UNet. The winner's writeup wasn't found. Less relevant, since the game is multi-agent.
- **NRPA** ([Rosin 2011](https://ijcai.org/Abstract/11/115), [Cazenave's variants](https://www.lamsade.dauphine.fr/~cazenave/papers/policyNRPA.pdf)). Holds records in single-player puzzles by adapting the playout policy to the best sequence. It's built for deterministic games, so at best it could help the foresight plans, not live play.

## Ideas that came out of this

1. **Sequential halving at the root**, in place of UCT and widening.
2. **Depthwise-separable nets.** One 3x3 filter per channel plus a 1x1 mix keeps 32 channels at about half the multiplications of the 16-channel net. On the server the full net takes about a fifth of the search time.
3. **A small net at the root as well**, which frees that time.
4. **Priors at depth 1**, since the tree net has already paid for its logits.

## Results, night of 2026-11-06

Local tests are 120 paired games on seeds 8000+, unless noted. Costs come from the server's BUDGET
logs: budget 7,240, full net 1,575, small16 175, depthwise 100.

| Change, against v37 (small16 tree net from the second visit) | Result |
|---|---|
| small16 on every depth-1 node | -204 ±595 |
| small16 from the third visit | -150 ±547 |
| small16 to depth 2 | -694 ±634 |
| Root prior weight 0.15 / 0.6 | -1,386 ±591 / +83 ±607 |
| Widening 2 / 4 | +856 ±571 / -1,280 ±638 |
| Exploration 0.35 / 0.7 | +538 ±557 / -812 ±691 |
| Value weight 0.2 / 0.4 | -615 ±586 / +534 ±568 |
| 15 / 50 policy targets | -534 ±657 / +130 ±617 |
| Depth 4 / 6 | -1,284 ±581 / +325 ±570 |
| ds32 on every depth-1 node, full net at the root | +661 ±583 |
| ds32 from the second visit | -761 ±583 |
| ds32 to depth 2 | -156 ±602 |
| **ds32 at the root and on every depth-1 node** | **+1,687 ±687** |
| The same to depth 2 | +1,253 ±685 |

`ds32` is a depthwise-separable copy of the DAgger PPO net: 32 channels, 11,427 parameters, 0.44 ms per
evaluation against 2.2 ms for small16 and about 14 ms for the full net on the same machine. It picks
the full net's top move 76% of the time, and has it in its top 5 96% of the time. Training took 20
minutes on the GPU. 24 and 48 channels reached 72% and 75% in the same time.

Map skipping: the bot reads the map on tick 1 from the height of the lowest mountain tile (20 on
`volcanic_plains`, 16 on `mountain_range`, 13-14 on `deep_crevasse`). That matched all 193 server
games. On other maps it replies late until it's kicked after 50 ticks, which voids the game. In 1,560
local games only `volcanic_plains` reached 85,000: 11 of 520, against a best of 82,162 on
`deep_crevasse` and 56,443 on `mountain_range`.

### Fresh seeds 9000+, 180 paired games

| Change | Against | Result |
|---|---|---|
| ds32 at the root and in the tree, widening 2, exploration 0.35, value weight 0.4 (v43) | v37 | +3,241 ±554 |
| Exploration 0.25 / value weight 0.5 / 0.6 | v43 | -560 ±547 / -343 ±585 / -1,125 ±540 |
| Depth 6 / root prior weight 0.5 | v43 | -229 ±591 / -527 ±515 |
| Widening 1.5 (v44) | v43 | +1,285 ±499 |
| Widening 1.0 / 1.25 / ds32 to depth 2 | v44 | -469 ±451 / -48 ±473 / +602 ±509 |
| **Sequential halving, 16 root actions** | v43 | **+4,280 ±512** |
| Halving with 8 / 32 actions / with the prior in the ranking | v43 | +1,822 ±536 / +1,411 ±584 / -1,032 ±493 |
| Halving 16 with widening 1.5 (v45) | v44 | +2,369 ±547 |
| Halving 16 with widening 2 against widening 1.5 | v45 | +626 ±604 |

Against v37 on these seeds, v45's setup is +6,896 ±512 and halving with widening 2 is +7,522 ±574.
Other nets in place of ds32 all lost: 24 channels -737 ±741, 48 channels -1,677 ±683, a copy of the
plan-start PPO net -423 ±618 (120 games each). A prior at depth-1 nodes lost 462 ±731.

On the server, v45 averaged 64,831 ±1,752 over 42 volcanic games, with 10 at 70k+, 2 at 90k+ and a best of 108,536.

### Around v45 (halving 16, widening 1.5), 180 fresh paired games

| Change | Result |
|---|---|
| Halving with 12 / 20 actions | +158 ±554 / +680 ±567 |
| ds32 to depth 2 | -4,155 ±496 |
| Exploration 0.5 | +298 ±479 |
| Value weight 0.3 | +485 ±553 |
| Depth 6 | +174 ±603 |

None of these is clearly better, so v45 stays as it is. Using the net two levels deep, which was level before halving, now loses badly.

Training ds32 for 60 minutes instead of 20 raised top-move agreement from 76% to 77% but gained only +149 ±546 in the search (180 games), so the 20-minute net stays.

## Gumbel follow-ups, 2026-11-07

Halving at the root was the night's biggest gain, so the day went to work that builds on Gumbel
MuZero and AlphaZero.

### Sources

- **Variance reduction in MCTS** ([Veness, Lanctot and Bowling, NIPS 2011](https://proceedings.neurips.cc/paper_files/paper/2011/file/d736bb10d83a904aefc1d6ce93dc54b8-Paper.pdf)). Common random numbers, antithetic variates and control variates, tested on stochastic single-player games (Pig, Can't Stop, Dominion). It led to the one gain of the day: giving every root action the same dice.
- **H-MCTS and SHOT** ([Pepels, Cazenave, Winands and Lanctot, 2014](https://www.lamsade.dauphine.fr/~cazenave/papers/PepelsCGW2014.pdf)). Sequential halving recursively near the root, and UCT below it. In partially observable games ([Pepels et al., 2016](https://cris.maastrichtuniversity.nl/en/publications/sequential-halving-for-partially-observable-games)), flat halving at the root was best in Phantom Go and H-MCTS in Go Fish. Not tried: our depth-1 nodes get about 9-140 visits, and narrower trees have always lost here.
- **Anytime sequential halving** ([Sagers, Winands and Soemers, 2024](https://arxiv.org/abs/2411.07171)). Halving without a known budget. We size rounds from the last tick's iteration count, which is almost constant locally (about 460 or 595 iterations). It could matter on the server, where stalls cut the search short.
- **Twice Sequential Monte Carlo Tree Search** ([2025](https://arxiv.org/abs/2511.14220)). Halving at the root over particle searches, which beat Gumbel MCTS at about 24 simulations on Jumanji and Brax. Our budget is about 500 iterations, so the tree below the root is not the weak point.
- **Boltzmann tree search, DENTS** ([Painter et al., NeurIPS 2023](https://papers.neurips.cc/paper_files/paper/2023/file/f670ef96387d9a5a8a51e2ed80cb148d-Paper-Conference.pdf)). Boltzmann exploration in place of UCT. It mainly helps exploration under sparse rewards, which isn't our problem.
- **MiniZero** ([Wu et al., 2023](https://arxiv.org/abs/2310.11305)) and **Gumbel search for LLM reasoning** ([2026](https://arxiv.org/abs/2603.21162)). Both find Gumbel root search clearly better than PUCT at small budgets, and close at large ones.
- **GAZ Play-to-Plan** ([Pirnay et al., ICLR 2023](https://arxiv.org/abs/2306.04403)). Single-player Gumbel AlphaZero that plans against its past policy. It's a training method for deterministic puzzles.
- **Learned planning budgets** ([Muppidi et al., 2026](https://arxiv.org/abs/2606.26463)). A small PPO gate picks how long an AlphaZero planner thinks at each step. The server's per-tick limit leaves us no time to bank, so this is closest to the phase budget idea.
- **KataGo** ([Wu, 2019](https://arxiv.org/abs/1902.10565)). Forced playouts, policy target pruning and FPU reduction are mostly training-side.

### What we measured

The search now runs about 460-595 iterations per tick (logged as the 7th CSV column). Over 612 searched ticks
of one game, the chosen action was the policy's top action only 24% of the time, and the most-visited one
86% of the time.

Paired local tests, 180 games on seeds 9000+ (60 per biome), against v45 unless noted:

| Change | Result |
|---|---|
| **CRN: the k-th visit of every root action draws the same dice** | **+1,473 ±523** |
| CRN on fresh seeds 10000+, against v45 there | **+1,155 ±563**, 13 games at 80k+ against 7 |
| Halving ranks by mean + 0.1 / 0.3 of (best - mean) | +550 ±564 / -920 ±611 |

Against v45 with CRN (v46):

| Change | Result |
|---|---|
| Keep the played action's subtree, below the new root's actions | -731 ±562 |
| The same, also counting its visits at the root | -1,731 ±552 |
| ds32 fine-tuned on the search's choices and visits (ex1), everywhere / in the tree only | -623 ±587 / -24 ±570 |
| ds32 fine-tuned on the search's choices, closer to the teacher (ex2) | -603 ±601 |
| 24 halving actions | -423 ±623, best 116,176, 14 games at 80k+ against 10 |
| Halving ranks by mean + 0.1 of (best - mean) | -1,276 ±573 |
| ds32 only on depth-1 nodes with 12 / 6 visits | -1,547 ±555 / -997 ±586 |
| Value weight 0.3 / 0.5 | +28 ±590 / +110 ±543 |
| ds32 fine-tuned towards the teacher only, at search positions (ex3) | -763 ±592 |
| 12 / 20 / 32 halving actions | -1,583 ±543 / +710 ±571 / -2,123 ±604 |
| 20 / 24 halving actions on fresh seeds 10000+ | -570 ±613 / +349 ±618 |
| Halving budget front-loaded (skew 0.5) / back-loaded (skew -0.3) | -473 ±544 / -1,441 ±530 |
| Widening 2 / depth 6 | -956 ±564 / -1,189 ±583 |

Notes:

- **CRN** pairs the root actions: with sequential halving, each action's k-th visit replays the same
  guess at the hidden dice, so a comparison isn't swamped by dice luck. It costs nothing.
- **Tree reuse** loses because last tick's statistics average over dice outcomes that didn't happen,
  and they crowd out fresh visits.
- **Expert iteration** (fine-tuning ds32 towards the search's decisions, as in AlphaZero) used 300
  search games on seeds 20000+ (`runs/dumps/crn1`, 184k positions, `tools/search_finetune.py`). The
  search's choice is hard to predict: agreement rose only from 22% to 25%, and the net drifted from the
  teacher. This matches the earlier finding that imitating MCTS visits plays badly.
- **Ranking every depth-1 node is worth its cost**, even though it takes about a fifth of the budget.

## Night of 2026-11-08

### Sources

- **2048 AIs** ([Guei et al., optimistic TD learning, 2021](https://arxiv.org/abs/2111.11090); [follow-up, 2022](https://arxiv.org/abs/2212.11087)). The closest well-studied game to ours: single player, high score, random spawns. The best agents pair a shallow expectimax with a strong value function learned by TD on n-tuple features, and gains level off at about 5 plies. The lesson for us is that the value function matters more than search tweaks, but our value nets have failed twice before.
- **GVGAI** ([two-player competition, 2016](https://cris.maastrichtuniversity.nl/en/publications/the-2016-two-player-gvgai-competition); [rolling horizon analysis](https://arxiv.org/abs/1704.07075)). Real-time, about 40 ms per move, stochastic games. Open-loop MCTS won most often, and rolling horizon evolution was second. We already run open-loop MCTS.
- **Kaggle simulation competitions** ([Hungry Geese 3rd place](https://speakerdeck.com/hoxomaxwell/kaggle-hungry-geese)). MCTS with a ResNet policy/value net and hand-built features. Nothing beyond what we do.
- **Ensemble determinization with dynamic allocation** ([Kowalski et al., 2026](https://arxiv.org/abs/2607.13007)). +2-5 points over uniform ensembles at 250,000 simulations per move. Our budget is about 500, and several dice guesses voting already lost here.

### Speed

Profiled with timers around the search's parts (one game, local build without AVX2): game steps about 55%,
value net about 30%, tree policy net about 12%. Inside a step, dinosaur moves are 40% and lava 13%.

| Change | Speed | Results |
|---|---|---|
| **Value net loops with compile-time sizes (12, 16, 32)** | **11.5% faster locally, +14% simulated per tick on the server** | Identical on 180 games |
| Skip lava spreading when nothing changed | 6% slower: it rarely applies and the bookkeeping blocks vectorization | Identical on 180 games and the engine check |
| Branch-free passes for lava-flow danger | No faster | |

The value net change is v47 (uploaded 01:20). On the server the median simulated count per tick rose from
8,601 (v46, 82 games) to 9,753 (v47, 55 games), which is about budget 8,300 in local units after the 1,500
root charge.

### Where the search goes wrong

`sim_live --oracle-budget 57920 --oracle-arms 48` runs an 8x search with 48 halving actions every few ticks.
On 193 positions it agreed with v46 47% of the time, and its choice was outside v46's 16 halving actions 30% of
the time (ranks 16-31: 20%, 32-47: 10%).

So we fine-tuned ds32 on the big search's choices and visits (`--oracle-dump`, 59,000 positions from 400 games,
`tools/search_finetune.py --arms 48`). The fine-tuned net's top 16 held the big search's choice 76-78% of the
time against 72% for ds32, yet it played worse:

| Net, mixed biomes, against v46 | Result |
|---|---|
| or1 everywhere | -1,148 ±509 |
| or1 at the root only | -2,053 ±505 |
| or1 in the tree only | +7 ±594 |
| or3 (kept closer to the teacher) in the tree only | +51 ±657 |

Moves that an 8x search likes aren't good candidates for a search with an eighth of the budget, which can't
follow them up. The candidate list is better served by the PPO net's plain preferences.

### Testing on `volcanic_plains` only

Until tonight, tests played 60 games on each biome, but the bot only plays `volcanic_plains` on the server.
Per biome, 60 games give a standard error of about 1,300, too noisy to decide anything. Tests now play 180
`volcanic_plains` games (`runs/tests/v1.sh`), with a standard error of about 750. At budget 8,300 the baseline
averages 68,590, close to v47's server mean.

Against v47's settings at budget 8,300 on 180 `volcanic_plains` games (seeds 9000+):

| Change | Result |
|---|---|
| 20 / 24 halving actions | -1,999 ±823 / -1,163 ±861 |
| Value weight 0.5 | -1,045 ±858 |
| Widening 2 | -685 ±847 |
| 4 / 2 volcanoes offered | -74 ±854 (best 155,051, on the seed where v47 scored 120,841) / -687 ±750 |
| Volcano ranking over 45 ticks | -354 ±495 |
| Exploration 0.25 / 0.5 | -1,720 ±776 / -1,057 ±808 |
| Radius 3 | -235 ±846 |
| 8 pocket targets / pocket limit 90 | -310 ±557 / +157 ±743 |
| Horizon 5 | -1,583 ±805 |
| Flood weight 0.05 / 0.15 | -384 ±515 / -174 ±567 |
| Pocket weight 0.1 / 0.3 | -1,097 ±658 / -149 ±614 |
| Volcano planning | -157 ±210 (153 of 180 games identical) |
| ds32 on depth-2 nodes after 8 / 24 / 60 visits (mixed biomes, budget 7,240) | +87 ±511 / +35 ±531 / +214 ±558 |

None beats v47, on the mean or in the tail: v47 had 6 games of 180 at 100k+ and 3 at 110k+, more than any
variant.

**What makes a high score:** across those 180 games, the final score correlates +0.83 with the share of ticks
where the spawn area is 60 tiles or fewer. The top 10% of games have such a pocket 12% of the time, against 5%
for the middle. The pocket and flood options above rarely change play, because a triggered volcano stays
active until it scrolls off, so the volcano decision seldom comes up.
