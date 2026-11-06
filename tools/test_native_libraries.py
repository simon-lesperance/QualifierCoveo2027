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
