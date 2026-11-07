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
