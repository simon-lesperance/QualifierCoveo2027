"""Benchmark a bot on reproducible seeds across the game's biomes.

    python tools/benchmark_bot.py --bot bots/search_bot --seeds 3
"""

import argparse
import statistics

from run_local import BIOMES, ROOT, play


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot", default="bots/search_bot")
    parser.add_argument("--biomes", nargs="+", choices=BIOMES, default=BIOMES)
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--first-seed", type=int, default=0)
    parser.add_argument("--tick-ms", type=int, default=150)
    args = parser.parse_args()
    if args.seeds < 1:
        parser.error("--seeds must be at least 1")

    bot_dir = (ROOT / args.bot).resolve()
    results: dict[str, list[dict]] = {biome: [] for biome in args.biomes}
    for biome in args.biomes:
        for index in range(args.first_seed, args.first_seed + args.seeds):
            seed = f"bench-{index}"
            row = play(bot_dir, biome, seed, args.tick_ms)
            row["biome"] = biome
            row["seed"] = seed
            results[biome].append(row)
            print(
                f"{biome:16} {seed:12} score={row['score']} "
                f"timedOut={row['timedOut']} late={row['late']} botErrors={row['botErrors']} "
                f"medianSimulated={row['medianSimulated']} maxMoveMs={row['maxMoveMs']}",
                flush=True,
            )

    for biome, rows in results.items():
        scores = [
            row["score"] for row in rows
            if row["score"] is not None and not row["timedOut"]
        ]
        completed = sum(not row["timedOut"] and row["score"] is not None for row in rows)
        late = sum(row["late"] for row in rows)
        errors = sum(row["botErrors"] for row in rows)
        if scores:
            print(
                f"{biome:16} completed={completed}/{len(rows)} "
                f"timeouts={len(rows) - completed} "
                f"mean={statistics.mean(scores):.0f} median={statistics.median(scores):.0f} "
                f"min={min(scores)} max={max(scores)} late={late} botErrors={errors}"
            )
        else:
            print(
                f"{biome:16} completed=0/{len(rows)} timeouts={len(rows)} "
                f"late={late} botErrors={errors}"
            )


if __name__ == "__main__":
    main()
