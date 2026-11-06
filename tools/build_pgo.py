"""Train and validate a Linux x86-64-v3 PGO bot library locally, without CPU tuning.

Requires the compiler-rt profile archive and llvm-profdata matching Zig's Clang.
All game simulation uses live observations, budget 7000 and the shipped value net.
Nothing contacts the launcher or game server. See docs/architecture.md.
"""

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

from build_sim import (
    BOT_DIR, FLAGS, LINUX_TARGET, compile_library, compile_program, profile_flags, zig_python,
)
from platform_paths import BUILD, ROOT

LIVE_OPTIONS = [
    "--budget", "7000", "--mcts", "1", "--radius", "2", "--depth", "5",
    "--plan-waves", "1", "--net", str(BOT_DIR / "value_net.bin"),
    "--value-weight", "0.3", "--volcano-rank", "1", "--volcanoes", "3",
    "--threads", "1",
]


def build_stage(folder: Path, flags: list[str], target: str | None = LINUX_TARGET) -> Path:
    folder.mkdir()
    library = folder / "libsim.so"
    compile_library(library, target, extra_flags=flags)
    # Link the same shared library that will be shipped, not a second engine copy.
    compile_program(
        "sim_live", "live_main.cpp", target, "_linux",
        output_dir=folder, library=library,
    )
    return folder / "sim_live_linux"


def merge_profile(tool: str, profiles: list[Path], output: Path, *,
                  include_value_net: bool = False) -> str:
    # Full-profile PGO regressed locally; preserve ordinary inference code generation.
    filters = [] if include_value_net else ["--no-function=ValueNet"]
    subprocess.run([tool, "merge", *filters, "-o", str(output), *map(str, profiles)],
                   check=True)
    summary = subprocess.check_output([tool, "show", str(output)], text=True)
    if "Instrumentation level: IR" not in summary or "Maximum function count: 0\n" in summary:
        raise RuntimeError(f"empty or incompatible profile:\n{summary}")
    return summary


def play(executable: Path, games: int, first: int, folder: Path,
         env: dict[str, str] | None = None) -> tuple[float, list[dict[str, str]]]:
    folder.mkdir()
    traces = folder / "traces"
    traces.mkdir()
    command = [
        str(executable), str(games), *LIVE_OPTIONS, "--first", str(first),
        "--csv", str(folder / "scores.csv"), "--log", str(traces),
    ]
    started = time.perf_counter()
    with (folder / "stdout.log").open("w") as log:
        subprocess.run(command, cwd=ROOT, env=env, stdout=log, check=True)
    elapsed = time.perf_counter() - started
    with (folder / "scores.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != games * 3:
        raise RuntimeError(f"expected {games * 3} games, got {len(rows)}")
    print(f"{folder.name}: {len(rows)} games in {elapsed:.3f}s", flush=True)
    return elapsed, rows


def compare_games(baseline: Path, optimized: Path, games: int, first: int) -> list[int]:
    differences = []
    for biome in ("deep_crevasse", "mountain_range", "volcanic_plains"):
        for seed in range(first, first + games):
            name = f"{biome}-live-{seed}.csv"
            original = (baseline / "traces" / name).read_bytes()
            candidate = (optimized / "traces" / name).read_bytes()
            if original != candidate:
                raise RuntimeError(f"PGO changed the fixed-work game trace: {name}")
            differences.append(
                int(candidate.splitlines()[-1].split(b",")[1])
                - int(original.splitlines()[-1].split(b",")[1])
            )
    return differences


def mean_se(values: list[float]) -> dict[str, float]:
    return {
        "mean": statistics.mean(values),
        "se": statistics.stdev(values) / math.sqrt(len(values)) if len(values) > 1 else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-runtime", type=Path, required=True,
                        help="matching LLVM libclang_rt.profile-x86_64.a")
    parser.add_argument("--llvm-profdata", default="llvm-profdata")
    parser.add_argument("--out", type=Path, default=BUILD / "pgo",
                        help="new directory for all artifacts; existing directories are refused")
    parser.add_argument("--train-games", type=int, default=1, help="games per biome")
    parser.add_argument("--test-games", type=int, default=1, help="held-out games per biome")
    parser.add_argument("--repeats", type=int, default=2, help="alternating paired timing rounds")
    parser.add_argument("--train-first", type=int, default=81000)
    parser.add_argument("--test-first", type=int, default=91000)
    parser.add_argument("--install", action="store_true",
                        help="copy validated profile-use libsim.so into bots/search_bot")
    parser.add_argument("--compare-full", action="store_true",
                        help="also measure full-library PGO from the same training games")
    args = parser.parse_args()
    if sys.platform != "linux" or platform.machine() not in ("x86_64", "AMD64"):
        parser.error("profile training currently requires a Linux x86-64 host")
    if min(args.train_games, args.test_games, args.repeats) < 1:
        parser.error("game counts and repeats must be positive")
    if max(args.train_first, args.test_first) < min(
        args.train_first + args.train_games, args.test_first + args.test_games
    ):
        parser.error("training and test seeds must not overlap")
    runtime = args.profile_runtime.resolve()
    try:
        generate_flags = profile_flags(True, runtime=runtime)
    except ValueError as error:
        parser.error(str(error))
    out = args.out.resolve()
    if out.exists():
        parser.error(f"{out} already exists; choose a fresh --out directory")
    compiler_version = subprocess.check_output(
        [zig_python(), "-m", "ziglang", "c++", "--version"], text=True
    )
    profdata_version = subprocess.check_output([args.llvm_profdata, "--version"], text=True)
    compiler_major = re.search(r"clang version (\d+)", compiler_version)
    profdata_major = re.search(r"LLVM version (\d+)", profdata_version)
    if (compiler_major is None or profdata_major is None
            or compiler_major[1] != profdata_major[1]):
        parser.error("llvm-profdata must match Zig's embedded Clang major version")
    out.mkdir(parents=True)
    print(compiler_version.splitlines()[0], flush=True)
    print(profdata_version.strip(), flush=True)

    baseline = build_stage(out / "baseline", [])
    # The local compiler-rt archive may require newer glibc than the server target.
    # Only training links it; the baseline and final library still target glibc 2.31.
    instrumented = build_stage(out / "instrumented", generate_flags, target=None)
    raw = out / "raw"
    raw.mkdir()
    env = dict(os.environ, LLVM_PROFILE_FILE=str(raw / "game-%m-%p.profraw"))
    play(instrumented, args.train_games, args.train_first, out / "training", env)
    profiles = sorted(raw.glob("*.profraw"))
    if not profiles:
        raise RuntimeError("no raw profiles emitted; check the profile runtime")
    profile = out / "sim.profdata"
    summary = merge_profile(args.llvm_profdata, profiles, profile)
    (out / "profile-summary.txt").write_text(summary)
    optimized = build_stage(out / "optimized", profile_flags(profile=profile))
    executables = {"baseline": baseline, "optimized": optimized}
    if args.compare_full:
        full_profile = out / "full.profdata"
        merge_profile(args.llvm_profdata, profiles, full_profile, include_value_net=True)
        executables["full"] = build_stage(out / "full", profile_flags(profile=full_profile))

    rounds = []
    full_rounds = []
    score_differences = []
    for repeat in range(args.repeats):
        order = list(executables)
        if repeat % 2:
            order.reverse()
        timings = {}
        for name in order:
            elapsed, _ = play(
                executables[name], args.test_games, args.test_first, out / f"{name}-{repeat}"
            )
            timings[name] = elapsed
        differences = compare_games(
            out / f"baseline-{repeat}", out / f"optimized-{repeat}",
            args.test_games, args.test_first,
        )
        if repeat == 0:
            score_differences = differences
        rounds.append({
            "baseline": timings["baseline"], "optimized": timings["optimized"],
            "runtime_reduction_percent": 100 * (1 - timings["optimized"] / timings["baseline"]),
        })
        if args.compare_full:
            compare_games(out / f"baseline-{repeat}", out / f"full-{repeat}",
                          args.test_games, args.test_first)
            full_rounds.append({
                "baseline": timings["baseline"], "optimized": timings["full"],
                "runtime_reduction_percent": 100 * (1 - timings["full"] / timings["baseline"]),
            })

    library = optimized.parent / "libsim.so"
    # Check the shipped C ABI in a fresh process before installing.
    subprocess.run([
        sys.executable, "-c",
        "import ctypes, sys; lib = ctypes.CDLL(sys.argv[1]); "
        "assert lib.sim_observation_size() > 0",
        str(library),
    ], check=True)
    report = {
        "compiler": compiler_version, "llvm_profdata": profdata_version,
        "target": LINUX_TARGET, "training_target": "native", "flags": FLAGS,
        "profile_runtime": str(runtime),
        "profile_excluded_functions": "ValueNet",
        "profile_use_flags": profile_flags(profile=profile),
        "live_options": LIVE_OPTIONS,
        "value_net_sha256": hashlib.sha256((BOT_DIR / "value_net.bin").read_bytes()).hexdigest(),
        "train_first": args.train_first, "train_games_per_biome": args.train_games,
        "test_first": args.test_first, "test_games_per_biome": args.test_games,
        "rounds": rounds,
        "runtime_reduction_percent": mean_se([r["runtime_reduction_percent"] for r in rounds]),
        "paired_games": len(score_differences),
        "paired_score_difference": mean_se(score_differences),
        "profile_sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
        "library_sha256": hashlib.sha256(library.read_bytes()).hexdigest(),
    }
    if args.compare_full:
        report["full_profile"] = {
            "rounds": full_rounds,
            "runtime_reduction_percent": mean_se(
                [r["runtime_reduction_percent"] for r in full_rounds]
            ),
            "profile_sha256": hashlib.sha256(full_profile.read_bytes()).hexdigest(),
            "library_sha256": hashlib.sha256(
                (executables["full"].parent / "libsim.so").read_bytes()
            ).hexdigest(),
        }
    (out / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    if args.install:
        if report["runtime_reduction_percent"]["mean"] <= 0:
            raise RuntimeError("PGO did not reduce runtime; refusing to install a slower library")
        destination = BOT_DIR / "libsim.so"
        shutil.copy2(library, destination)
        print(f"installed {destination}", flush=True)


if __name__ == "__main__":
    main()
