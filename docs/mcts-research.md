# MCTS and value-network research notes

Notes for improving `search_bot`, based on the AlphaGo/AlphaZero family of
methods. The paper summaries below are short and from memory; check the cited
papers before relying on a specific hyperparameter.

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

## What the methods do

- **AlphaGo** (Silver et al., Nature 2016): a policy net gives priors for PUCT
  selection, and leaf evaluation mixes a value net with fast rollouts. Training
  the value net on many positions from the same game made it overfit, so
  positions were sampled one per game.
- **AlphaGo Zero / AlphaZero** (Nature 2017; arXiv 1712.01815): a single net
  outputs a policy and a value. Its targets are the root visit distribution and
  the game outcome, taken from a replay window of self-play games.
- **MuZero** (arXiv 1911.08265): n-step bootstrapped value targets, min-max
  normalization of in-tree Q for unbounded rewards, and a value transform for
  heavy-tailed returns.
- **KataGo** (arXiv 1902.10565): playout cap randomization yields more value
  samples for the same compute, and dense auxiliary targets speed up learning.
- **Gumbel AlphaZero** (ICLR 2022): sequential halving at the root plus
  completed-Q policy targets. It improves the policy even with few simulations.
- **Expert Iteration** (arXiv 1705.08439): the search teaches a net, and the
  net in turn guides the search. Training on the tree policy works better than
  training only on the chosen move.
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
