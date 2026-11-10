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
8,601 (v46, 82 games) to 9,675 (v47, 102 games), which is about budget 8,300 in local units after the 1,500
root charge.

Over the night v47 played 110 `volcanic_plains` games on the server: mean 67,620 ±1,120, best 119,353
(129 short of v46's 119,482), 3 games at 100k+. That matches v46 (67,999 ±1,337 on 82 games), so the extra
search shows no gain on the server either, and the best score still comes down to a lucky map.

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

## Good seeds and ablation, 2026-11-08

### How much is the seed?

On the 180 `volcanic_plains` seeds played by 19 near-identical variants of v47, the seed explains about half of the
score variance (sd 8,100 between seed means, 7,600 within a seed). Replays of the 10 best seeds, 20 each with the
budget jittered between 8,205 and 8,395 to stand in for server timing:

| Seed | Mean | Best |
|---|---|---|
| live-9169 | 118,880 | **161,788** |
| The 9 others | 81,000-103,000 | 100,000-121,000 |
| All 200 games | 92,348 | 5 games at 140k+, 8 at 120k+ |

With the dice changed and the map kept (`--rng-tag`), live-9169 still averages 97,530, so its map is what makes it
good.

### Replaying server games

Each downloaded `game.log` prints the game's seed as a replay command (`--randomSeed <uuid>`). `sim_live
--seed-list file` plays those seeds. This only replays finished games offline; the live bot never sees a seed.

The 237 `volcanic_plains` server games from v45-v47 replay at 68,455 against 66,873 on the server, but the two
scores correlate only 0.41. The two 119k records replay at 85,436 and 56,827. A record is a decent seed plus a lucky
run of play.

A generalized Pareto fit to the 27 server games above 80k puts 160k at about 1 game in 57,000 `volcanic_plains`
games, and 204k at about 1 in 450 million. The fit is rough, but more games won't get there.

### What a good game looks like

| Games | Kills per game | Points per kill | Share of points from ticks with 5+ kills |
|---|---|---|---|
| live-9169 (20 replays) | 506 | 235 | 33% |
| The 9 other good seeds | 450 | 198 | 14% |
| The 237 server seeds | 396 | 173 | 6% |

On server seeds, ticks with 1-2 kills give 67% of the points. The bot already kills 97% of what spawns, so only
points per kill can grow. 204,657 needs about 500 per kill, which means many ticks of 10-20 kills
(`(1 + 0.5 (kills - 1))` multiplier, 20 alive at most). That takes setups over tens of ticks, which a 4-tick horizon
doesn't see.

### Ablation

One part of v47 removed or changed per run, 180 `volcanic_plains` games at budget 8,300, paired with v47:

| Change | Result |
|---|---|
| Depth 1 | -25,676 ±827 |
| No policy nets (root and tree) | -21,474 ±827 |
| No value net | -11,340 ±883 |
| No wave planning (`--plan-waves 0`) | -7,668 ±760 |
| No sequential halving | -6,137 ±810 |
| No tree policy net | -5,396 ±764 |
| Depth 3 | -2,759 ±878 |
| No common random numbers | -1,516 ±835 |
| No volcano actions offered | -1,280 ±780 |
| No volcano ranking | -412 ±592 |
| Half budget (4,150) | -4,844 ±764 |
| **Double budget (16,600)** | **+1,947 ±786** |
| **Four times the budget (33,200)** | **+2,506 ±810** |
| True future rows | -4,914 ±809 |
| True dice | -4,266 ±744 |
| True wave timing | -812 ±790 |
| All three true | +429 ±814 (best 148,376) |

- Every part helps, and the nets help most.
- Search has nearly levelled off: each doubling gains less (+4,844, +1,947, +559). A machine four times faster would
  add about 2,500, not the 90,000 that 160k needs.
- Revealing the hidden information loses on its own. The nets were trained on the hidden view, and halving's common
  random numbers assume sampled dice, so these runs don't measure what perfect prediction is worth. They do say it
  isn't a free win.

**Conclusion:** more training data or a faster search won't reach 160k-204k. The gap is in the plan: building
large multi-kills over many ticks.

## Herding and value nets, night of 2026-11-08

All tests are paired with v47 at budget 8,300 on `volcanic_plains`: 180 games on seeds 9000+, or 360 fresh games on
seeds 20000+. The fresh games' v47 reference is `runs/tests/d_v47a`, the 2,000 data-generation games, which play
exactly as v47. New `sim_live` options, all off by default: `--rollout-fire`, `--group-weight`, `--data`, `--wait`
and `--net2`. The log now also has each tick's spawn tiles, the best possible cover of them, and what the landing
meteor covered.

### Where big kills come from

Ticks with 5+ kills are mostly spawn ticks, when dinosaurs are age 0 and can't flee. The good seed live-9169 has 6.7
such ticks per game (24% of its points); server seeds have 0.7 (3.8%). Kills of live dinosaurs in groups are rare
because they see every footprint and have 4 moves to leave a 13-tile disc.

- When the spawn area is small, a meteor lands on 66-77% of spawn ticks and covers 75-85% of the best possible
  cover. In areas of 13 tiles or fewer the best disc covers only about 6, because pockets are strips.
- Pockets (spawn area under 40) are 2.6% of ticks, in about 3.7 episodes per game of 4 ticks (median). 5.5% of waves
  spawn in one, so the bot already times waves into them.
- Volcano choices barely change pocket time: 2.38% with no volcano actions, 2.62% in v47.

So aim and timing at spawn are near their limit, and pockets come from the terrain.

### Herding ideas

| Change | Games | Result |
|---|---|---|
| Rollouts fire at the best footprint when it covers 2+ dinosaurs, horizon 6 / 8 / 10 | 180 | +151 ±803 / +168 ±953 / -978 ±825 |
| The same, horizon 8, 1+ / 3+ dinosaurs | 180 | -193 ±820 / +189 ±844 |
| Leaf adds 0.05 / 0.15 / 0.40 of the best footprint's points (group potential) | 180 | +394 ±775 / +1,298 ±881 / -270 ±789 |
| Group potential 0.15, horizon 8, rollouts fire at 2+ | 180 | +735 ±760 |
| Group potential 0.10 / 0.15 / 0.25 | 360 fresh | +160 ±616 / +692 ±598 / +126 ±565 |

None changes the share of points from ticks with 5+ kills (about 5-6%). Group potential is at best a few hundred
points, within noise, so it wasn't shipped. Its rate of 100k+ games matched v47's on the same seeds (12 against 9).
Strike plans (fence plus kill) weren't built, because the spawn analysis shows little to gain from live groups.

### Value nets

2,000 v47 games gave 448,000 leaf-shaped positions (`runs/data/v47a`, 2.7 GB). Their 100-tick targets average 6.8k
with sd 3.2k, against 3.9k and 1.2k for gen2's data. Every new net lost to gen2, at the same 16,293-parameter size:

| Net | Data | R2 | Weight | Result (360 fresh) |
|---|---|---|---|---|
| gen5 | v47 games | 0.19 | 0.23 / 0.40 | -2,308 ±605 / -4,548 ±685 |
| gen5m | v47 + gen2 data | 0.35 (mixed biomes) | 0.31 | -1,519 ±658 |
| gen5w | v47, 200-tick window | 0.09 | 0.115 | -4,854 ±650 |
| gen5g | v47, +100 per kill in 4+ kill ticks | 0.18 | not tested | |
| gen2b | gen2 data only, retrained | 0.26 | 0.40 | -1,937 ±608 |
| **gen2c** | **gen1 + gen2 data, as gen2, new initialization** | 0.24 | 0.40 | **-2,058 ±620** |
| gen2 + gen2c averaged | | | 0.40, net charged twice | -1,583 ±631 |

gen2c is the key result: the same data, script and size as gen2 lose about 2,000. So either gen2 is a lucky draw, or
v47's settings have been tuned around gen2 over 40 versions. That would also explain why gen3 and gen4 lost. Value
net retraining is not a way forward unless the search is retuned with the new net.

### Conclusion

Neither herding terms nor retrained value nets beat v47. Big kills need age-0 dinosaurs in small spawn areas, which the
bot already exploits close to the limit of what it can see. v47 stays the server build.

## Bootstrapped value nets and terrain inference, 2026-11-09

All tests are paired with v47 at budget 8,300 on `volcanic_plains`, 360 games per row. Seed sets: A = 20000-20359,
B = 20360-20719, C = 20720-21079, all against `runs/tests/d_v47a`. Nets were picked on A, so B and C are the honest
checks.

### Bootstrapped value targets

The value net's usual target, points over the next 100 ticks, has sd 3.2k on v47 games, mostly luck at spawn.
`tools/train_value_boot.py` replaces everything after N ticks with a teacher net's estimate: target = real points over
N ticks + teacher(position at N) x (100 - N) / 100. With gen2 as teacher and its weights as the starting point:

| Net | Recipe | A | B | C |
|---|---|---|---|---|
| b20 | N = 20, from scratch | -311 ±636 | | |
| b40 | N = 40, from scratch | +434 ±705 | | |
| b20i (v48) | N = 20, from gen2, 10 epochs | +1,636 ±701 | +1,330 ±601 | +2,041 ±619 |
| b40i | N = 40, from gen2, 10 epochs | +1,774 ±704 | | |
| b20i2 / b20i3 | teacher = the previous one, 10 epochs each | +1,876 / +2,780 | +1,406 / | / +2,470 |
| **b20i30 (v49)** | **N = 20, from gen2, 30 epochs** | **+2,665 ±705** | **+2,122 ±580** | **+3,010 ±650** |
| b30x2 | teacher and start = b20i30, 30 epochs | +2,890 ±715 | | +2,661 ±648 |
| b20i60 | N = 20, from gen2, 60 epochs | | +2,962 ±594 | +3,740 ±646 |

- Starting from gen2's weights is what makes this work. Every net trained from scratch, on any target, has lost to gen2,
  including one on gen2's own data (-2,058). The search's settings are fitted to gen2, and fine-tuning keeps that fit.
- b20i30 beats b20i on all three sets (+1,029, +792, +969). Another round on top of it (b30x2) doesn't add more
  (-348 ±634 against b20i30 on C).
- Same size and speed as gen2, so it costs no search time. Validation R2 on the bootstrapped target is about 0.59;
  on raw 100-tick returns it is negative because gen2's scale is about 40% below v47's real rate. That doesn't matter
  for ranking leaves.

### Terrain inference

The terrain is two layers of 2-octave Perlin noise, and both octaves read the same lattice of gradient indices.
`sim/terrain_infer.cpp` keeps every assignment of gradient indices consistent with the rows seen so far (column by
column, per layer, then joined exactly) and predicts upcoming rows. Offline over 10 seeds, with the true gradients always
among the hypotheses:

| Row ahead | Heights right | Copy last row | Flags right | Copy last row |
|---|---|---|---|---|
| +1 | 80% | 39% | 97% | 89% |
| +2 | 65% | 21% | 95% | 85% |
| +8 | 34% | 12% | 91% | 82% |

Uncertainty right after a lattice line is real: the next lattice row's gradients aren't seen yet. In play:

| Change | Result |
|---|---|
| True future rows (`--reveal-rows`, fixed to keep wave planning on) | +1,972 ±674 |
| Search from the true game, nothing revealed (control) | identical to v47 in 360/360 |
| True rows for only the next 2 rows (`--forecast-truth 2`) | +1,966 ±674 |
| Inferred rows, majority, 3 / 8 rows ahead | +61 ±636 / -459 ±684 |
| Inferred rows, each search iteration plays its own posterior draw | +960 ±679 (gen2, A); over b20i30: +2,513 ±749 (B), +812 ±701 (C) |

All the value of true terrain is in the next two rows, the ones inside the search's reach. A single best guess at
them is worth nothing; sampling a guess per iteration, as the search already does for the dice, recovers much of it:
about +1,600 ±510 on top of b20i30 over sets B and C. b20i60 adds about +790 ±450 over b20i30 on the same sets.

The earlier ablation's "true rows -4,914" was an artefact: hiding the waves in the reveal path turned wave planning
off. With it kept on, true rows are worth about +2,000.

## v50 and v51, night of 2026-11-09

All tests are 360 paired `volcanic_plains` games at budget 8,300. The value net training data
(`runs/data/v47a`) came from seeds 20000-21999, which overlap test sets A-D, so tonight's
confirmations also use sets E (40000+) and F (40360+), outside all training data.

### v50: shipping terrain draws

- b20i60 plus sampled terrain rows: **+4,923 ±677** over v47 on set D (21080+), about the sum of
  the separate gains (+2,570, +790, +1,600).
- **Latency.** Inference averaged 1.3 ms per tick, but 10% of games had a tick over 95 ms (worst
  275 ms), because one extension (`extend_one`) could take 75 ms before the per-tick cap was
  checked. The per-layer search and the join are now resumable (`extend_step`): they stop at the
  deadline and continue next tick. Draws are cached by (hypothesis version, last row, first row
  drawn), and the act() deadline starts before inference, so its time comes out of the tick.
  Worst tick fell to 18 ms over 22 games with 11 threads. Offline accuracy with 32 ms per row
  (about 4.5 ticks per row at 8 ms) matches uncapped inference. When sampling, the majority vote
  is skipped and only 3 rows are drawn.
- The rebuilt v50: **+4,764 ±707** over v47 on set D.
- Server: 108 games, mean 70,476, best 117,588, 7 late ticks.

### Tuning on top of v50

| Change | Set D | Set E | Set F |
|---|---|---|---|
| b20i100 (100 epochs) | -189 ±705 | | |
| m20i60: gen2 targets on v47 + v50 games | +581 ±777 | +467 ±693 | |
| c20i30: b20i60 teacher on 2,000 v50 games | +955 ±746 | +289 ±751 | |
| 16 terrain draws instead of 8 | | -289 ±657 | |
| Depth 6 | | +197 ±685 | |
| Value weight 0.3 / 0.5 / 0.6 | -1,009 / +291 / +270 | | |
| **Horizon 5** | **+1,021 ±685** | **+932 ±724** | **+1,225 ±682** |
| Horizon 6 | | +468 ±739 | |

With horizon 5 as the base: c20i30 is -647, -402 and +243 on sets D, E and F, and depth 4 is
-1,318 ±690 on set E. Exploration 0.45 is -1,722 ±635 and 0.25 is -131 ±679 on set E. Widening 1.25 is -1,341 ±713 and 2 is +4 ±716.
v51 is v50 with horizon 5.
