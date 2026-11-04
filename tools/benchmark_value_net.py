"""Compare live-information MCTS with and without a trained value network.

Runs build/sim_live twice on the same deterministic seeds (live-<first>...),
once as a baseline and once with the network, then reports per-biome and
overall score deltas. The simulator prints integer-truncated means.

    python tools/benchmark_value_net.py runs/nets/<name>/value_net.bin --games 2
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
BIOMES = ["deep_crevasse", "mountain_range", "volcanic_plains"]
BIOME_RE = re.compile(r"^(\S+)\s+mean\s+(-?\d+)\s+best\s+(-?\d+)\s+kills\s+(-?\d+)\s+spawned\s+(-?\d+)\s*$")
OVERALL_RE = re.compile(r"^overall mean (-?\d+)\s*$")


def default_simulator() -> Path:
    return BUILD / "sim_live.exe"


def parse_output(text: str) -> tuple[dict[str, dict[str, int]], int]:
    biomes: dict[str, dict[str, int]] = {}
    overall = None
    for line in text.splitlines():
        if match := BIOME_RE.match(line):
            name, mean, best, kills, spawned = match.groups()
            biomes[name] = {"mean": int(mean), "best": int(best), "kills": int(kills), "spawned": int(spawned)}
        elif match := OVERALL_RE.match(line):
            overall = int(match.group(1))
    missing = [biome for biome in BIOMES if biome not in biomes]
    if missing or overall is None:
        raise ValueError(f"missing {', '.join(missing) or 'overall mean'} in simulator output:\n{text}")
    return biomes, overall


def run(simulator: Path, args: argparse.Namespace, extra: list[str]) -> tuple[dict[str, dict[str, int]], int]:
    command = [
        str(simulator), str(args.games),
        "--mcts", "1",
        "--budget", str(args.budget),
        "--threads", str(args.threads),
        "--first", str(args.first_seed),
        *extra,
    ]
    print("$ " + " ".join(command), flush=True)
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf8", errors="replace")
    if result.returncode != 0:
        sys.exit(f"simulator exited with {result.returncode}:\n{result.stderr.strip() or result.stdout.strip()}")
    try:
        return parse_output(result.stdout)
    except ValueError as error:
        sys.exit(f"malformed simulator output: {error}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("net", type=Path, help="trained value_net.bin from tools/train_value.py")
    parser.add_argument("--sim", type=Path, default=default_simulator(), help="sim_live binary (default: %(default)s)")
    parser.add_argument("--games", type=int, default=2, help="games per biome (default: %(default)s)")
    parser.add_argument("--first-seed", type=int, default=0, help="first live-<n> seed (default: %(default)s)")
    parser.add_argument("--threads", type=int, default=1, help="simulator threads (default: %(default)s)")
    parser.add_argument("--budget", type=int, default=6500, help="simulated ticks per move (default: %(default)s)")
    parser.add_argument("--value-weight", type=float, default=1.0, help="value-net weight (default: %(default)s)")
    parser.add_argument("--net-cost", type=int, help="simulated ticks per net evaluation; omitted uses the simulator default")
    args = parser.parse_args()
    if args.games < 1:
        parser.error("--games must be at least 1")
    if args.threads < 1:
        parser.error("--threads must be at least 1")
    if args.budget < 1:
        parser.error("--budget must be at least 1")
    if args.first_seed < 0:
        parser.error("--first-seed must be non-negative")

    simulator = args.sim if args.sim.is_absolute() else (Path.cwd() / args.sim)
    if not simulator.is_file():
        sys.exit(f"simulator not found: {simulator} (build it with tools/build_sim.py)")
    net = args.net.resolve()
    if not net.is_file():
        sys.exit(f"value net not found: {net} (train one with tools/train_value.py)")

    net_options = ["--net", str(net), "--value-weight", repr(args.value_weight)]
    if args.net_cost is not None:
        net_options += ["--net-cost", str(args.net_cost)]

    baseline, baseline_overall = run(simulator, args, [])
    valued, valued_overall = run(simulator, args, net_options)

    print(f"\n{'biome':16} {'baseline':>9} {'value net':>10} {'delta':>8} {'delta%':>8}")
    rows = [(biome, baseline[biome]["mean"], valued[biome]["mean"]) for biome in BIOMES]
    rows.append(("overall", baseline_overall, valued_overall))
    for name, base, value in rows:
        delta = value - base
        percent = f"{100 * delta / base:+.1f}%" if base else "n/a"
        print(f"{name:16} {base:9d} {value:10d} {delta:+8d} {percent:>8}")


if __name__ == "__main__":
    main()
