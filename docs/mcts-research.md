# MCTS and value-network research notes

Notes for improving `search_bot`, based on the AlphaGo/AlphaZero family of
methods. These are high-level takeaways; validate any adaptation to this
single-player, partially observable game with controlled local benchmarks.

## Running these examples on Windows

Historical command blocks below use Bash continuations (`\`). In PowerShell,
put the command on one line (or use PowerShell's backtick continuation).
Use `./build/sim_live.exe` on Windows in place of `build/sim_live_linux`;
the original `build/sim_live.exe` baseline example refers to the same native
tool. On Linux, use `build/sim_live_linux` throughout. Build host tools with
`uv run --no-project python tools/build_sim.py`; Python benchmark tools choose
the appropriate executable automatically. Keep forward slashes in paths.

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

## Target-generation experiments at the local deadline

The `feature/better-targets` experiment combines meteor candidates around
predicted dinosaur positions with volcano candidates ranked by nearest dinosaur.
At 6,500 simulated ticks per move, 10 paired seeds per biome gave overall means
of approximately 41,520 baseline and 43,887 combined (+5.7%). That fixed-work
result did not transfer to the launcher's 95 ms search deadline: on `live-26`,
combined targeting lost in all three biomes.

To reduce overhead, mountain distances are now computed once per candidate
instead of in the sorting comparator. Meteor priority is also precomputed per
cell, preserving the previous stable ordering. MCTS prediction is restricted
to the root; deeper nodes retain current-position targets.

Launcher results on `bench-26`, one game per biome and variant, with the
precomputed sorting in every variant:

| Variant | deep_crevasse | mountain_range | volcanic_plains | Overall mean |
|---|---:|---:|---:|---:|
| Current-position baseline | 57,932 | 41,783 | 49,606 | 49,774 |
| Prediction at every node + nearby volcanoes | 55,633 | 37,764 | 45,740 | 46,379 |
| Root-only prediction + nearby volcanoes | 50,309 | 47,084 | 52,210 | 49,868 |

All nine games finished without late ticks or reported bot errors; maximum
reported move times were 95.3-95.7 ms. Root-only prediction recovers the lost
throughput in deep crevasse (median simulated work 25,726 versus 23,974 for
every-node prediction), but its score is still mixed across biomes. Wall-clock
search is not deterministic even with fixed map seeds. A single seed per biome
and a +0.2% overall difference are not evidence of a reliable improvement.

Decision: retain baseline defaults. Set `BOT_AIM_PREDICTED=1` and
`BOT_VOLCANO_NEAR=1` to test the root-only combined variant with
`tools/benchmark_bot.py`. The 200,000-point target remains unmet.
The integrated simulator matched every tick of 12 launcher games, including
the boundary/cap action probes from `rich/dev`.

The remote value-network variant was also screened with combined targeting:
on four paired seeds per biome it scored 43,008 overall versus 44,559 without
the net (weight 0.3). It remains unintegrated.

## Tuning at the measured Linux capacity

The local 95 ms bot typically simulates 20,000-25,000 ticks per move, rather
than the 6,500 used in the original tuning. The following screening used
22,000 simulated ticks, six paired seeds per biome (`live-40..45`), and the
baseline settings unless shown otherwise:

| Variant | deep_crevasse | mountain_range | volcanic_plains | Overall mean |
|---|---:|---:|---:|---:|
| Baseline | 54,875 | 45,273 | 46,928 | 49,025 |
| Depth 10 | 57,021 | 42,376 | 47,411 | 48,936 |
| Depth 16, widening 2 | 55,584 | 41,502 | 45,824 | 47,637 |
| 1,000-point wave-clear bonus | 56,314 | 43,521 | 50,486 | 50,107 |
| 3,000-point wave-clear bonus | 55,440 | 44,195 | 45,297 | 48,311 |
| Optimism 0.25 | 57,391 | 45,426 | 48,401 | 50,406 |

Deeper search alone did not help. The wave-clear bonus tried to account for
the next wave, which planning copies otherwise omit. Its 1,000-point variant
failed on fresh seeds `live-60..69`: mean 48,194 versus baseline 48,596.
The temporary clear-bonus code was removed.

Optimistic MCTS blends each visited edge's best observed return into its mean
before the usual UCB normalization:
`value = mean + optimism * (best - mean)`. The default weight is zero, retaining
the existing policy. A weight of 0.25 favors lines with a strong coordinated
continuation instead of judging them only by mostly unsuccessful continuations.
It can also overvalue lucky hidden-RNG samples, so local deadline results remain
the deciding evidence.

On the 30 fresh paired simulator games (`live-60..69`):

| Biome | Baseline mean | Optimism 0.25 mean | Delta | Paired wins |
|---|---:|---:|---:|---:|
| deep_crevasse | 54,062 | 55,827 | +3.3% | 5/10 |
| mountain_range | 43,031 | 46,126 | +7.2% | 9/10 |
| volcanic_plains | 48,697 | 49,057 | +0.7% | 5/10 |
| **Overall** | **48,597** | **50,336** | **+3.6%** | **19/30** |

The mean paired gain was 1,740 points (standard error 781, paired t 2.23).
That is encouraging simulator evidence, not proof of a deadline improvement.

The real launcher comparison used two further seeds per biome,
`bench-80..81`, at the default 95 ms search / 150 ms tick limits:

| Biome | Baseline mean | Optimism 0.25 mean |
|---|---:|---:|
| deep_crevasse | 54,406 | 54,451 |
| mountain_range | 43,666 | 44,687 |
| volcanic_plains | 47,830 | 45,621 |
| **Overall** | **48,634** | **48,253** |

All 12 games completed without timeouts, late ticks, or reported bot errors.
The candidate's best game was 61,632 versus 57,662 for the baseline, but its
mean was 0.8% lower and it won only three of the six pairs. These samples are
too small to establish an improvement in the high-score tail. Keep optimism
opt-in rather than changing the shipped defaults. The 200,000-point target
remains unmet.

Reproduce the candidate simulator evaluation with:

```bash
build/sim_live_linux 10 --mcts 1 --radius 2 --horizon 4 --volcanoes 6 \
  --depth 5 --widening 3 --exploration 0.5 --budget 22000 --threads 8 \
  --first 60 --optimism 0.25 --csv runs/optimism-holdout.csv
```

`--csv` writes per-biome, per-seed scores, kills, and spawn counts so paired
statistics do not depend on truncated aggregate means. It excludes filtered-out
biomes and reports output errors explicitly. CLI regression checks are in
`tools/test_sim_live.py`; disabling optimism also reproduced the original
scores, kills, and spawns exactly on `live-60` in all three biomes.

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
