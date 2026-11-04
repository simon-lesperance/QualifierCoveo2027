"""Build the C++ simulator with Zig, which needs no other compiler.

    .venv/bin/python tools/build_sim.py            # host tools in build/
    .venv/bin/python tools/build_sim.py --linux    # adds Linux targets and the bot's library
"""

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "sim"
BUILD = ROOT / "build"

ENGINE_SOURCES = ["rng.cpp", "terrain.cpp", "game.cpp", "agent.cpp", "features.cpp", "value_net.cpp"]
PROGRAMS = {
    "sim_replay": "replay_main.cpp",
    "sim_bench": "bench_main.cpp",
    "sim_search": "search_main.cpp",
    "sim_live": "live_main.cpp",
    "sim_datagen": "datagen_main.cpp",
    "sim_nettest": "nettest_main.cpp",
    "sim_patience": "patience_main.cpp",
    "sim_triple": "triple_main.cpp",
}

# -ffp-contract=off keeps floating-point results identical to the JS engine's.
FLAGS = ["-std=c++20", "-O2", "-DNDEBUG", "-ffp-contract=off", "-fno-sanitize=undefined", "-Wno-nullability-completeness"]
LINUX_TARGET = "x86_64-linux-gnu.2.31"
BOT_DIR = ROOT / "bots" / "search_bot"


def zig_python() -> str:
    """Use this interpreter if it has ziglang, otherwise the project's .venv one."""
    if importlib.util.find_spec("ziglang") is not None:
        return sys.executable
    venv_python = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if venv_python.exists():
        return str(venv_python)
    sys.exit("ziglang not found: install requirements-dev.txt into .venv (see README)")


def compile_program(name: str, main_source: str, target: str | None = None, suffix: str = ".exe") -> None:
    command = [zig_python(), "-m", "ziglang", "c++", *FLAGS]
    if target:
        command += ["-target", target]
    command += ["-o", str(BUILD / f"{name}{suffix}")]
    command += [str(SIM / source) for source in [*ENGINE_SOURCES, main_source]]
    subprocess.run(command, check=True)
    print(f"built {name}{suffix}")


def compile_library(output: Path, target: str | None = None) -> None:
    command = [zig_python(), "-m", "ziglang", "c++", *FLAGS, "-shared", "-fPIC"]
    if target:
        command += ["-target", target]
    command += ["-o", str(output), *(str(SIM / source) for source in [*ENGINE_SOURCES, "capi.cpp"])]
    subprocess.run(command, check=True)
    print(f"built {output.name}")


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
    compile_library(BOT_DIR / "libsim.dll")
    if args.linux:
        compile_library(BOT_DIR / "libsim.so", LINUX_TARGET)


if __name__ == "__main__":
    main()
