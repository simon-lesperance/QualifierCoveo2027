"""Break down how a search plan earns its score.

    python tools/analyze_plan.py runs/plans/mountain_range-est-0-w200.txt mountain_range est-0
"""

import argparse
import collections
import json
import math
import statistics
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIM_REPLAY = ROOT / "build" / "sim_replay.exe"
LAVA = 4


def decay(age: int) -> int:
    return math.ceil(max(0, 500 / math.sqrt(age / 4 + 5) - 60))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    parser.add_argument("biome")
    parser.add_argument("seed")
    args = parser.parse_args()

    lines = subprocess.run(
        [str(SIM_REPLAY), args.biome, args.seed, str(args.plan.resolve())],
        check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    states = [json.loads(line) for line in lines]

    cause_kills = collections.Counter()
    cause_base = collections.Counter()
    species_kills = collections.Counter()
    spawned = collections.Counter()
    ages = []
    wave_ticks = []
    alive_ticks = []
    lava_tiles = []
    seen = set()
    survivors_by_wave = []

    for previous, state in zip(states, states[1:]):
        tick = state["tick"]
        before = {d[0]: d for d in previous["dinos"]}
        after = {d[0]: d for d in state["dinos"]}
        new = [d for d in state["dinos"] if d[0] not in seen]
        for d in new:
            seen.add(d[0])
            spawned[d[1]] += 1
        if any(d[0] not in before for d in state["dinos"]):
            wave_ticks.append(tick)

        landing = [m for m in previous["meteors"] if m[2] == 1]
        scored = state["kills"] - previous["kills"]
        gone = [d for i, d in before.items() if i not in after]
        origin = previous["originY"]
        for d in gone:
            _, name, x, y, age = d
            age += 1
            row = y - origin
            in_blast = any((x - m[0]) ** 2 + (y - m[1]) ** 2 <= 4 for m in landing)
            on_lava = 0 <= row < 20 and state["flags"][min(399, max(0, (y - state["originY"]) * 20 + x))] & LAVA
            if in_blast:
                cause = "meteor"
            elif tick % 5 == 0 and row == 0:
                cause = "scrolled off"
            elif on_lava:
                cause = "lava"
            else:
                cause = "other (eaten or moved then hit)"
            cause_kills[cause] += 1
            cause_base[cause] += decay(age)
            species_kills[name] += 1
            ages.append(age)
        alive_ticks.append(len(after))
        lava_tiles.append(sum(1 for flags in state["flags"] if flags & LAVA))

    gaps = [b - a for a, b in zip(wave_ticks, wave_ticks[1:])]
    print(f"score {states[-1]['score']}, kills {states[-1]['kills']}, waves {len(wave_ticks)}")
    print(f"wave gap: median {statistics.median(gaps)}, mean {statistics.mean(gaps):.1f}, max {max(gaps)}, "
          f"at the 100-tick timer {sum(1 for gap in gaps if gap >= 100)}")
    print(f"age at death: median {statistics.median(ages)}, mean {statistics.mean(ages):.1f}, "
          f"age 0-4: {sum(1 for age in ages if age <= 4)} of {len(ages)}")
    # Killed before any state could show them. Needs knowledge of where waves spawn.
    print(f"killed on their spawn tick: {states[-1]['kills'] - sum(cause_kills.values())} of {states[-1]['kills']}")
    print(f"alive per tick: mean {statistics.mean(alive_ticks):.1f}")
    print(f"lava tiles: mean {statistics.mean(lava_tiles):.0f}, max {max(lava_tiles)}")
    print("by cause (kills, points before multiplier):")
    for cause, count in cause_kills.most_common():
        print(f"  {cause:32} {count:4}  {cause_base[cause]:7}")
    print("kills / spawned by species:")
    for name, count in spawned.most_common():
        print(f"  {name:14} {species_kills[name]:4} / {count}")


if __name__ == "__main__":
    main()
