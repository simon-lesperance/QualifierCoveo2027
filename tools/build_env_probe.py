"""Build experiments/env_probe: the search library in several CPU targets, plus the search bot.

    python tools/build_env_probe.py
    python tools/make_zip.py experiments/env_probe

The probe plays with every variant in turn on the server, so their speed can be compared on
the real CPU. Variants are Linux x86-64 only; one the CPU can't run is skipped at startup.
"""

import shutil
import subprocess

import build_sim
from platform_paths import ROOT

PROBE = ROOT / "experiments" / "env_probe"
SEARCH_BOT = ROOT / "bots" / "search_bot"

# The probe imports search.py, which loads libsim.so, so the generic AVX2 build takes that name.
VARIANTS = {
    "libsim.so": ["-mcpu=x86_64_v3"],
    "libsim_v2.so": ["-mcpu=x86_64_v2"],
    "libsim_clx.so": ["-mcpu=cascadelake"],
    "libsim_clx512.so": ["-mcpu=cascadelake", "-mprefer-vector-width=512"],
}


def main() -> None:
    flags = [flag for flag in build_sim.FLAGS if flag != "-march=native"]
    sources = [str(build_sim.SIM / name) for name in [*build_sim.ENGINE_SOURCES, "capi.cpp"]]
    for name, target_flags in VARIANTS.items():
        command = [
            build_sim.zig_python(), "-m", "ziglang", "c++", *flags, *target_flags,
            "-shared", "-fPIC", "-target", build_sim.LINUX_TARGET,
            "-o", str(PROBE / name), *sources,
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            # Zig rebuilds libc++ per CPU and warns a lot, so only show the end on failure.
            raise SystemExit(f"{name} failed:\n{result.stderr[-4000:]}")
        print(f"built {name}")
    shutil.copyfile(SEARCH_BOT / "bot.py", PROBE / "search.py")
    print("copied search.py")


if __name__ == "__main__":
    main()
