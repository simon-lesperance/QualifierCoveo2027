"""Reports the server's CPU and environment, and measures where tick freezes come from.

Plays the current search bot (search.py, a copy of bots/search_bot/bot.py) so the game is
realistic, rotating each tick between libsim builds for different CPU targets to compare
their speed on the server's CPU. Build and package with:

    python tools/build_env_probe.py
    python tools/make_zip.py experiments/env_probe

Then read bot.log from `<launcher> download <gameId>`, or run tools/analyze_env_probe.py on it:
  ENV lines      the machine, once at startup (secrets such as TOKEN are never printed)
  VARIANT lines  which libsim builds run on this CPU
  TICK lines     per tick: our time, the cycle time, and what the CPU did meanwhile
  PROCS lines    the other processes we can see and the CPU time they used
  SUMMARY lines  per-variant speed and stall statistics
"""

import ctypes
import gc
import glob
import os
import platform
import resource
import shutil
import subprocess
import sys
import sysconfig
import threading
import time

os.environ.setdefault("BOT_FIRST_MS", "75")
os.environ.setdefault("BOT_LAST_MS", "95")

import search  # noqa: E402  (reads the BOT_* settings above at import)
from game_message import *  # noqa: E402,F403

HERE = os.path.dirname(os.path.abspath(__file__))
CLOCK_TICK_MS = 1000 / os.sysconf("SC_CLK_TCK")
STALL_SAMPLE_S = 0.0005
STALL_REPORT_MS = 3.0
# Environment variables worth printing in full. Everything else is listed by name only.
SHOWN_ENV = {"PATH", "HOME", "USER", "LANG", "TZ", "HOSTNAME", "PYTHONPATH", "PYTHONHASHSEED",
             "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE", "OMP_NUM_THREADS", "MALLOC_ARENA_MAX"}


def read(path: str, limit: int = 4000) -> str:
    try:
        with open(path) as file:
            return file.read(limit).strip()
    except OSError as error:
        return f"unavailable ({error.__class__.__name__})"


def env(key: str, value) -> None:
    print(f"ENV {key}={value}", flush=True)


def cgroup_dirs() -> list[str]:
    """The cgroup folders that hold our limits, for cgroup v2 and v1 layouts."""
    dirs = []
    for line in read("/proc/self/cgroup").splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        controllers, path = parts[1], parts[2]
        if controllers == "":
            dirs += [f"/sys/fs/cgroup{path}", "/sys/fs/cgroup"]
        elif "cpu" in controllers.split(",") or "memory" in controllers.split(","):
            for name in controllers.split(","):
                dirs += [f"/sys/fs/cgroup/{name}{path}", f"/sys/fs/cgroup/{name}"]
            dirs.append(f"/sys/fs/cgroup/{controllers}")
    seen = []
    for folder in dirs:
        if os.path.isdir(folder) and folder not in seen:
            seen.append(folder)
    return seen


CGROUP_DIRS = cgroup_dirs()


def cgroup_file(*names: str) -> str | None:
    for folder in CGROUP_DIRS:
        for name in names:
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                return path
    return None


CPU_STAT = cgroup_file("cpu.stat")
PSI_CPU = "/proc/pressure/cpu" if os.path.isfile("/proc/pressure/cpu") else None


def probe_cpu() -> None:
    info = read("/proc/cpuinfo", 200_000)
    first = info.split("\n\n")[0]
    for line in first.splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key in ("vendor_id", "cpu family", "model", "model name", "stepping", "microcode",
                   "cpu MHz", "cache size", "siblings", "cpu cores", "bogomips", "address sizes"):
            env(f"cpu.{key.replace(' ', '_')}", value)
        elif key in ("flags", "bugs"):
            env(f"cpu.{key}", value)
    env("cpu.processors", info.count("processor\t:"))
    for index in sorted(glob.glob("/sys/devices/system/cpu/cpu0/cache/index*")):
        env(f"cpu.cache.{os.path.basename(index)}",
            f"L{read(index + '/level')} {read(index + '/type')} {read(index + '/size')}")
    env("cpu.clocksource", read("/sys/devices/system/clocksource/clocksource0/current_clocksource"))
    env("cpu.available_clocksources",
        read("/sys/devices/system/clocksource/clocksource0/available_clocksource"))
    env("cpu.max_freq_khz", read("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq"))
    env("cpu.online", read("/sys/devices/system/cpu/online"))
    env("hypervisor", read("/sys/hypervisor/type"))
    for name in ("sys_vendor", "product_name", "bios_vendor"):
        env(f"dmi.{name}", read(f"/sys/class/dmi/id/{name}"))


def probe_compiler_target() -> None:
    """What `-march=native` would pick here, from the CPU flags, for the zig/clang -mcpu option."""
    flags = set(read("/proc/cpuinfo", 200_000).split("flags", 1)[-1].split("\n", 1)[0].split())
    if {"avx512f", "avx512vnni"} <= flags:
        guess = "cascadelake or newer (AVX-512 VNNI)"
    elif "avx512f" in flags:
        guess = "skylake-avx512"
    elif {"avx2", "bmi2", "fma"} <= flags:
        guess = "x86_64_v3 (AVX2)"
    else:
        guess = "x86_64_v2 or older"
    env("cpu.target_guess", guess)


def probe_system() -> None:
    env("python", f"{platform.python_version()} {platform.python_implementation()} "
                  f"{sys.version.splitlines()[0]}")
    env("python.gil_disabled", sysconfig.get_config_var("Py_GIL_DISABLED"))
    env("python.executable", sys.executable)
    env("platform", platform.platform())
    env("uname", " ".join(os.uname()))
    env("os_release", read("/etc/os-release").replace("\n", " | "))
    env("glibc", " ".join(platform.libc_ver()))
    env("cpu_count", os.cpu_count())
    if hasattr(os, "sched_getaffinity"):
        env("usable_cpus", sorted(os.sched_getaffinity(0)))
    env("loadavg", read("/proc/loadavg"))
    env("uptime_s", read("/proc/uptime"))
    for line in read("/proc/meminfo").splitlines():
        if line.split(":")[0] in ("MemTotal", "MemFree", "MemAvailable", "SwapTotal", "Hugepagesize"):
            env(f"mem.{line.split(':')[0]}", line.split(":")[1].strip())
    env("cgroup", read("/proc/self/cgroup").replace("\n", " | "))
    env("cgroup.dirs", CGROUP_DIRS)
    for name in ("cpu.max", "cpu.cfs_quota_us", "cpu.cfs_period_us", "cpu.shares", "cpu.weight",
                 "cpu.stat", "memory.max", "memory.limit_in_bytes", "memory.high", "pids.max",
                 "cpuset.cpus.effective", "cpu.pressure"):
        path = cgroup_file(name)
        if path:
            env(f"cgroup.{name}", read(path).replace("\n", " | "))
    env("psi.cpu", read("/proc/pressure/cpu").replace("\n", " | "))
    env("psi.memory", read("/proc/pressure/memory").replace("\n", " | "))
    env("psi.io", read("/proc/pressure/io").replace("\n", " | "))
    env("sched.autogroup", read("/proc/self/autogroup"))
    env("kernel.sched", {name: read(f"/proc/sys/kernel/{name}") for name in (
        "sched_child_runs_first", "sched_rt_runtime_us", "sched_autogroup_enabled")})
    env("clk_tck", os.sysconf("SC_CLK_TCK"))
    env("status", {line.split(":")[0]: line.split(":")[1].strip()
                   for line in read("/proc/self/status").splitlines()
                   if line.split(":")[0] in ("Cpus_allowed_list", "Mems_allowed_list", "Threads",
                                             "Seccomp", "NoNewPrivs", "CapEff", "VmRSS")})
    env("limits", {name: resource.getrlimit(getattr(resource, name)) for name in (
        "RLIMIT_CPU", "RLIMIT_AS", "RLIMIT_NOFILE", "RLIMIT_NPROC", "RLIMIT_STACK")})
    env("nice", os.nice(0))
    env("sched_policy", os.sched_getscheduler(0) if hasattr(os, "sched_getscheduler") else "?")
    env("uid_gid", f"{os.getuid()}:{os.getgid()}")
    env("cwd", os.getcwd())
    env("listing", sorted(os.listdir(".")))
    for folder in (".", "/tmp", "/dev/shm"):
        try:
            usage = shutil.disk_usage(folder)
            env(f"disk.{folder}", f"total={usage.total >> 20}MB free={usage.free >> 20}MB "
                                  f"writable={os.access(folder, os.W_OK)}")
        except OSError as error:
            env(f"disk.{folder}", repr(error))
    env("mounts", "; ".join(" ".join(line.split()[:4]) for line in read("/proc/mounts").splitlines()))
    env("env_names", sorted(os.environ))
    env("env_values", {key: os.environ[key] for key in sorted(SHOWN_ENV & set(os.environ))})
    env("tools", {tool: shutil.which(tool) for tool in (
        "gcc", "cc", "g++", "clang", "zig", "rustc", "perf", "taskset", "chrt", "nproc")})
    env("perf_counter", time.get_clock_info("perf_counter"))
    env("gc", f"thresholds={gc.get_threshold()} counts={gc.get_count()}")


def clock_cost_ns() -> float:
    """Cost of a monotonic clock read. On a slow clocksource (xen, hpet) it is a syscall."""
    count = 200_000
    clock = time.perf_counter_ns
    start = clock()
    for _ in range(count):
        clock()
    return (clock() - start) / count


def list_processes() -> dict:
    """Visible processes: pid -> (name, cpu ms). Only names, never arguments or environments."""
    found = {}
    for path in glob.glob("/proc/[0-9]*/stat"):
        try:
            with open(path) as file:
                text = file.read()
        except OSError:
            continue
        name = text[text.index("(") + 1:text.rindex(")")]
        fields = text[text.rindex(")") + 2:].split()
        found[int(path.split("/")[2])] = (name, (int(fields[11]) + int(fields[12])) * CLOCK_TICK_MS)
    return found


def check_variants() -> list[str]:
    """Each libsim build runs once in a subprocess, since an unsupported CPU target crashes."""
    usable = []
    for path in sorted(glob.glob(os.path.join(HERE, "libsim*.so"))):
        name = os.path.basename(path)
        code = (
            "import ctypes, search; "
            f"lib = search.load_library({name!r}); "
            "agent = lib.sim_agent_create(2, 4, 6, 5, 3.0, 0.5); obs = search.Observation(); "
            "k, x, y = ctypes.c_int(), ctypes.c_int(), ctypes.c_int(); "
            "lib.sim_agent_act(agent, ctypes.byref(obs), 5.0, ctypes.byref(k), ctypes.byref(x), ctypes.byref(y))"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=HERE, capture_output=True, text=True,
                                timeout=60)
        print(f"VARIANT {name} returncode={result.returncode} {result.stderr.strip()[-300:]}", flush=True)
        if result.returncode == 0:
            usable.append(name)
    return usable


STARTED = time.perf_counter()
print("ENV probe starting", flush=True)
probe_cpu()
probe_compiler_target()
probe_system()
env("clock_cost_ns", f"{clock_cost_ns():.1f}")
PROCESSES_AT_START = list_processes()
env("processes", sorted((pid, name) for pid, (name, _) in PROCESSES_AT_START.items()))
VARIANTS = check_variants()
LIBRARIES = {name: search.load_library(name) for name in VARIANTS}


def benchmark_observation() -> "search.Observation":
    """A fixed mid-game-like position: rolling terrain, ten dinosaurs, two corpses."""
    observation = search.Observation()
    observation.tick = 50
    for cell in range(search.CELLS):
        row, column = divmod(cell, search.WIDTH)
        observation.elevation[cell] = (row * 7 + column * 3 + (row * column) % 5) % 9
        if (row * 13 + column * 7) % 37 == 0:
            observation.flags[cell] = search.MOUNTAIN | search.IMPASSABLE
    observation.dino_count = 10
    for i in range(10):
        observation.dino_id[i] = i + 1
        observation.dino_species[i] = i % 4
        observation.dino_x[i] = (i * 7 + 3) % search.WIDTH
        observation.dino_y[i] = 4 + (i * 5) % 14
        observation.dino_age[i] = 3 + i
    observation.corpses[5 * search.WIDTH + 5] = 1
    observation.corpses[12 * search.WIDTH + 14] = 1
    return observation


def benchmark_variants(rounds: int = 4, ms: float = 100.0) -> None:
    """Simulated ticks per ms on the same position, interleaved so drift hits every variant alike."""
    observation = benchmark_observation()
    kind, x, y = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
    results = {name: [] for name in VARIANTS}
    agents = {name: library.sim_agent_create(2, 4, 6, 5, 3.0, 0.5) for name, library in LIBRARIES.items()}
    for _ in range(rounds):
        for name, library in LIBRARIES.items():
            start = time.perf_counter()
            library.sim_agent_act(agents[name], ctypes.byref(observation), ms,
                                  ctypes.byref(kind), ctypes.byref(x), ctypes.byref(y))
            elapsed = (time.perf_counter() - start) * 1000
            results[name].append(library.sim_agent_last_simulated(agents[name]) / elapsed)
    for name, rates in results.items():
        print(f"BENCH variant={name} sims_per_ms={sorted(rates)[len(rates) // 2]:.1f} "
              f"all={[round(rate, 1) for rate in rates]}", flush=True)


benchmark_variants()
env("startup_s", f"{time.perf_counter() - STARTED:.2f}")


class StallSampler(threading.Thread):
    """Sleeps 0.5 ms in a loop. A long gap means this process got no CPU (or the GIL) meanwhile."""

    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.stalls = []  # (end time, gap ms)

    def run(self) -> None:
        clock = time.perf_counter
        last = clock()
        while True:
            time.sleep(STALL_SAMPLE_S)
            now = clock()
            gap = (now - last) * 1000
            if gap > STALL_REPORT_MS:
                with self.lock:
                    self.stalls.append((now, gap))
            last = now

    def take(self) -> list:
        with self.lock:
            stalls, self.stalls = self.stalls, []
        return stalls


class GcTimer:
    def __init__(self):
        self.started = 0.0
        self.total_ms = 0.0
        self.count = 0
        gc.callbacks.append(self.callback)

    def callback(self, phase: str, info: dict) -> None:
        if phase == "start":
            self.started = time.perf_counter()
        else:
            self.total_ms += (time.perf_counter() - self.started) * 1000
            self.count += 1


def thread_schedstat() -> tuple[int, int]:
    """(ns on CPU, ns runnable but waiting for a CPU) for the calling thread."""
    try:
        with open("/proc/thread-self/schedstat") as file:
            run_ns, wait_ns, _ = file.read().split()
        return int(run_ns), int(wait_ns)
    except (OSError, ValueError):
        return 0, 0


def counters() -> dict:
    values = {}
    try:
        with open("/proc/stat") as file:
            cpu = file.readline().split()[1:]
        values["busy_ms"] = sum(int(v) for i, v in enumerate(cpu[:8]) if i not in (3, 4)) * CLOCK_TICK_MS
        values["steal_ms"] = int(cpu[7]) * CLOCK_TICK_MS
    except (OSError, IndexError, ValueError):
        pass
    if CPU_STAT:
        for line in read(CPU_STAT).splitlines():
            key, _, value = line.partition(" ")
            if key in ("nr_throttled", "throttled_usec", "throttled_time", "usage_usec"):
                values[key] = int(value)
    if PSI_CPU:
        for line in read(PSI_CPU).splitlines():
            if line.startswith("some"):
                values["psi_some_us"] = int(line.rsplit("total=", 1)[1])
    usage = resource.getrusage(resource.RUSAGE_SELF)
    values["own_cpu_ms"] = (usage.ru_utime + usage.ru_stime) * 1000
    values["minflt"] = usage.ru_minflt
    values["majflt"] = usage.ru_majflt
    values["invol"] = usage.ru_nivcsw
    return values


class ProbeSearchBot(search.Bot):
    """The search bot, plus the worker thread's own scheduler statistics."""

    def search(self, start: float) -> None:
        before = thread_schedstat()
        try:
            super().search(start)
        finally:
            after = thread_schedstat()
            self.worker_run_ms = (after[0] - before[0]) / 1e6
            self.worker_wait_ms = (after[1] - before[1]) / 1e6


class Bot:
    def __init__(self):
        self.bots = {}
        for name in VARIANTS:
            search.LIBRARY = LIBRARIES[name]
            self.bots[name] = ProbeSearchBot()
        self.current = None
        self.sampler = StallSampler()
        self.sampler.start()
        self.gc = GcTimer()
        self.previous = None  # (tick, start, wall ms, counters, our ms, variant, simulated, worker stats)
        self.speed = {name: [] for name in VARIANTS}
        self.cycles = []
        self.stall_count = 0
        print("TICK fields: tick wall_ms ours_ms cycle_ms max_stall_ms@offset stalls>3ms worker_run_ms "
              "worker_wait_ms steal_ms busy_ms own_cpu_ms psi_us throttled invol minflt gc_ms variant sims",
              flush=True)

    def log_previous(self, now: float, sample: dict) -> None:
        tick, start, wall_ms, before, ours_ms, variant, simulated, worker = self.previous
        stalls = [(end, gap) for end, gap in self.sampler.take()]
        self.stall_count += len(stalls)
        worst = max(stalls, key=lambda item: item[1], default=(start, 0.0))
        offset = (worst[0] - worst[1] / 1000 - start) * 1000

        def delta(key):
            return sample.get(key, 0) - before.get(key, 0)

        cycle_ms = (now - start) * 1000
        self.cycles.append(cycle_ms)
        throttled = f"{delta('nr_throttled')}/{delta('throttled_usec') or delta('throttled_time')}"
        print(
            f"TICK {tick} {wall_ms:.1f} {ours_ms:.1f} {cycle_ms:.1f} {worst[1]:.1f}@{offset:.0f} {len(stalls)} "
            f"{worker[0]:.1f} {worker[1]:.1f} {delta('steal_ms'):.0f} {delta('busy_ms'):.0f} "
            f"{delta('own_cpu_ms'):.1f} {delta('psi_some_us')} {throttled} {delta('invol')} "
            f"{delta('minflt')} {self.gc.total_ms:.2f} {variant} {simulated}",
            flush=True,
        )
        self.gc.total_ms = 0.0

    def summary(self, tick: int) -> None:
        for name, rates in self.speed.items():
            if rates:
                ordered = sorted(rates)
                print(f"SUMMARY tick={tick} variant={name} ticks={len(rates)} "
                      f"sims_per_ms median={ordered[len(ordered) // 2]:.1f} "
                      f"p10={ordered[len(ordered) // 10]:.1f} p90={ordered[len(ordered) * 9 // 10]:.1f}",
                      flush=True)
        if self.cycles:
            ordered = sorted(self.cycles)
            print(f"SUMMARY tick={tick} cycle_ms median={ordered[len(ordered) // 2]:.1f} "
                  f"p99={ordered[len(ordered) * 99 // 100]:.1f} max={ordered[-1]:.1f} "
                  f"stalls>3ms={self.stall_count} gc_collections={self.gc.count}", flush=True)
        now = list_processes()
        own = os.getpid()
        used = []
        for pid, (name, cpu_ms) in now.items():
            before = PROCESSES_AT_START.get(pid, (name, 0.0))[1]
            used.append((round(cpu_ms - before), pid, name, "self" if pid == own else ""))
        print(f"PROCS tick={tick} loadavg={read('/proc/loadavg')} "
              f"cpu_ms_since_start={sorted(used, reverse=True)[:12]}", flush=True)

    def get_next_move(self, game_message: TeamGameState) -> list[Action]:
        start = time.perf_counter()
        wall_ms = time.time() * 1000
        sample = counters()
        if self.previous is not None:
            self.log_previous(start, sample)

        # Each variant keeps its own agent; finish the last one's search before switching libraries.
        if self.current is not None:
            self.bots[self.current].wait_for_worker()
        variant = VARIANTS[game_message.tick % len(VARIANTS)]
        self.current = variant
        search.LIBRARY = LIBRARIES[variant]
        bot = self.bots[variant]
        bot.worker_run_ms = bot.worker_wait_ms = 0.0
        actions = bot.get_next_move(game_message)
        ours_ms = (time.perf_counter() - start) * 1000

        simulated = search.LIBRARY.sim_agent_last_simulated(bot.agent)
        if simulated > 0:
            self.speed[variant].append(simulated / ours_ms)
        self.previous = (game_message.tick, start, wall_ms, sample, ours_ms, variant, simulated,
                         (bot.worker_run_ms, bot.worker_wait_ms))
        if game_message.tick % 250 == 0 or game_message.tick >= 999:
            self.summary(game_message.tick)
        return actions
