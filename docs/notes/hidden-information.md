# What hidden information costs

> **Lab notes, kept as history.** They were written as the work happened, so earlier sections can be wrong or out of date, and later sections correct them. For current conclusions, see [results.md](../results.md) and [status.md](../status.md).

Run on 2026-11-04. The live agent (beam 30, radius 2, 16,000 simulated ticks per move) played 18 simulated games per row, 6 on each biome, with one hidden item revealed to its planner at a time. The true game was unchanged.

| Revealed to the planner | Overall | `deep_crevasse` | `volcanic_plains` | `mountain_range` |
|---|---|---|---|---|
| Nothing (live bot) | 44,876 | 50,141 | 44,484 | 40,004 |
| True future rows | 45,136 | 49,651 | 46,492 | 39,264 |
| Waves at guessed positions | 43,021 | 48,069 | 42,736 | 38,259 |
| **True tie-breaks (RNG)** | **63,763** | 68,799 | 63,495 | 58,994 |
| True tie-breaks and rows | 63,704 | 69,078 | 64,079 | 57,955 |
| Tie-breaks, rows and waves | 75,104 | 85,398 | 72,143 | 67,771 |
| The same, plus aiming at empty ground | 80,530 | 89,826 | 76,213 | 75,553 |
| The same, ruling out spawn-tick kills | 70,187 | 79,592 | 69,224 | 61,745 |

Changes of about 1,000-2,000 are noise.

## What it means

- **Tie-breaks are the largest cost, about 19,000.** A single simulated guess at dinosaur tie-breaks makes plans fail when the real dinosaurs choose differently.
- **Future rows don't matter.** Repeating the bottom row is good enough.
- **Simulating waves without knowing their positions doesn't help.** Leaving them out of planning was right.
- **Knowing spawns adds about 11,000-17,000.** About 10,000 of that is spawn-tick kills, which are out of reach for a live bot.
- **Waves rise with information:** about 35 at base, 43 with tie-breaks, 47-48 with everything.

The seed can't be recovered, so the tie-breaks will stay hidden. The goal is to plan well despite them, which favours methods that average over many guesses.

Note: the RNG also decides which of several equal moves a Triceratops pack takes, so "tie-breaks" includes pack steps.

## Reproduce

Run from `Code/`:

```bash
build/sim_live.exe 6 --budget 16000 --beam 30 --radius 2 --reveal-rng 1
```
