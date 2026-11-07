"""Unit tests for bots/search_bot/phase_budget.py, on synthetic clocks."""

import sys
import unittest

from platform_paths import ROOT

sys.path.insert(0, str(ROOT / "bots" / "search_bot"))
from phase_budget import PhaseBudget  # noqa: E402

GRID_END = 49.0  # Grid freezes run from about grid point - 3 ms to grid point + 49 ms.


def learned(**kwargs) -> PhaseBudget:
    """A policy that has seen two cycles: a burst at 0-800 ms of each 5 s cycle, clean elsewhere."""
    policy = PhaseBudget(big_ms=120, **kwargs)
    for cycle in range(2):
        base = cycle * 5000.0
        for k in range(8):
            point = base + 100.0 * k
            policy.observe_search(point - 3, point + 95, [(point - 3, point + GRID_END)], 52.0)
        for t in range(1000, 5000, 100):
            policy.observe_search(base + t, base + t + 95, [], 0.0)
    return policy


class PhaseBudgetTest(unittest.TestCase):
    def test_learns_the_grid_from_recorded_freezes(self):
        policy = learned()
        self.assertTrue(policy.grid_known())
        self.assertAlmostEqual(policy.grid_phase, GRID_END, delta=0.5)
        self.assertAlmostEqual(policy.stall_length, 52.0, delta=0.5)

    def test_big_budget_outside_bursts(self):
        policy = learned()
        now = 10000.0 + 2500.0
        self.assertEqual(policy.budget(now, now + 1), 120)

    def test_deadline_moves_before_a_freeze_that_ends_too_late(self):
        policy = learned()
        # A 120 ms deadline from grid point 300 lands in the freeze at 400, which ends at 449: past the
        # reply limit of 300 + 142. The deadline moves to before that freeze starts.
        start = 10000.0 + 300.0
        budget = policy.budget(start, start + 1)
        self.assertLess(start + budget, 10000.0 + 400.0 - 3)
        self.assertGreater(budget, 30)
        self.assertEqual(policy.stats["moved"], 1)

    def test_deadline_kept_when_the_freeze_ends_in_time(self):
        policy = learned()
        # The deadline lands in the freeze at 300, which ends at 349: before the limit of 220 + 142.
        start = 10000.0 + 220.0
        self.assertEqual(policy.budget(start, start + 1), 120)

    def test_base_budget_until_calibrated(self):
        policy = PhaseBudget(big_ms=120)
        policy.observe_search(0.0, 95.0, [], 0.0)
        self.assertEqual(policy.budget(1000.0, 1001.0), 95)

    def test_shared_cpu_without_long_pauses_is_not_a_freeze(self):
        policy = learned()
        # 40 ms off the CPU in slices too short to be recorded, and the decision met its deadline.
        stalls = policy.stats["stalls"]
        policy.observe_search(12500.0, 12620.0, [], 40.0, overrun_ms=0.5)
        self.assertEqual(policy.stats["stalls"], stalls)
        self.assertFalse(policy.burst_possible(12700.0, 12720.0, 12630.0))
        # The same loss counts when it made the decision overrun its deadline.
        policy.observe_search(12500.0, 12620.0, [], 40.0, overrun_ms=30.0)
        self.assertEqual(policy.stats["stalls"], stalls + 1)
        self.assertTrue(policy.burst_possible(12700.0, 12720.0, 12630.0))

    def test_repeated_slow_replies_turn_the_extension_off(self):
        policy = learned()
        for i in range(policy.max_overruns):
            policy.finish(i * 200.0, i * 200.0 + 145, 120)
        self.assertFalse(policy.extend)
        now = 12500.0
        self.assertEqual(policy.budget(now, now + 1), 95)


if __name__ == "__main__":
    unittest.main()
