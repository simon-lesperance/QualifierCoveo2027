"""How small can the spawn area get, and how many dinosaurs does a wave lose when it does?

Reads a plan through sim_replay, working out the spawn area from each tick's tiles.

    python tools/analyze_pockets.py runs/plans/<plan>.txt <biome> <seed>
"""

import argparse
import collections
import json
import subprocess
from pathlib import Path

from platform_paths import simulator_path

SIM_REPLAY = simulator_path("sim_replay")
WIDTH = 20
BLOCKED = 1 | 4  # Impassable or lava.


def reachable_tiles(flags: list[int]) -> int:
    seen = set()
    stack = [380 + x for x in range(WIDTH) if not flags[380 + x] & BLOCKED]
    seen.update(stack)
    while stack:
        cell = stack.pop()
        row, column = cell // WIDTH, cell % WIDTH
        for r, c in ((row, column + 1), (row, column - 1), (row + 1, column), (row - 1, column)):
            if 0 <= r < WIDTH and 0 <= c < WIDTH:
                neighbor = r * WIDTH + c
                if neighbor not in seen and not flags[neighbor] & BLOCKED:
                    seen.add(neighbor)
                    stack.append(neighbor)
    return len(seen)


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

    areas = [reachable_tiles(state["flags"]) for state in states]
    buckets = collections.Counter()
    for area in areas:
        buckets["<=13" if area <= 13 else "14-30" if area <= 30 else "31-60" if area <= 60 else "61-150" if area <= 150 else "151+"] += 1
    print("ticks by spawn area:", dict(sorted(buckets.items())))

    kill_sizes = collections.Counter()
    for previous, state in zip(states, states[1:]):
        kills = state["kills"] - previous["kills"]
        if kills:
            kill_sizes[kills] += 1
        points = state["score"] - previous["score"]
        if kills >= 6:
            print(f"tick {state['tick']}: {kills} kills, {points} points, spawn area {areas[state['tick']]}")
    print("kills in a tick:", dict(sorted(kill_sizes.items())))
    print("score", states[-1]["score"])


if __name__ == "__main__":
    main()
