# MCTS and value-network research notes

Notes for improving `search_bot`, based on the AlphaGo/AlphaZero family of
methods. These are high-level takeaways; validate any adaptation to this
single-player, partially observable game with controlled local benchmarks.

## Baseline

Live bot settings (`radius 2, horizon 4, volcanoes 6, depth 5, widening 3,
exploration 0.5`) at the server-equivalent budget of 6,500 simulated ticks per
move (~68k ticks/s, so ~95 ms):

```bash
build/sim_live.exe 4 --mcts 1 --radius 2 --horizon 4 --volcanoes 6 --depth 5 \
  --widening 3 --exploration 0.5 --budget 6500 --threads 7
```

| Biome | Mean score |
|---|---:|
| deep_crevasse | 49,371 |
| mountain_range | 40,345 |
| volcanic_plains | 43,276 |
| **Overall** | **44,331** |

Three random launcher games at 150 ms per tick scored 40,318, 47,364 and
43,701, with no timeouts.

## First search-tuning iteration

Seven alternatives were compared against the baseline using the same seeds
(`live-0` through `live-3` in each biome), MCTS enabled, and 6,500 simulated
ticks per move. The four-seed-per-biome cohort is small, so close differences
are inconclusive; none of the tested alternatives beat the baseline overall.

| Change | Overall | deep_crevasse | mountain_range | volcanic_plains |
|---|---:|---:|---:|---:|
| baseline (depth 5, widening 3, exploration 0.5) | **44,331** | 49,371 | 40,345 | 43,276 |
| exploration 0.3 | 43,947 | 48,804 | 39,965 | 43,073 |
| exploration 0.8 | 42,036 | 46,460 | 39,417 | 40,230 |
| exploration 1.2 | 44,090 | 48,590 | 41,867 | 41,814 |
| widening 2 | 42,103 | 48,766 | 37,417 | 40,127 |
| widening 4 | 43,941 | 49,639 | 40,343 | 41,841 |
| depth 4 | 43,721 | 49,777 | 40,014 | 41,373 |
| depth 7 | 42,566 | 48,923 | 37,619 | 41,157 |

Decision: retain the baseline settings. Exploration 1.2 and widening 4 are
close enough that this small sample cannot rule them out; test on a larger,
fresh paired cohort before drawing conclusions. The launcher integration test
was not run because no launcher is present in `localRunner/`.

## First value-network iteration

Generated simulator-only data with the baseline agent: 180 games (60 per
biome), sampled every 5 ticks. Training used 28,998 positions; 3,222 positions
from 18 whole held-out games were reserved for validation. The 16,293-parameter
network reached validation R² 0.239 for its 100-tick score target, versus
R² -0.002 for a quadratic tick-only predictor. This is a useful signal, but
not by itself evidence that using the net improves action selection.

The exported net's C++ inference matched the PyTorch outputs with a worst
absolute difference of 1.4e-6, at about 27 microseconds per evaluation.

All score comparisons used the baseline MCTS settings and 6,500 simulated
ticks per move on paired seeds. On the initial `live-0..3` cohort, overall
means were 44,331 for baseline, 44,504 at value weight 0.25, 46,430 at 0.5,
and 42,658 at 1.0. Weight 0.5 was then tested on fresh seeds `live-4..11`:

| Biome | Baseline mean | Net weight 0.5 mean | Delta | Paired t | Net wins |
|---|---:|---:|---:|---:|---:|
| deep_crevasse | 50,394 | 51,982 | +3.2% | +1.07 | 5/8 |
| mountain_range | 39,498 | 36,802 | -6.8% | -2.44 | 1/8 |
| volcanic_plains | 42,830 | 46,416 | +8.4% | +1.27 | 6/8 |
| **Overall** | **44,240** | **45,066** | **+1.9%** | **+0.68** | **12/24** |

Decision: do not integrate this model into the live bot. The modest overall
lift is not reliable on 24 paired games, and the mountain-range regression is
concerning. Evidence is limited to one training run and a small dataset; further
work should gather more varied data and validate on a larger paired cohort.
The launcher test was not run because its binary is unavailable in
`localRunner/`.

## What the methods do

- **AlphaGo** (Silver et al., Nature 2016): a policy net gives priors for PUCT
  selection, and leaf evaluation mixes a value net with fast rollouts. Training
  the value net on many positions from the same game made it overfit, so
  positions were sampled one per game.
- **AlphaGo Zero / AlphaZero** ([paper](https://arxiv.org/abs/1712.01815)): a
  single net outputs a policy and a value. Its targets are the root visit
  distribution and the game outcome, taken from a replay window of self-play
  games.
- **MuZero** ([paper](https://arxiv.org/abs/1911.08265)): n-step bootstrapped
  value targets, min-max normalization of in-tree Q for unbounded rewards, and
  a value transform for heavy-tailed returns.
- **KataGo** ([paper](https://arxiv.org/abs/1902.10565), [methods notes](https://github.com/lightvector/KataGo/blob/master/docs/KataGoMethods.md)):
  playout cap randomization yields more value samples for the same compute, and
  dense auxiliary targets speed up learning.
- **Gumbel AlphaZero** (ICLR 2022): sequential halving at the root plus
  completed-Q policy targets. It improves the policy even with few simulations.
- **Expert Iteration** ([paper](https://arxiv.org/abs/1705.08439)): the search
  teaches a net, and the net in turn guides the search. Training on the tree
  policy works better than training only on the chosen move.
- **Hidden information**: open-loop MCTS (already used here) and re-sampling
  the hidden state on each iteration avoid over-optimism from planning against
  one fixed future.

## Recommendations for this bot

1. **Leaf value.** Keep the exact 4-tick no-op rollout, which resolves every
   in-flight meteor. Then add `γ^d · V(leaf)`, where V predicts future points
   only.
2. **Value target.** Use a discounted return (γ ≈ 0.98 per tick) truncated at
   game end, with ticks remaining as an input. Later generations can use n-step
   bootstrapped targets. Use a Huber loss or a value transform because kill
   rewards are heavy-tailed.
3. **UCB normalization.** Normalize Q with per-search min-max, then tune the
   exploration constant on fixed seeds.
4. **Policy prior.** Use it to order progressive widening rather than for full
   PUCT. A dense per-cell "points from an impact here" target is cheap to
   produce with the simulator. Always keep no-op and volcano actions available.
5. **Expert iteration.** Generate data with the current bot, split by seed,
   keep a replay window, and gate each new net on paired fixed seeds against
   the previous bot.
6. **Cost.** Inference time comes out of the search budget, so keep the net
   small.
7. **Pitfalls.** Watch for overfitting to the bot's own state distribution,
   leaking hidden information into features, and γ or horizon mismatch between
   the training target and the tree.

## Validation loop

Compare candidates with `tools/benchmark_value_net.py` or `sim_live` on the
same fixed seeds. Confirm the winner with `tools/benchmark_bot.py` at the real
150 ms tick budget before packaging.
