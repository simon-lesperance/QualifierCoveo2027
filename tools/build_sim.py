"""Build the C++ simulator with Zig, which needs no other compiler.

    python tools/build_sim.py            # Windows tools in build/
    python tools/build_sim.py --linux    # adds Linux builds, including the bot's .so
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "sim"
BUILD = ROOT / "build"

ENGINE_SOURCES = ["rng.cpp", "terrain.cpp", "game.cpp"]
PROGRAMS = {"sim_replay": "replay_main.cpp", "sim_bench": "bench_main.cpp"}

# -ffp-contract=off keeps floating-point results identical to the JS engine's.
FLAGS = ["-std=c++20", "-O2", "-DNDEBUG", "-ffp-contract=off", "-fno-sanitize=undefined", "-Wno-nullability-completeness"]
LINUX_TARGET = "x86_64-linux-gnu.2.31"


def compile_program(name: str, main_source: str, target: str | None = None, suffix: str = ".exe") -> None:
    command = [sys.executable, "-m", "ziglang", "c++", *FLAGS]
    if target:
        command += ["-target", target]
    command += ["-o", str(BUILD / f"{name}{suffix}")]
    command += [str(SIM / source) for source in [*ENGINE_SOURCES, main_source]]
    subprocess.run(command, check=True)
    print(f"built {name}{suffix}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--linux", action="store_true")
    args = parser.parse_args()

    BUILD.mkdir(exist_ok=True)
    for name, source in PROGRAMS.items():
        if (SIM / source).exists():
            compile_program(name, source)
            if args.linux:
                compile_program(name, source, LINUX_TARGET, "_linux")


if __name__ == "__main__":
    main()
