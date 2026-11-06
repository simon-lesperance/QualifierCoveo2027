"""Build the C++ simulator with Zig, which needs no other compiler.

    python tools/build_sim.py            # Windows tools in build/
    python tools/build_sim.py --linux    # adds Linux builds, including the bot's library

See tools/build_pgo.py for local profile training and a PGO library.
"""

import argparse
import importlib.util
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from platform_paths import BUILD, ROOT, executable_suffix, library_name

SIM = ROOT / "sim"

ENGINE_SOURCES = [
    "rng.cpp",
    "terrain.cpp",
    "game.cpp",
    "agent.cpp",
    "features.cpp",
    "value_net.cpp",
    "policy_net.cpp",
]
PROGRAMS = {
    "sim_replay": "replay_main.cpp",
    "sim_bench": "bench_main.cpp",
    "sim_search": "search_main.cpp",
    "sim_live": "live_main.cpp",
    "sim_datagen": "datagen_main.cpp",
    "sim_nettest": "nettest_main.cpp",
    "sim_patience": "patience_main.cpp",
    "sim_triple": "triple_main.cpp",
    "sim_policygen": "policygen_main.cpp",
    "sim_policytest": "policytest_main.cpp",
    "sim_pocket": "pocket_main.cpp",
    "sim_carve": "carve_main.cpp",
    "sim_probe": "probe_main.cpp",
}

# -ffp-contract=off keeps floating-point results identical to the JS engine's.
FLAGS = [
    "-std=c++20",
    "-O2",
    "-DNDEBUG",
    "-ffp-contract=off",
    "-fno-sanitize=undefined",
    "-Wno-nullability-completeness",
    "-march=x86_64_v3",
]
# Zig's plain -mtune selects a CPU and can broaden the ISA to AVX-512.
SERVER_TUNE_FLAGS = ["-Xclang", "-tune-cpu", "-Xclang", "cascadelake"]
LINUX_TARGET = "x86_64-linux-gnu.2.31"
WINDOWS_TARGET = "x86_64-windows-gnu"
BOT_DIR = ROOT / "bots" / "search_bot"
PROBE_DIR = ROOT / "experiments" / "probe_bot"


def profile_flags(generate: bool = False, *, runtime: Path | None = None,
                  profile: Path | None = None) -> list[str]:
    if generate:
        if runtime is None or not runtime.is_file():
            raise ValueError("instrumentation needs an existing --profile-runtime archive")
        # Zig emits counters but does not automatically link compiler-rt's profile runtime.
        return ["-fprofile-generate", "-Wl,-u,__llvm_profile_runtime", str(runtime)]
    if profile is not None:
        if not profile.is_file():
            raise ValueError(f"profile not found: {profile}")
        return [f"-fprofile-use={profile}", "-Werror=profile-instr-out-of-date"]
    return []


def zig_python() -> str:
    if importlib.util.find_spec("ziglang") is not None:
        return sys.executable
    venv_python = ROOT / ".venv" / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    if venv_python.exists():
        return str(venv_python)
    sys.exit("ziglang not found: install requirements-dev.txt into .venv")


def compile_program(
    name: str,
    main_source: str,
    target: str | None = None,
    suffix: str | None = None,
    *,
    extra_flags: Sequence[str] = (),
    output_dir: Path = BUILD,
    library: Path | None = None,
) -> None:
    if suffix is None:
        suffix = executable_suffix()
    command = [zig_python(), "-m", "ziglang", "c++", *FLAGS, *extra_flags]
    if target:
        command += ["-target", target]
    command += ["-o", str(output_dir / f"{name}{suffix}")]
    sources = [*ENGINE_SOURCES, main_source] if library is None else [main_source]
    command += [str(SIM / source) for source in sources]
    if library is not None:
        command += ["-L", str(library.parent), "-lsim", "-Wl,-rpath,$ORIGIN"]
    subprocess.run(command, check=True)
    print(f"built {name}{suffix}")


def compile_shared(
    output: Path, sources: list[Path], target: str | None = None, *, c: bool = False,
    extra_flags: Sequence[str] = (),
) -> None:
    flags = ["-O2"] if c else FLAGS
    command = [
        zig_python(), "-m", "ziglang", "cc" if c else "c++",
        *flags, *extra_flags, "-shared",
    ]
    windows = "windows" in target if target else sys.platform == "win32"
    if not windows:
        command.append("-fPIC")
    if target:
        command += ["-target", target]
    command += ["-o", str(output), *(str(source) for source in sources)]
    subprocess.run(command, check=True)
    print(f"built {output.name}")


def compile_library(
    output: Path, target: str | None = None, *, extra_flags: Sequence[str] = ()
) -> None:
    compile_shared(
        output, [SIM / source for source in [*ENGINE_SOURCES, "capi.cpp"]], target,
        extra_flags=extra_flags,
    )


def compile_vec_env(
    output: Path, target: str | None = None, *, extra_flags: Sequence[str] = ()
) -> None:
    compile_shared(
        output, [SIM / source for source in [*ENGINE_SOURCES, "vec_env.cpp"]], target,
        extra_flags=extra_flags,
    )


def build_targets(linux: bool, windows: bool) -> list[tuple[str, str | None]]:
    targets = {sys.platform: None}
    if linux:
        targets["linux"] = LINUX_TARGET
    if windows:
        targets["win32"] = WINDOWS_TARGET
    return list(targets.items())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--linux", action="store_true", help="also build x86-64 Linux artifacts")
    parser.add_argument("--windows", action="store_true", help="also build x86-64 Windows artifacts")
    parser.add_argument("--only", nargs="+", choices=[*PROGRAMS, "libsim", "libvec", "libprobe"],
                        help="build only these artifacts")
    parser.add_argument("--pgo-use", type=Path, help="reuse a merged profile from build_pgo.py")
    parser.add_argument("--server-tune", action="store_true",
                        help="tune only the --linux bot library for Cascade Lake, keeping v3")
    args = parser.parse_args()
    if args.server_tune and (not args.linux or (args.only and "libsim" not in args.only)):
        parser.error("--server-tune requires --linux and a build including libsim")
    try:
        flags = profile_flags(profile=args.pgo_use.resolve() if args.pgo_use else None)
    except ValueError as error:
        parser.error(str(error))
    if args.pgo_use and (sys.platform != "linux" or args.windows):
        parser.error("reuse Linux training profiles only for Linux builds")
    options = {"extra_flags": flags} if flags else {}
    selected = set(args.only) if args.only else {*PROGRAMS, "libsim", "libvec", "libprobe"}

    BUILD.mkdir(exist_ok=True)
    for platform, target in build_targets(args.linux, args.windows):
        for name, source in PROGRAMS.items():
            if name in selected:
                compile_program(name, source, target, executable_suffix(platform), **options)
        if "libsim" in selected:
            library_options = options
            if args.server_tune and platform == "linux":
                library_options = {"extra_flags": [*flags, *SERVER_TUNE_FLAGS]}
            compile_library(BOT_DIR / library_name("sim", platform), target, **library_options)
        if "libprobe" in selected:
            compile_shared(
                PROBE_DIR / library_name("probe", platform),
                [PROBE_DIR / "probe_native.c"],
                target,
                c=True,
            )
        if "libvec" in selected:
            compile_vec_env(BUILD / library_name("vec", platform), target, **options)


if __name__ == "__main__":
    main()
