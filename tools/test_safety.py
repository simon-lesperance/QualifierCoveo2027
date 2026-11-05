"""Compile and check the current internal safety distance on either host."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from build_sim import FLAGS, SIM, zig_python
from platform_paths import executable_suffix


class SafetyRegressionTest(unittest.TestCase):
    def test_distances_match_reference(self):
        with tempfile.TemporaryDirectory(prefix="safety regression ") as folder:
            executable = Path(folder) / f"safety_test{executable_suffix()}"
            command = [
                zig_python(), "-m", "ziglang", "c++", *FLAGS,
                "-o", str(executable),
                str(SIM / "safety_test.cpp"), str(SIM / "rng.cpp"), str(SIM / "terrain.cpp"),
            ]
            built = subprocess.run(command, capture_output=True, text=True, timeout=180)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("queries checked", result.stdout)
            print(result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
