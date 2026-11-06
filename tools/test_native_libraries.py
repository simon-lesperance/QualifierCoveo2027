"""Smoke checks for the host libraries built by tools/build_sim.py."""

import ctypes
import subprocess
import sys
import unittest

from platform_paths import ROOT, library_name


class NativeLibrariesTest(unittest.TestCase):
    def test_search_bot_loads_library_and_calls_agent(self):
        code = (
            "import ctypes, bot; "
            "agent = bot.Bot(); "
            "kind, x, y = ctypes.c_int(), ctypes.c_int(), ctypes.c_int(); "
            "bot.LIBRARY.sim_agent_act(agent.agent, ctypes.byref(agent.observation), 1.0, "
            "ctypes.byref(kind), ctypes.byref(x), ctypes.byref(y)); "
            "assert kind.value == 0"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT / "bots" / "search_bot",
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_search_bot_anytime_search_stops_from_another_thread(self):
        code = (
            "import ctypes, threading, time, bot; "
            "agent = bot.Bot(); "
            "assert bot.LIBRARY.sim_agent_begin(agent.agent, ctypes.byref(agent.observation)) == 1; "
            "kind, x, y = ctypes.c_int(), ctypes.c_int(), ctypes.c_int(); "
            "args = (ctypes.byref(kind), ctypes.byref(x), ctypes.byref(y)); "
            "bot.LIBRARY.sim_agent_resume(agent.agent, 5.0, *args); "
            "threading.Timer(0.05, bot.LIBRARY.sim_agent_stop, (agent.agent,)).start(); "
            "start = time.perf_counter(); "
            "bot.LIBRARY.sim_agent_resume(agent.agent, 10000.0, *args); "
            "assert time.perf_counter() - start < 2, 'stop did not interrupt resume'"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT / "bots" / "search_bot",
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_search_bot_late_tick_shortens_the_decision_deadline(self):
        code = (
            "import time, types, bot; "
            "agent = bot.Bot(); "
            "agent.fill = lambda message: None; "
            "o = agent.observation; o.tick = 10; o.dino_count = 3; "
            "exec('for i in range(3): o.dino_id[i] = i + 1; o.dino_x[i] = 4 + 5 * i; o.dino_y[i] = 8'); "
            "message = types.SimpleNamespace(tick=1, score=0); "
            "agent.replied_at = time.perf_counter() - (bot.ARRIVAL_GRACE_MS + 60) / 1000; "
            "start = time.perf_counter(); "
            "agent.get_next_move(message); "
            "elapsed = (time.perf_counter() - start) * 1000; "
            "assert agent.lagged == 1 and agent.max_lag_ms >= 60, agent.max_lag_ms; "
            "assert elapsed < bot.LAST_DECISION_MS - 50, elapsed"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT / "bots" / "search_bot",
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_native_probe_exports_bfs(self):
        library = ctypes.CDLL(str(ROOT / "experiments" / "probe_bot" / library_name("probe")))
        library.bfs_many.restype = ctypes.c_int64
        library.bfs_many.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
        self.assertEqual(library.bfs_many(bytes(400), 210, 2), 800)
        blocked = bytearray([1] * 400)
        blocked[210] = 0
        self.assertEqual(library.bfs_many(bytes(blocked), 210, 2), 2)


if __name__ == "__main__":
    unittest.main()
