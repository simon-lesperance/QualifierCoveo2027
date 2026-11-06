"""Phase-aware search budget.

On the server, other work takes the CPU in bursts that repeat every 5,000 ms of wall time and
last 750-850 ms. Inside a burst the CPU is gone on a fixed 100 ms wall-clock grid: from about
3 ms before a grid point to 49 ms after it, then it comes back. Both phases differ between games
but hold for a whole game (docs/notes/freeze-analysis.md, docs/notes/phase-budget.md).

A freeze only makes a reply late when the reply has to wait for it: when the search deadline
falls inside a freeze that ends too close to the server's 150 ms limit. So the bot

- learns the 5 s wheel: which phases of the cycle had freezes, from every searched interval;
- learns the 100 ms grid from the exact freezes the search loop records;
- gives each tick the large budget, unless a burst is possible near the deadline and the
  predicted freeze there would end too late. Then the deadline moves to just before that freeze.

Until the wheel has seen a couple of full cycles, the deadline is capped at the base budget
(still moved before predicted freezes). Repeated slow replies on extended budgets switch the
extension off for the rest of the game.

All times are milliseconds on one monotonic clock. Nothing here reads the clock, so the policy
can be replayed offline (tools/simulate_phase_budget.py).
"""

import cmath
import math
from collections import deque


class PhaseBudget:
    def __init__(
        self,
        base_ms: float = 95.0,
        big_ms: float = 95.0,
        cut_ms: float = 85.0,
        limit_ms: float = 150.0,
        safety_ms: float = 8.0,
        overhead_ms: float = 4.0,
        cycle_ms: float = 5000.0,
        bin_ms: float = 20.0,
        grid_ms: float = 100.0,
        stall_ms: float = 30.0,
        full_stall_ms: float = 30.0,
        clean_min_ms: float = 60.0,
        hot_rate: float = 0.25,
        min_hot: int = 1,
        warmup_cycles: float = 1.2,
        recent_ms: float = 150.0,
        margin_ms: float = 4.0,
        min_grid_samples: int = 3,
        min_grid_r: float = 0.9,
        overrun_ms: float = 140.0,
        max_overruns: int = 3,
    ):
        if not 0 < cut_ms <= base_ms:
            raise ValueError("cut_ms must be in (0, base_ms]")
        self.base_ms, self.big_ms, self.cut_ms = base_ms, big_ms, cut_ms
        # A reply has to leave by start + latest_ms.
        self.latest_ms = limit_ms - safety_ms
        self.overhead_ms = overhead_ms
        self.cycle_ms, self.bin_ms, self.grid_ms = cycle_ms, bin_ms, grid_ms
        self.bins = max(int(round(cycle_ms / bin_ms)), 1)
        self.stall_ms, self.full_stall_ms = stall_ms, full_stall_ms
        self.clean_min_ms, self.hot_rate, self.min_hot = clean_min_ms, hot_rate, min_hot
        self.warmup_ms = warmup_cycles * cycle_ms
        self.recent_ms, self.margin_ms = recent_ms, margin_ms
        self.min_grid_samples, self.min_grid_r = min_grid_samples, min_grid_r
        self.overrun_ms, self.max_overruns = overrun_ms, max_overruns

        self.stalled = [0] * self.bins
        self.seen = [0] * self.bins
        self.origin = None
        self.last_stall_end = -math.inf
        self.full_stalls = deque(maxlen=32)  # (end, length) of freezes seen whole by the search.
        self.grid_phase = None  # Where freezes end, modulo grid_ms.
        self.grid_r = 0.0
        self.stall_length = 55.0
        self.overruns = 0  # Slow decisions on ticks that got more than the base budget.
        self.extend = big_ms > base_ms
        self.stats = {"big": 0, "moved": 0, "base": 0, "cut": 0, "stalls": 0, "slow": 0}

    # Learning.

    def _mark(self, t0: float, t1: float, stalled: bool) -> None:
        first = math.floor((t0 - self.origin) / self.bin_ms)
        last = min(math.floor((t1 - self.origin) / self.bin_ms), first + self.bins - 1)
        for k in range(first, last + 1):
            i = k % self.bins
            self.seen[i] += 1
            if stalled:
                self.stalled[i] += 1

    def _stall(self, t0: float, t1: float) -> None:
        # A burst covers the free part of the grid around each freeze too.
        pad = self.grid_ms - self.stall_length
        self._mark(t0 - pad, t1 + pad, True)
        self.last_stall_end = max(self.last_stall_end, t1)
        self.stats["stalls"] += 1

    def observe_search(self, t0: float, t1: float, gaps, off_ms: float, overrun_ms: float = 0.0) -> None:
        """A decision ran from t0 to t1, off the CPU for off_ms, with exact pauses in gaps.

        overrun_ms is how far past its deadline the decision ended. Off-CPU time the search loop
        didn't see as one pause only counts as a freeze when it made the decision overrun: on a
        shared CPU the bot loses time in slices too short to threaten the deadline.
        """
        if self.origin is None:
            self.origin = t0
        exact = 0.0
        for start, end in gaps:
            length = end - start
            exact += length
            if length >= self.stall_ms:
                self._stall(start, end)
            if length >= self.full_stall_ms:
                self.full_stalls.append((end, length))
                self._fit_grid()
        if off_ms - exact >= self.stall_ms and overrun_ms >= self.stall_ms:
            self._stall(t0, t1)  # Lost outside the search loop: only the interval is known.
        elif off_ms < self.stall_ms and t1 - t0 >= self.clean_min_ms:
            self._mark(t0, t1, False)  # A long clean stretch can't be inside a burst.

    def observe_wait(self, replied_at: float, arrived: float, lag_ms: float) -> None:
        """The tick after our reply arrived lag_ms later than usual."""
        if self.origin is None:
            self.origin = replied_at
        if lag_ms >= self.stall_ms:
            self._stall(replied_at, arrived)

    def _fit_grid(self) -> None:
        if len(self.full_stalls) < self.min_grid_samples:
            return
        turn = 2 * math.pi / self.grid_ms
        mean = sum(cmath.exp(1j * turn * end) for end, _ in self.full_stalls) / len(self.full_stalls)
        self.grid_r = abs(mean)
        self.grid_phase = (cmath.phase(mean) / turn) % self.grid_ms
        lengths = sorted(length for _, length in self.full_stalls)
        self.stall_length = min(max(lengths[-2] if len(lengths) > 1 else lengths[0], 40.0), 70.0)

    # Prediction.

    def calibrated(self, now: float) -> bool:
        return self.origin is not None and now - self.origin >= self.warmup_ms

    def grid_known(self) -> bool:
        return self.grid_phase is not None and self.grid_r >= self.min_grid_r

    def hot(self, i: int) -> bool:
        stalled = self.stalled[i]
        return stalled >= self.min_hot and stalled >= self.hot_rate * self.seen[i]

    def burst_possible(self, t0: float, t1: float, now: float) -> bool:
        if now - self.last_stall_end < self.recent_ms:
            return True
        if not self.calibrated(now):
            return False
        first = math.floor((t0 - self.origin) / self.bin_ms)
        last = math.floor((t1 - self.origin) / self.bin_ms)
        return any(self.hot(k % self.bins) for k in range(first, last + 1))

    def freeze_around(self, t: float):
        """The predicted grid freeze whose padded span contains t, as (start, end), or None."""
        end = self.grid_phase + self.grid_ms * math.ceil((t - self.grid_phase - self.margin_ms) / self.grid_ms)
        start = end - self.stall_length - self.margin_ms - self.overhead_ms
        if start <= t:
            return start, end + self.margin_ms
        return None

    def budget(self, start: float, now: float) -> float:
        """Search budget in ms, measured from start, the server's probable send time."""
        cap = self.big_ms if self.extend and self.calibrated(now) else self.base_ms
        deadline = start + cap
        latest = start + self.latest_ms
        if not self.burst_possible(deadline - self.stall_length - self.margin_ms, deadline + self.overhead_ms, now):
            self.stats["big" if cap > self.base_ms else "base"] += 1
            return cap
        if not self.grid_known():
            self.stats["cut"] += 1
            return min(cap, self.cut_ms)
        freeze = self.freeze_around(deadline)
        if freeze is None or freeze[1] + self.overhead_ms <= latest:
            self.stats["big" if cap > self.base_ms else "base"] += 1
            return cap
        # Reply before the freeze rather than waiting it out past the limit.
        moved = max(freeze[0] - start, 1.0)
        self.stats["moved"] += 1
        return moved

    def finish(self, start: float, end: float, budget_ms: float) -> None:
        """Record a finished decision; repeated slow ones on extended budgets stop the extension."""
        if end - start < self.overrun_ms:
            return
        self.stats["slow"] += 1
        if budget_ms > self.base_ms:
            self.overruns += 1
            if self.overruns >= self.max_overruns:
                self.extend = False

    # Reporting.

    def hot_share(self) -> float:
        return sum(self.hot(i) for i in range(self.bins)) / self.bins

    def summary(self) -> str:
        s = self.stats
        grid = f"{self.grid_phase:.1f}/{self.grid_r:.2f}/{self.stall_length:.0f}" if self.grid_phase is not None else "-"
        return (
            f"phase={'on' if self.extend else 'off'} big={s['big']} moved={s['moved']} base={s['base']} "
            f"cut={s['cut']} stalls={s['stalls']} slow={s['slow']} overruns={self.overruns} "
            f"hot={self.hot_share():.2f} grid={grid}"
        )

    def reset_stats(self) -> None:
        for key in self.stats:
            self.stats[key] = 0
