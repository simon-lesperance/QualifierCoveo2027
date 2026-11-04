"""Compare biomes by running the same foresight search on several seeds of each.

Plans go to runs/plans/, named <biome>-<seed>-w<width>.txt.

    python tools/estimate_maps.py --width 200 --seeds 4
"""

import argparse
import re
import statistics
import subprocess

from platform_paths import ROOT, simulator_path

SEARCH = simulator_path("sim_search")
PLANS = ROOT / "runs" / "plans"
BIOMES = ["deep_crevasse", "mountain_range", "volcanic_plains"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, default=200)
    parser.add_argument("--seeds", type=int, default=4)
    parser.add_argument("--first-seed", type=int, default=0)
    parser.add_argument("--threads", type=int, default=11)
    parser.add_argument("--biomes", nargs="*", default=BIOMES)
    parser.add_argument("--shield", action="store_true", help="rule out spawn-tick kills")
    args = parser.parse_args()

    PLANS.mkdir(parents=True, exist_ok=True)
    scores: dict[str, list[int]] = {}
    for index in range(args.first_seed, args.first_seed + args.seeds):
        for biome in args.biomes:
            seed = f"est-{index}"
            suffix = "-shield" if args.shield else ""
            plan = PLANS / f"{biome}-{seed}-w{args.width}{suffix}.txt"
            output = subprocess.run(
                [str(SEARCH), biome, seed, str(args.width), str(plan), "--threads", str(args.threads),
                 "--cluster", "0", "--spread", "0", "--shield", "1" if args.shield else "0"],
                check=True, capture_output=True, text=True,
            ).stdout
            print(f"{biome:16} {seed:7} {output.splitlines()[0]}", flush=True)
            scores.setdefault(biome, []).append(int(re.search(r"score=(\d+)", output).group(1)))

    print()
    for biome, values in scores.items():
        spread = statistics.stdev(values) if len(values) > 1 else 0
        print(f"{biome:16} mean {statistics.mean(values):9.0f}  sd {spread:8.0f}  best {max(values)}  worst {min(values)}")


if __name__ == "__main__":
    main()
