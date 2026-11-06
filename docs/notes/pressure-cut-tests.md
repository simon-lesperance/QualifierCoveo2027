# Server latency mitigations: CPU pinning and pressure cut (2026-11-05)

## Background

The server is an AWS t3.medium with 2 vCPUs, which are two hyperthreads of one physical core.
On about 14% of ticks, something else on the host takes about half the CPU. Freezes of 50–60 ms
come in clusters. The late-arrival fix (`BOT_ARRIVAL_GRACE_MS`) covers freezes between ticks.
These tests target freezes that happen while the bot is searching.

## Options tested (all off by default in `bots/search_bot/bot.py`)

- `BOT_PIN_CPU`: `auto` or a CPU index. It pins the process to one vCPU.
- `BOT_PSI_CUT_MS`: when greater than 0, this budget replaces `BOT_TIME_MS` (95 ms) on ticks that
  follow CPU pressure. CPU pressure is time our tasks were runnable but waiting for a CPU. The bot
  reads it from `/proc/pressure/cpu` ("some total") and takes the difference between ticks.
- `BOT_PSI_THRESHOLD_MS` (default 20): a tick counts as pressured when that difference is at least
  this much. In probe game 24273, 20 ms caught every starved tick and flagged 5% of normal ones.
- `BOT_PSI_HOLD_TICKS` (default 3): how many ticks the cut budget stays after a pressured tick.

## Results (3 server practice games per variant, build v17 code with the x86-64-v3 library)

Reply times are the server's "Command received ... after X ms" values. Per variant, the `>110`,
`>130` and `>140` columns count replies slower than that many ms, summed over the 3 games. `p99`
and `max` are the 99th-percentile and slowest reply times. "Cut %" is the share of ticks that
used the cut budget.

| Variant | Timeouts | >110 | >130 | >140 | p99 | max | Median sims/move | Cut % | Scores |
|---|---|---|---|---|---|---|---|---|---|
| baseline, games 1–3 | 0 | 8 | 5 | 2 | 98 | 145 | 7,101 | 0 | 59,984 / 48,652 / 57,535 |
| baseline, games 4–6 | 0 | 10 | 4 | 1 | 99 | 145 | 6,693 | 0 | 54,697 / 45,460 / 39,688 |
| pin one vCPU | 1 | 16 | 9 | n/a | 100 | 148 | 6,897 | 0 | 60,276 / 40,297 / 44,192 |
| cut 85 ms, hold 1 | 0 | 6 | 4 | 0 | 98 | 140 | 7,149 | 19.5 | 45,343 / 45,704 / 40,218 |
| **cut 85 ms, hold 3** | 0 | 7 | **1** | 0 | 98 | **133** | 7,353 | 24.4 | 44,241 / 60,705 / 54,150 |
| cut 70 ms, hold 1 | 0 | 11 | 2 | 0 | 100 | 136 | 6,909 | 20.7 | 48,112 / 49,280 / 39,804 |
| cut 70 ms, hold 3 | 0 | 14 | 3 | 0 | 99 | 136 | 6,765 | 26.1 | 44,480 / 50,506 / 51,932 |
| cut 60 ms, hold 0 | 1* | 18 | 3 | 0 | 98 | 139 | 7,041 | 12.8 | 57,191 / 38,313 / 47,950 |

\* The c60h0 timeout (tick 380 of its first game) came right after a tick with nothing to decide.
The freeze lasted more than 150 ms, so no budget setting could have prevented it.

Game ids:
- baseline: 24278–24280 and 24291–24293
- pin: 24282–24284
- c85h1: 24295–24297
- c85h3: 24299–24301
- c70h1: 24303–24305
- c70h3: 24286–24288
- c60h0: 24307–24309

## Conclusions

- **Pinning hurts.** It gave twice as many slow replies and the only timeout among the pinned
  games. Drop it.
- **The pressure cut removes the worst tail.** All 15 games with a cut had 0 replies over 140 ms,
  against 3 in the 6 baseline games. The slowest reply dropped from 145 ms to 133–140 ms.
- **The best setting is cut 85 ms, hold 3.** It had 1 reply over 130 ms in 3 games, against 9 in
  6 baseline games (1.5 per game). Median simulations didn't drop. Under the baseline rate, seeing
  1 or fewer by chance has a probability of about 6%, so this is suggestive, not proven.
- **Cuts below 85 ms don't help.** They gave more replies over 110 ms and no better tail, and they
  lose search time.
- **Scores can't separate the variants.** The random map dominates them (39k–61k within a single
  variant).
- **Neither baseline had a timeout.** The cut only reduces how close we get to 150 ms.

**Verdict:** not a clear improvement yet, so the code is retained as an opt-in and off by default.
If you adopt it, use `BOT_PSI_CUT_MS=85` with `BOT_PSI_HOLD_TICKS=3`. Confirming it would take
about 10 or more games per arm.
