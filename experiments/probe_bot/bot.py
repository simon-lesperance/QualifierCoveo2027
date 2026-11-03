"""Measures the server environment. Plays the starter strategy so the game still scores.

Read the results from bot.log after `<launcher> download <gameId>`:
  PROBE lines   the machine, what installed, and how fast it runs
  MAP lines     a fingerprint of the opening map; compare across games to spot repeated seeds
  STATE lines   a per-tick hash; replay the seed locally and diff to check parity

Slow setup belongs at module level. The server closes a connection that has not
registered within 5 s, and application.py builds Bot() after connecting, but it
imports this module before connecting.

The stall tests run after PARITY_END_TICK so they cannot disturb the parity check.
A late tick loses its action, which would otherwise change the rest of the game.
"""

import ctypes
import hashlib
import importlib.metadata
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

from game_message import *

PARITY_END_TICK = 899

# (first tick, last tick, milliseconds to stall). Confirms the real tick limit;
# twenty late ticks at worst, well below the kick limit of 50.
STALL_WINDOWS = [(900, 904, 100), (920, 924, 130), (940, 944, 145), (960, 964, 170)]

BFS_COUNT = 100_000


def stall_ms(tick: int) -> int:
    for first, last, ms in STALL_WINDOWS:
        if first <= tick <= last:
            return ms
    return 0


def read_first_line(path: str) -> str:
    try:
        with open(path) as file:
            return file.readline().strip()
    except OSError:
        return "unavailable"


def probe_cpu() -> None:
    try:
        with open("/proc/cpuinfo") as file:
            lines = file.read().splitlines()
    except OSError:
        print("PROBE cpuinfo unavailable")
        return
    model = next((line.split(":", 1)[1].strip() for line in lines if line.startswith("model name")), "?")
    flags = next((line.split(":", 1)[1].split() for line in lines if line.startswith("flags")), [])
    print(f"PROBE cpu_model={model} avx2={'avx2' in flags} avx512f={'avx512f' in flags}")


def probe_disk() -> None:
    for directory in (".", tempfile.gettempdir()):
        try:
            with tempfile.NamedTemporaryFile(dir=directory) as file:
                file.write(b"probe")
            print(f"PROBE writable {directory}=True")
        except OSError as error:
            print(f"PROBE writable {directory}=False {error!r}")


def python_loop_seconds(iterations: int = 2_000_000) -> float:
    start = time.perf_counter()
    total = 0
    for i in range(iterations):
        total += i & 3
    return time.perf_counter() - start


def probe_processes() -> None:
    single = python_loop_seconds()
    print(f"PROBE python_loop_mops={2.0 / single:.1f}", flush=True)

    # Plain interpreters rather than multiprocessing, which would re-import this
    # module in each worker and run these probes again. Ten times the loop above,
    # so interpreter startup does not dominate the timing.
    code = "total = 0\nfor i in range(20_000_000): total += i & 3"
    for workers in (2, 4):
        start = time.perf_counter()
        children = [subprocess.Popen([sys.executable, "-I", "-c", code]) for _ in range(workers)]
        for child in children:
            child.wait(timeout=60)
        elapsed = time.perf_counter() - start
        print(f"PROBE processes={workers} speedup={workers * single * 10 / elapsed:.2f}", flush=True)


def probe_numba() -> None:
    start = time.perf_counter()
    try:
        import numba
        import numpy as np
    except Exception as error:
        print(f"PROBE numba unavailable: {error!r}", flush=True)
        return
    print(f"PROBE numba version={numba.__version__} numpy={np.__version__} import_s={time.perf_counter() - start:.3f}")

    @numba.njit(nogil=True)
    def bfs(blocked, start, dist, queue):
        dist[:] = -1
        head, tail = 0, 1
        dist[start] = 0
        queue[0] = start
        while head < tail:
            v = queue[head]
            head += 1
            y, x = v // 20, v % 20
            for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < 20 and 0 <= nx < 20:
                    u = ny * 20 + nx
                    if dist[u] == -1 and not blocked[u]:
                        dist[u] = dist[v] + 1
                        queue[tail] = u
                        tail += 1
        return tail

    @numba.njit(nogil=True)
    def bfs_many(blocked, start, dist, queue, count):
        total = 0
        for _ in range(count):
            total += bfs(blocked, start, dist, queue)
        return total

    blocked = np.zeros(400, np.bool_)

    def run(count):
        return bfs_many(blocked, 210, np.zeros(400, np.int32), np.zeros(400, np.int32), count)

    start = time.perf_counter()
    run(1)
    print(f"PROBE numba jit_s={time.perf_counter() - start:.3f}", flush=True)

    start = time.perf_counter()
    run(BFS_COUNT)
    single = time.perf_counter() - start
    print(f"PROBE numba bfs_us={single / BFS_COUNT * 1e6:.2f}", flush=True)

    with ThreadPoolExecutor(2) as pool:
        start = time.perf_counter()
        list(pool.map(run, [BFS_COUNT] * 2))
        elapsed = time.perf_counter() - start
    print(f"PROBE numba threads=2 speedup={2 * single / elapsed:.2f}", flush=True)


def probe_native() -> None:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "libprobe.so")
    try:
        library = ctypes.CDLL(path)
    except OSError as error:
        print(f"PROBE native load failed: {error!r}", flush=True)
        return

    library.bfs_many.restype = ctypes.c_int64
    library.bfs_many.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
    blocked = bytes(400)

    start = time.perf_counter()
    library.bfs_many(blocked, 210, BFS_COUNT)
    print(f"PROBE native bfs_us={(time.perf_counter() - start) / BFS_COUNT * 1e6:.2f}", flush=True)

    start = time.perf_counter()
    for _ in range(10_000):
        library.bfs_many(blocked, 210, 0)
    print(f"PROBE native call_overhead_us={(time.perf_counter() - start) / 10_000 * 1e6:.2f}", flush=True)


def run_startup_probes() -> None:
    started = time.perf_counter()
    print(f"PROBE python={platform.python_version()} machine={platform.machine()}")
    print(f"PROBE platform={platform.platform()}")
    print(f"PROBE cpu_count={os.cpu_count()}")
    if hasattr(os, "sched_getaffinity"):
        print(f"PROBE usable_cpus={len(os.sched_getaffinity(0))}")
    probe_cpu()
    print(f"PROBE cgroup_cpu_max={read_first_line('/sys/fs/cgroup/cpu.max')}")
    print(f"PROBE meminfo={read_first_line('/proc/meminfo')}")
    print(f"PROBE cwd={os.getcwd()}")
    probe_disk()
    print(f"PROBE tempdir={tempfile.gettempdir()}")
    for tool in ("gcc", "cc", "g++", "clang", "rustc", "cargo"):
        print(f"PROBE tool {tool}={shutil.which(tool)}")
    packages = sorted(f"{d.name}=={d.version}" for d in importlib.metadata.distributions())
    print(f"PROBE packages={packages}", flush=True)
    probe_processes()
    probe_numba()
    probe_native()
    print(f"PROBE startup_s={time.perf_counter() - started:.2f}", flush=True)


def print_map_fingerprint(game_message: TeamGameState) -> None:
    tiles = [tile for row in game_message.map.tiles for tile in row]
    elevations = "".join(chr(48 + tile.elevation) for tile in tiles)
    fingerprint = hashlib.sha1(elevations.encode()).hexdigest()[:12]
    print(
        f"MAP fingerprint={fingerprint} mountains={len(game_message.mountains)} "
        f"impassable={sum(tile.isImpassable for tile in tiles)} "
        f"mean_elevation={sum(tile.elevation for tile in tiles) / len(tiles):.2f}"
    )
    print(f"PROBE constants={game_message.constants}")
    print(f"PROBE first_dinos={[(d.id, d.name, d.position.x, d.position.y) for d in game_message.dinosaurs]}")
    print(f"PROBE map_size={game_message.map.width}x{game_message.map.height}", flush=True)


run_startup_probes()


class Bot:
    def __init__(self):
        self.last_sent = None
        self.gaps_ms = []
        self.mapped = False

    def get_next_move(self, game_message: TeamGameState) -> list[Action]:
        now = time.perf_counter()
        if not self.mapped:
            print_map_fingerprint(game_message)
            self.mapped = True

        if self.last_sent is not None:
            self.gaps_ms.append((now - self.last_sent) * 1000)

        dinos = sorted((d.id, d.position.x, d.position.y) for d in game_message.dinosaurs)
        state = f"{game_message.map.origin.y}|{game_message.score}|{dinos}"
        print(f"STATE {game_message.tick} {game_message.score} {hashlib.sha1(state.encode()).hexdigest()[:12]}")

        if game_message.tick % 100 == 0 and self.gaps_ms:
            ordered = sorted(self.gaps_ms)
            print(
                f"PROBE gap_ms tick={game_message.tick} median={ordered[len(ordered) // 2]:.1f} "
                f"max={ordered[-1]:.1f}",
                flush=True,
            )
            self.gaps_ms = []

        stall = stall_ms(game_message.tick)
        if stall:
            time.sleep(stall / 1000)

        actions = []
        if game_message.dinosaurs:
            actions.append(LaunchMeteorAction(target=game_message.dinosaurs[0].position))

        self.last_sent = time.perf_counter()
        return actions
