"""Smoke checks for the host libraries built by tools/build_sim.py."""

import ctypes
import os
import signal
import subprocess
import sys
import time
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
            "assert elapsed < bot.TIME_BUDGET_MS - 50, elapsed"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT / "bots" / "search_bot",
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_search_bot_pressure_cut_is_opt_in(self):
        code = (
            "import types, bot; "
            "assert bot.PSI_CUT_MS == 0; "
            "agent = bot.Bot(); agent.fill = lambda message: None; "
            "exec('def unexpected_read():\\n raise AssertionError(\"pressure read while disabled\")'); "
            "bot.read_cpu_pressure_us = unexpected_read; "
            "agent.get_next_move(types.SimpleNamespace(tick=1, score=0)); "
            "assert not agent.psi_enabled; assert agent.psi_cut == 0"
        )
        env = os.environ.copy()
        env.pop("BOT_PSI_CUT_MS", None)
        env.pop("BOT_PIN_CPU", None)
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT / "bots" / "search_bot",
            capture_output=True, text=True, timeout=30, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_search_bot_pressure_shortens_decision_budget(self):
        code = (
            "import time, types, bot; "
            "agent = bot.Bot(); agent.fill = lambda message: None; "
            "o = agent.observation; o.dino_count = 3; "
            "exec('for i in range(3): o.dino_id[i] = i + 1; o.dino_x[i] = 4 + 5 * i; o.dino_y[i] = 8'); "
            "readings = iter((0, 100000)); bot.read_cpu_pressure_us = lambda: next(readings); "
            "agent.get_next_move(types.SimpleNamespace(tick=1, score=0)); "
            "start = time.perf_counter(); "
            "agent.get_next_move(types.SimpleNamespace(tick=2, score=0)); "
            "elapsed = (time.perf_counter() - start) * 1000; "
            "assert agent.psi_cut == 1, agent.psi_cut; "
            "assert agent.max_psi_ms >= bot.PSI_THRESHOLD_MS, agent.max_psi_ms; "
            "assert elapsed < bot.TIME_BUDGET_MS - 40, elapsed"
        )
        env = os.environ.copy()
        env["BOT_TIME_MS"] = "95"
        env["BOT_PSI_CUT_MS"] = "30"
        env["BOT_PSI_THRESHOLD_MS"] = "20"
        env.pop("BOT_PIN_CPU", None)
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT / "bots" / "search_bot",
            capture_output=True, text=True, timeout=30, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(sys.platform.startswith("linux"), "uses SIGSTOP")
    def test_search_bot_phase_budget_records_a_pause_during_search(self):
        code = (
            "import sys, types, bot; "
            "agent = bot.Bot(); agent.fill = lambda message: None; "
            "o = agent.observation; o.dino_count = 3; "
            "exec('for i in range(3): o.dino_id[i] = i + 1; o.dino_x[i] = 4 + 5 * i; o.dino_y[i] = 8'); "
            "print('go', flush=True); "
            "agent.get_next_move(types.SimpleNamespace(tick=1, score=0)); "
            "(end, length), = agent.phase.full_stalls; "
            "assert 40 <= length <= 90, length; "
            "assert agent.phase.stats['stalls'] == 1, agent.phase.stats"
        )
        env = os.environ.copy()
        env["BOT_PHASE_BIG_MS"] = "120"
        env.pop("BOT_PIN_CPU", None)
        child = subprocess.Popen(
            [sys.executable, "-c", code], cwd=ROOT / "bots" / "search_bot",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        )
        while (line := child.stdout.readline()) and line.strip() != "go":
            pass
        self.assertEqual(line.strip(), "go")
        time.sleep(0.03)
        os.kill(child.pid, signal.SIGSTOP)
        time.sleep(0.05)
        os.kill(child.pid, signal.SIGCONT)
        _, stderr = child.communicate(timeout=30)
        self.assertEqual(child.returncode, 0, stderr)

    @unittest.skipUnless(sys.platform.startswith("linux"), "CPU affinity is Linux-only")
    def test_search_bot_auto_cpu_pinning(self):
        code = "import os, bot; assert len(os.sched_getaffinity(0)) == 1"
        env = os.environ.copy()
        env["BOT_PIN_CPU"] = "auto"
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT / "bots" / "search_bot",
            capture_output=True, text=True, timeout=30, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PIN cpu=", result.stdout)

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
