# CPU freezes: all-game statistical analysis

Analysis date: 2026-11-06 UTC. Pressure-cut code is committed as `423c7be`,
disabled by default. No bot build was uploaded or practice game started during
this analysis.

Follow-up: [phase-budget.md](phase-budget.md) finds a fixed 100 ms grid inside the bursts, shows
they are not tied to game events, and uses both to search longer.

## Main finding

**The major CPU dropouts follow a five-second wall-clock cycle, not a game-tick
cycle.** In the instrumented game, a typical burst lasts about 0.75 seconds,
contains roughly 50 ms scheduler gaps about every 100 ms, and then disappears
until the next burst. The same five-second cadence is detectable in timing
proxies from other games.

This supports periodic scheduling contention. It does **not** identify the
competing process, prove AWS credit exhaustion, or establish that a specific AWS
service is responsible.

## Data collection and coverage

All **62 games returned by the authenticated launcher's `games` command** were
downloaded: `bot.log`, `game.log`, and `replay.jsonl` for each game, with no
download failures. The inventory spans November 3-6 UTC. This is every game the
launcher exposes, not a claim about inaccessible games or other teams.

Raw logs and generated data are in `runs/freeze-analysis/` (about 2.2 GB,
git-ignored). `manifest.csv` records the size and SHA-256 of every downloaded
file. `collection.json` records collection time and coverage.

| Dataset | Coverage |
|---|---:|
| Downloaded games | 62 |
| Parsed server tick entries | 55,695 |
| Eligible command windows | 54,694 across 55 games |
| Eligible timeout windows | 15 |
| Direct CPU/sampler observations used | 998 cycles in game 24273 |

Exclusions are explicit in `ticks.csv`, not discarded from the archive:

- 300 disconnected-socket windows in six failed startup games.
- 340 entries from game 24269, the unsafe-deadline experimental build with
  50 timeouts and under 90% reply coverage. Its failures are real, but mixing
  that configuration into the stable-build timing analysis would be misleading.
- 309 tail entries from three older probes that deliberately inject delays
  near the end of the match. The exclusion starts conservatively at tick 899.
- 52 remaining final log entries with neither a reply nor a timeout: these are
  not command trials.

The CPU probe logs tick 1 through tick 999; tick 1000 has no following cycle
measurement. Tick 1 is excluded from CPU inference because the sampler already
ran during startup. The final replay can repeat a state; duplicate tick states
are not counted twice.

## How long and how frequent?

A "major stall" below means a sampler gap of at least 20 ms. The sampler
attempts to wake every 0.5 ms, and logs only the longest gap per cycle plus the
number of gaps over 3 ms. Therefore, these are **affected-cycle counts**, not
a complete list of individual CPU preemptions.

| Measurement in game 24273 | Result |
|---|---:|
| Cycles with a major stall | 108 / 998 = 10.82% |
| Affected-cycle rate, 95% block-bootstrap interval | 6.61%-15.13% |
| Major-stall duration, median | 50.6 ms |
| Major-stall duration, middle 50% | 48.95-52.0 ms |
| Major-stall duration, 95th percentile | 53.3 ms |
| Major-stall duration, maximum | 59.7 ms |
| Longest gaps summed / observed wall time | 8.09% |
| Worker on-CPU time, median on searched clean cycles | 87.0 ms |
| Worker on-CPU time, median on searched stalled cycles | 42.85 ms |

The 8.09% figure is a sampled-gap share, **not an exact CPU-loss percentage**:
it omits shorter gaps and any second large gap in the same cycle, and includes
small amounts of intended sampler sleep. Worker timings use searched cycles
with positive reported worker runtime; they are descriptive, not a
workload-matched throughput experiment.

## Bursts and periodicity

Grouping major sampled gaps whose successive start times are no more than
500 ms apart gives 15 episodes: **13 substantial bursts** with at least three
affected cycles, plus two isolated events.

| Substantial-burst measurement | Result |
|---|---:|
| Span, median | 749.1 ms |
| Span, range | 636.6-851.6 ms |
| Successive burst-start spacing, mean | 5,000.56 ms |
| Start-spacing standard deviation | 5.46 ms |
| Start-spacing range | 4,994.1-5,012.3 ms |
| Within-burst sampled gap-start spacing, median | 100.1 ms |
| Sampled-gap share within bursts, mean | 52.23% |

The combination is striking: **about 50 ms off-CPU out of each 100 ms during
a roughly 0.75-second burst, repeating every five seconds**. Episode spans
are sampled envelopes, not exact start/end times of the competing workload.

The five-second result is not just visual interpretation:

1. A frequency scan over periods from 2 to 10 seconds finds its maximum at
   **5,000 ms** in the CPU probe. A 10-tick-block permutation test comparing
   the maximum over the entire scan gives **p = 0.0005** (1,999 resamples).
   This accounts for selecting the strongest frequency in that scan.
2. The selected five-second frequency replicates in independent games:
   server pre-reply timing-overhead events in **53 games**, and late-reply /
   timeout events in **37 games**, each give **p = 0.0005** using separate
   short-block permutations. Each game's phase is allowed to differ; only
   period is shared. Games with fewer than three events are omitted from
   these replication tests.
3. Changing the probe's stall threshold to 10, 30, or 40 ms preserves the
   five-second result: each fixed-frequency test gives **p = 0.001**
   (999 resamples).

These are Monte Carlo p-values at the stated sampling resolution, not exact
probabilities of a hardware cause. The permutation null assumes short blocks
can be rearranged within a game; these are observational, not randomized games.
The three principal spectral tests also remain below 0.002 after a simple
three-test Bonferroni correction.

The independent timing proxies are not direct CPU measurements:
`dispatch_gap20` is at least 20 ms between the server's "Playing tick" log and
the start inferred from its reply-time counter. This can include dispatch,
serialization, scheduling, and logging effects. `late110` means an accepted
reply over 110 ms or a non-socket timeout.

### Alignment to game start versus UTC

The five-second cycle is **not synchronized to the same UTC phase across games**.
For the 53 games with enough server-overhead events, the preferred phase is
strongly concentrated relative to each game's first eligible tick:

| Phase reference | Circular concentration R | Uniform-phase permutation p |
|---|---:|---:|
| Game start | 0.899 | 0.00005 |
| Absolute UTC | 0.186 | 0.158 |

Here R ranges from 0 (dispersed) to 1 (identical phase); the tests use 19,999
independent uniform phase draws per game. The circular mean overhead-event
phase is **4.350 seconds after game start, modulo five seconds**. This points
toward a task or lifecycle tied to the game/server/container startup, rather
than a single globally synchronized five-second host event. It still cannot
identify which task is responsible.

Do not hard-code that phase into the bot: the instrumented build's sampled
bursts begin about **1.25 seconds** after its first tick, modulo five seconds.
Startup work and instrumentation differ. Any future phase-aware policy needs
to calibrate its phase within the actual game.

## Before, during, and after the decision

Of the 108 affected probe cycles, the longest sampled stall was:

| Position relative to the bot decision call | Cycles |
|---|---:|
| Entirely during the decision | 97 |
| Entirely after the decision, before the next call | 5 |
| Overlapping the end of the decision | 5 |
| Crossing a measurement boundary / unaligned | 1 |

This is not evidence that searching *causes* the stalls: decisions occupy much
more wall time than the gaps between calls. "After decision" is not an exact
WebSocket send timestamp; the returned action still has to be serialized and
sent by the unchanged connection code.

The two probe timeouts are especially informative:

| Timeout tick | Previous call-to-next-call gap after decision | Arrival after server's tick-start log | Current decision time |
|---|---:|---:|---:|
| 249 | 55.7 ms | 51.5 ms | 99.5 ms |
| 788 | 59.1 ms | 58.5 ms | 99.1 ms |

In both cases, a nearly full normal search followed an already late arrival.
This directly supports subtracting excess inter-tick arrival delay from the
search budget. Ordinary production logs do not provide equivalent per-tick
arrival timestamps.

Normal probe gaps after the decision have a median of **3.2 ms** and a
95th percentile of **5.4 ms**, consistent with the existing 6 ms grace.

Do not treat `arrival_after_send_ms` as a reliable one-way network latency:
it combines bot wall time with an inferred server counter start and sometimes
becomes negative. `arrival_after_play_log_ms` is the directly comparable
log-relative quantity used above, also not an exact wire latency.

## Clustering and advance warning

Major CPU stalls cluster strongly. There are **64 adjacent affected-cycle
pairs**, against a mean of **11.67** under 4,999 permutations that preserve the
event count within each 100-tick match segment (**p = 0.0002**).

Descriptively:

- Another stall occurs on the next cycle **59.3%** of the time after a stall,
  versus **4.95%** after a clean cycle.
- A host CPU-pressure delta of at least 20 ms precedes a next-cycle stall
  **55.6%** of the time, versus **3.94%** without that pressure.
- The same-cycle pressure threshold detects all 108 major sampled-stall cycles
  in this probe, while flagging **2.81%** of cycles without a major sampled gap.

These predictive percentages come from one instrumented game. The 25
exploratory circular-shift lag tests do **not** survive Holm correction
(smallest adjusted p = 0.13); do not present any individual lag as independently
proven. The within-segment clustering test and five-second spectral tests are
different, explicitly stated null hypotheses.

**CPU stalls and late replies are different outcomes.** Modern logged builds
have no pairs of >110 ms/timeout events exactly 1, 2, 3, 5, or 10 ticks apart,
even though the CPU probe has many consecutive stalled cycles. A wall-clock
search deadline can absorb a stall by doing fewer simulations and still reply
on time. Pressure is useful evidence of reduced CPU availability, not proof
the next command will time out.

## Game events, match progression, and possible causes

A binomial regression with game-clustered standard errors controls for known
build variant, match progress, dinosaur count, map shifts, meteor explosions,
and whether the previous reply was very fast. It uses 25,974 non-initial
windows across 26 known-configuration games and 97 late events.

- No detected relationship with map shifts, increasing dinosaur count, or
  match progression after Holm correction across the five gameplay predictors.
- Previous very-fast reply has an estimated odds ratio of 1.70, but its
  interval spans 1 (0.97-2.98); adjusted p = 0.256.
- Meteor explosions are associated with *fewer late replies* (odds ratio
  0.483, interval 0.339-0.687; adjusted p = 0.000262). This is an association
  with **reply timing**, plausibly related to action availability and search
  workload. It does not show that meteors reduce CPU contention.
- Tests for tick-number modulo 5, 10, 20, 50, or 100 find no significant
  fixed-tick cadence after correction. The strong cadence is wall-clock time.

In the CPU probe, the correlation between sampled stall size and host
CPU-pressure delta is strong (Spearman rho **0.695**); with worker scheduler
wait it is **0.598**. These correlations are descriptive: the nominal
Spearman p-values in the CSV do not account for temporal dependence.

There are **zero cgroup throttle events**, only **270 ms** of host steal-time
counter increments across the observed cycles, and **no >1 ms Python GC
cycles**. Typical stalled worker scheduler-wait times are about 50 ms.
This points toward runnable tasks waiting for scheduling rather than Python
GC or an observed container CPU-quota limit. Host steal is coarse-grained;
neither it nor the available logs can rule out every hypervisor effect.
No CPU-credit balance was measured.

## Pressure cut and pinning: what the statistics actually support

These are accepted-reply counts, with timeouts kept separate:

| Configuration | Games | >110 ms | >130 ms | >140 ms | Timeouts |
|---|---:|---:|---:|---:|---:|
| Baseline 95 ms | 6 | 18 | 9 | 3 | 0 |
| Pinning | 3 | 16 | 9 | 4 | 1 |
| Cut 85, hold 1 | 3 | 6 | 4 | 0 | 0 |
| Cut 85, hold 3 | 3 | 7 | 1 | 0 | 0 |
| Cut 70, hold 1 | 3 | 11 | 2 | 0 | 0 |
| Cut 70, hold 3 | 3 | 14 | 3 | 0 | 0 |
| Cut 60, hold 0 | 3 | 18 | 3 | 0 | 1 |

Exact game-level label-permutation comparisons use >130 ms **or a timeout**
as the outcome, so a failed reply is never counted as a fast success.
Cut 85 / hold 3 versus baseline has **two-sided p = 0.238**; pinning has
**p = 0.0952**. Neither is significant after six-comparison Holm correction.
Games were run sequentially on random maps, not randomized or paired, further
limiting causal interpretation.

This is more conservative than the earlier report's rough Poisson calculation,
which treated individual tail events as independent. There is still no clear
evidence that pressure cut improves scores or overall throughput. Keep it
**off / opt-in**. The strong new finding is the five-second CPU-contention
cycle, not a proven winning budget setting.

## Files and reproduction

The main data products under `runs/freeze-analysis/` are:

| File | Contents |
|---|---|
| `ticks.csv` | All parsed ticks; response timing, censoring, eligibility, replay workload, lagged fields, probe telemetry where available |
| `games.csv` | Every game, configuration labels, coverage, scores, per-game reply summaries |
| `probe-ticks.csv` | Detailed direct telemetry and previous-cycle gaps |
| `sampled-freezes.csv` | Cycles with >=20 ms longest gaps, timestamps, phases, episode labels |
| `freeze-episodes.csv` | Burst envelopes and spacing |
| `budget-blocks.csv` | Existing 100-tick aggregate bot summaries; not fabricated per-tick PSI |
| `distributions.csv`, `variants.csv` | Duration distributions and configuration summaries |
| `lag-tests.csv`, `pattern-tests.csv` | Lag, map-shift, and fixed-tick tests with Holm-adjusted p-values |
| `variant-tests.csv`, `timing-model.csv` | Game-level comparisons and clustered regression |
| `probe-correlations.csv`, `probe-periodogram.csv` | Descriptive relationships and irregular-time Lomb-Scargle spectrum |
| `five-second-phases.csv` | Per-game preferred overhead-event phases relative to game start and UTC |
| `statistics.json` | Full numeric results, inference settings, package versions, spectral tests |
| `freeze-patterns.png`, `freeze-periodicity.png` | Six-panel overview, wall-clock spectrum, and phase alignment |
| `manifest.csv`, `collection.json` | Archive integrity and collection provenance |

Blank probe fields in `ticks.csv` mean **not measured**, not zero CPU pressure.
Accepted replies are not a complete latency distribution because timeout
durations are censored at the server deadline. Historical configurations remain
explicitly unknown rather than being guessed from scores.

From the repository root:

```bash
uv pip install --python .venv/bin/python -r tools/requirements-analysis.txt
.venv/bin/python tools/collect_game_logs.py
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/bin/python tools/analyze_freezes.py --resamples 4999 \
  > runs/freeze-analysis/analysis-output.json
```

The analysis seed is `20261106`. Rate confidence intervals use a hierarchical
game bootstrap with circular 20-tick blocks, 4,999 resamples. For the CPU probe,
there is only one game, so that interval describes within-game uncertainty,
not uncertainty across cloud hosts or deployments. Spectral tests use at most
1,999 resamples; threshold sensitivity checks use at most 999.
