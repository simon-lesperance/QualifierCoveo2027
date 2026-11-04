"""Build the C++ simulator with Zig, which needs no other compiler.

    python tools/build_sim.py             # host tools and bot libraries
    python tools/build_sim.py --linux     # also build Linux upload artifacts
    python tools/build_sim.py --windows   # also cross-compile Windows artifacts
"""

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

from platform_paths import BUILD, ROOT, executable_suffix, library_name

SIM = ROOT / "sim"

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
WINDOWS_TARGET = "x86_64-windows-gnu"
BOT_DIR = ROOT / "bots" / "search_bot"
PROBE_DIR = ROOT / "experiments" / "probe_bot"


def zig_python() -> str:
    """Use this interpreter if it has ziglang, otherwise the project's .venv one."""
    if importlib.util.find_spec("ziglang") is not None:
        return sys.executable
    venv_python = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if venv_python.exists():
        return str(venv_python)
    sys.exit("ziglang not found: install requirements-dev.txt into .venv (see README)")


def compile_program(
    name: str, main_source: str, target: str | None = None, suffix: str | None = None,
) -> None:
    if suffix is None:
        suffix = executable_suffix()
    command = [zig_python(), "-m", "ziglang", "c++", *FLAGS]
    if target:
        command += ["-target", target]
    command += ["-o", str(BUILD / f"{name}{suffix}")]
    command += [str(SIM / source) for source in [*ENGINE_SOURCES, main_source]]
    subprocess.run(command, check=True)
    print(f"built {name}{suffix}")


def compile_shared(output: Path, sources: list[Path], target: str | None = None, *, c: bool = False) -> None:
    flags = ["-O2"] if c else FLAGS
    command = [zig_python(), "-m", "ziglang", "cc" if c else "c++", *flags, "-shared"]
    windows = "windows" in target if target else sys.platform == "win32"
    if not windows:
        command.append("-fPIC")
    if target:
        command += ["-target", target]
    command += ["-o", str(output), *(str(source) for source in sources)]
    subprocess.run(command, check=True)
    print(f"built {output.name}")


def compile_library(output: Path, target: str | None = None) -> None:
    compile_shared(output, [SIM / source for source in [*ENGINE_SOURCES, "capi.cpp"]], target)


def build_targets(linux: bool, windows: bool) -> list[tuple[str, str | None]]:
    targets = {sys.platform: None}
    if linux:
        targets["linux"] = LINUX_TARGET
    if windows:
        targets["win32"] = WINDOWS_TARGET
    return list(targets.items())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--linux", action="store_true", help="also build x86-64 Linux artifacts for upload")
    parser.add_argument("--windows", action="store_true", help="also build x86-64 Windows artifacts")
    args = parser.parse_args()

    BUILD.mkdir(exist_ok=True)
    for platform, target in build_targets(args.linux, args.windows):
        for name, source in PROGRAMS.items():
            compile_program(name, source, target, executable_suffix(platform))
        compile_library(BOT_DIR / library_name("sim", platform), target)
        compile_shared(
            PROBE_DIR / library_name("probe", platform), [PROBE_DIR / "probe_native.c"], target, c=True,
        )


if __name__ == "__main__":
    main()
