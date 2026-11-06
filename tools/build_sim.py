"""Build the C++ simulator with Zig, which needs no other compiler.

    python tools/build_sim.py            # Windows tools in build/
    python tools/build_sim.py --linux    # adds Linux builds, including the bot's library
    python tools/build_sim.py --vec-only # just what PPO training needs
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "sim"
BUILD = ROOT / "build"

ENGINE_SOURCES = ["rng.cpp", "terrain.cpp", "game.cpp", "agent.cpp", "features.cpp", "value_net.cpp", "policy_net.cpp"]
PROGRAMS = {"sim_replay": "replay_main.cpp", "sim_bench": "bench_main.cpp", "sim_search": "search_main.cpp", "sim_live": "live_main.cpp", "sim_datagen": "datagen_main.cpp", "sim_policygen": "policygen_main.cpp", "sim_nettest": "nettest_main.cpp", "sim_policytest": "policytest_main.cpp", "sim_pocket": "pocket_main.cpp", "sim_carve": "carve_main.cpp"}

# -ffp-contract=off keeps floating-point results identical to the JS engine's.
FLAGS = ["-std=c++20", "-O2", "-DNDEBUG", "-ffp-contract=off", "-fno-sanitize=undefined", "-Wno-nullability-completeness"]
LINUX_TARGET = "x86_64-linux-gnu.2.31"
BOT_DIR = ROOT / "bots" / "search_bot"


def compile_program(name: str, main_source: str, target: str | None = None, suffix: str = ".exe") -> None:
    command = [sys.executable, "-m", "ziglang", "c++", *FLAGS]
    if target:
        command += ["-target", target]
    command += ["-o", str(BUILD / f"{name}{suffix}")]
    command += [str(SIM / source) for source in [*ENGINE_SOURCES, main_source]]
    subprocess.run(command, check=True)
    print(f"built {name}{suffix}")


def compile_library(output: Path, target: str | None = None) -> None:
    command = [sys.executable, "-m", "ziglang", "c++", *FLAGS, "-shared", "-fPIC"]
    if target == LINUX_TARGET:
        command.append("-march=x86_64_v3")  # The server's Xeon has it, and rich/dev measured more search per tick.
    if target:
        command += ["-target", target]
    command += ["-o", str(output), *(str(SIM / source) for source in [*ENGINE_SOURCES, "capi.cpp"])]
    subprocess.run(command, check=True)
    print(f"built {output.name}")


def compile_vec_env() -> None:
    library = "libvec.dll" if sys.platform == "win32" else "libvec.so"
    command = [sys.executable, "-m", "ziglang", "c++", *FLAGS, "-shared", "-fPIC", "-o", str(BUILD / library)]
    command += [str(SIM / source) for source in [*ENGINE_SOURCES, "vec_env.cpp"]]
    subprocess.run(command, check=True)
    print(f"built {library}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--linux", action="store_true")
    parser.add_argument("--vec-only", action="store_true", help="Only the library PPO trains on. Works on Windows or Linux")
    args = parser.parse_args()

    BUILD.mkdir(exist_ok=True)
    if args.vec_only:
        compile_vec_env()
        return
    for name, source in PROGRAMS.items():
        if (SIM / source).exists():
            compile_program(name, source)
            if args.linux:
                compile_program(name, source, LINUX_TARGET, "_linux")
    compile_vec_env()
    compile_library(BOT_DIR / "libsim.dll")
    if args.linux:
        compile_library(BOT_DIR / "libsim.so", LINUX_TARGET)


if __name__ == "__main__":
    main()
