"""Writes foresight plans from sim_search --plan-data files for train_ppo.py --plans.

    python tools/extract_plans.py runs/data/oracle1 runs/data/oracle1.plans
"""

import argparse
from pathlib import Path

import numpy as np

BIOMES = ["deep_crevasse", "mountain_range", "volcanic_plains"]
FEATURE_BYTES = 14 * 400 + 5 + 400 + 2
HEADER_BYTES = 1002 * 4
RECORD_BYTES = 4 + FEATURE_BYTES + 4 + 8


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    count = 0
    with open(args.out, "w") as out:
        for path in sorted(args.folder.glob("*.bin")):
            biome = next(b for b in BIOMES if path.name.startswith(b + "-"))
            seed = path.stem[len(biome) + 1:]
            data = np.fromfile(path, dtype=np.uint8)
            if len(data) != HEADER_BYTES + 1000 * RECORD_BYTES:
                raise ValueError(f"{path.name} has an unexpected size")
            offsets = HEADER_BYTES + np.arange(1000) * RECORD_BYTES + 4 + FEATURE_BYTES + 4
            codes = np.frombuffer(data, dtype=np.uint8)[offsets[:, None] + np.arange(4)]
            codes = (codes.astype(np.int64) * [1, 256, 65536, 16777216]).sum(axis=1)
            out.write(f"{BIOMES.index(biome)} {seed} " + " ".join(map(str, codes)) + "\n")
            count += 1
    print(count, "plans")


if __name__ == "__main__":
    main()
