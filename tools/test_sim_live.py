"""Focused CLI checks; build the simulator with tools/build_sim.py first."""

import csv
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from build_sim import BUILD


class LiveResultsTest(unittest.TestCase):
    def run_game(self, *options: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                str(BUILD / "sim_live.exe"), "1", "--mcts", "1",
                "--budget", "1", "--threads", "1", "--first", "123", *options,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_csv_matches_reported_games(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "results.csv"
            result = self.run_game("--csv", str(path))
            self.assertEqual(result.returncode, 0, result.stderr)
            with path.open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(
                [row["biome"] for row in rows],
                ["deep_crevasse", "mountain_range", "volcanic_plains"],
            )
            for row in rows:
                self.assertEqual(row["seed"], "live-123")
                report = re.search(
                    rf"^{row['biome']}\s+mean\s+(\d+).*kills\s+(\d+)\s+spawned\s+(\d+)$",
                    result.stdout, re.MULTILINE,
                )
                self.assertIsNotNone(report)
                self.assertEqual(
                    report.groups(), (row["score"], row["kills"], row["spawned"]),
                )

    def test_csv_excludes_filtered_biomes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "results.csv"
            result = self.run_game("--csv", str(path), "--biome", "mountain_range")
            self.assertEqual(result.returncode, 0, result.stderr)
            with path.open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["biome"], "mountain_range")
            self.assertEqual(rows[0]["seed"], "live-123")

    def test_unwritable_csv_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "missing" / "results.csv"
            result = self.run_game("--csv", str(path))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("could not open", result.stderr)

    def test_invalid_optimism_is_rejected(self):
        for weight in ("-0.1", "1.1", "nan", "inf"):
            with self.subTest(weight=weight):
                result = self.run_game("--optimism", weight)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--optimism must be between 0 and 1", result.stderr)


if __name__ == "__main__":
    unittest.main()
