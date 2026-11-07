"""Replay the phase-aware budget against modelled server CPU freezes.

    python tools/simulate_phase_budget.py --games 200
    python tools/simulate_phase_budget.py --probe   # also replay probe game 24273's recorded stalls
    python tools/simulate_phase_budget.py --trace runs/phase-server/24316/bot.log

--trace replays the policy on the PHASE lines of a server bot log instead (BOT_PHASE_TRACE=1). The
budgets don't feed back into that game's timing, so it shows what the policy would decide, and
how many of its shortened ticks fell outside the bursts seen in the game.

The model follows docs/notes/freeze-analysis.md and docs/notes/phase-budget.md: bursts every
5,000 ms with a random phase per game, lasting 8 or 9 periods of a 100 ms wall-clock grid. In each
period the CPU is gone from 0-6 ms before the grid point to 49 ms after it.
Rare isolated 20-40 ms freezes and short 3-12 ms hiccups come on top. The server sends a tick,
starts its 150 ms clock and sends the next tick 2 ms after the reply (or the timeout). The bot's
search checks its wall-clock deadline only while it is on the CPU, so a freeze that straddles
the deadline delays the reply. Reported search time is on-CPU search per searched tick, the
quantity that scales the number of simulations.

This measures timing only; it says nothing about scores.
"""

import argparse
import bisect
import collections
import csv
import math
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bots" / "search_bot"))
from phase_budget import PhaseBudget  # noqa: E402

LIMIT_MS = 150.0
TICKS = 1000


class Freezes:
    """Sorted, non-overlapping intervals during which the bot is off the CPU."""

    def __init__(self, intervals):
        merged = []
        for s, e in sorted(intervals):
            if merged and s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        self.starts = [s for s, _ in merged]
        self.ends = [e for _, e in merged]

    def covering(self, t):
        i = bisect.bisect_right(self.starts, t) - 1
        if i >= 0 and t < self.ends[i]:
            return i
        return None

    def run(self, t, cpu_ms):
        """Wall time when cpu_ms of work started at t finishes."""
        i = self.covering(t)
        if i is not None:
            t = self.ends[i]
        while True:
            j = bisect.bisect_right(self.starts, t)
            if j >= len(self.starts) or self.starts[j] >= t + cpu_ms:
                return t + cpu_ms
            cpu_ms -= self.starts[j] - t
            t = self.ends[j]

    def off(self, t0, t1):
        total = 0.0
        j = max(bisect.bisect_right(self.starts, t0) - 1, 0)
        while j < len(self.starts) and self.starts[j] < t1:
            total += max(min(self.ends[j], t1) - max(self.starts[j], t0), 0.0)
            j += 1
        return total


def synthetic_freezes(rng, horizon_ms, cycle=5000.0, grid=100.0, bursts=True, isolated_per_s=0.02,
                      hiccups_per_s=1.0):
    """Bursts on a 5 s cycle; inside them the CPU goes from just before each 100 ms grid point to
    49 ms after it, as in probe game 24273. Both phases are random per game."""
    intervals = []
    if bursts:
        phase = rng.uniform(0, grid)
        t = phase + grid * math.floor(rng.uniform(0, cycle) / grid)
        while t < horizon_ms:
            for j in range(9 if rng.random() < 0.3 else 8):
                point = t + grid * j
                intervals.append((point - rng.uniform(0, 6), point + 49 + rng.uniform(0, 0.8)))
            t += cycle
    t = 0.0
    while isolated_per_s > 0:
        t += rng.expovariate(isolated_per_s) * 1000
        if t >= horizon_ms:
            break
        intervals.append((t, t + rng.uniform(20, 40)))
    t = 0.0
    while hiccups_per_s > 0:
        t += rng.expovariate(hiccups_per_s) * 1000
        if t >= horizon_ms:
            break
        intervals.append((t, t + rng.uniform(3, 12)))
    return Freezes(intervals)


def probe_freezes():
    """Recorded longest gap per cycle of probe game 24273 (only the longest gap is known)."""
    path = ROOT / "runs" / "freeze-analysis" / "probe-ticks.csv"
    intervals, origin = [], None
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            wall = float(row["bot_wall_epoch_ms"])
            origin = wall if origin is None else min(origin, wall)
            stall = float(row["max_stall_ms"] or 0)
            if stall >= 3:
                start = wall + float(row["stall_offset_ms"] or 0)
                intervals.append((start, start + stall))
    intervals = [(s - origin, e - origin) for s, e in intervals]
    return Freezes(intervals), max(e for _, e in intervals)


class Fixed:
    """The old behaviour: the same budget on every tick."""

    def __init__(self, budget_ms):
        self.budget_ms = budget_ms

    def observe_search(self, *args):
        pass

    def observe_wait(self, *args):
        pass

    def finish(self, *args):
        pass

    def budget(self, start, now):
        return self.budget_ms


class Oracle(Fixed):
    """Knows every future freeze: the most any predictor could reach at this cap."""

    def __init__(self, freezes, budget_ms, overhead_ms=4.0, latest_ms=142.0):
        super().__init__(budget_ms)
        self.freezes, self.overhead_ms, self.latest_ms = freezes, overhead_ms, latest_ms

    def budget(self, start, now):
        deadline = start + self.budget_ms
        f = self.freezes
        while deadline > now:
            j = bisect.bisect_right(f.starts, deadline + self.overhead_ms) - 1
            if j < 0 or f.ends[j] <= deadline or f.ends[j] + self.overhead_ms <= start + self.latest_ms:
                break
            deadline = f.starts[j] - self.overhead_ms - 0.5
        return max(deadline - start, 1.0)


def play(policy, freezes, rng, idle_share=0.35, horizon_ms=None, grace_ms=6.0, gap_ms=5.0):
    """One game; returns per-tick (response_ms, search_cpu_ms or None)."""
    results = []
    send = 1000.0  # Ticks 1-10 get extra time; start after them.
    replied_at = None
    for tick in range(11, TICKS + 1):
        if horizon_ms is not None and send > horizon_ms:
            break
        arrived = freezes.run(send + 0.5, 1.0)  # Receive and decode the message.
        lag = 0.0 if replied_at is None else max(arrived - replied_at - grace_ms, 0.0)
        start = arrived - lag
        if replied_at is not None:
            policy.observe_wait(replied_at, arrived, lag)
        filled = freezes.run(arrived, 0.5)
        budget = policy.budget(start, filled)
        gaps = []
        if rng.random() < idle_share:
            end = freezes.run(filled, 0.3)
            search = None
        else:
            deadline = start + budget
            end = max(filled, deadline)
            i = freezes.covering(end)
            if i is not None:
                end = freezes.ends[i]
            search = (end - filled) - freezes.off(filled, end)
            j = max(bisect.bisect_right(freezes.starts, filled) - 1, 0)
            while j < len(freezes.starts) and freezes.starts[j] < end:
                a, b = max(freezes.starts[j], filled), min(freezes.ends[j], end)
                if b - a >= gap_ms:
                    gaps.append((a, b))
                j += 1
        overrun = 0.0 if search is None else end - (start + budget)
        policy.observe_search(arrived, end, gaps, freezes.off(arrived, end), overrun)
        policy.finish(start, end, budget)
        replied_at = end
        received = freezes.run(end, 1.0) + 0.5
        response = received - send
        results.append((response, search))
        send = min(received, send + LIMIT_MS) + 2.0
    return results


def summarise(name, games):
    timeouts = [sum(r > LIMIT_MS for r, _ in g) for g in games]
    over130 = [sum(r > 130 for r, _ in g) for g in games]
    over140 = [sum(140 < r <= LIMIT_MS for r, _ in g) for g in games]
    search = [statistics.fmean(s for _, s in g if s is not None) for g in games]
    worst = max(timeouts)
    print(
        f"{name:22s} search/tick {statistics.fmean(search):6.1f} ms  timeouts/game "
        f"{statistics.fmean(timeouts):5.2f} (max {worst})  >130 {statistics.fmean(over130):5.2f}  "
        f"140-150 {statistics.fmean(over140):5.2f}"
    )
    return statistics.fmean(search)


def load_trace(path):
    rows = []
    for line in Path(path).read_text(encoding="utf8", errors="replace").splitlines():
        i = line.find("PHASE tick=")
        if i < 0:
            continue
        fields = dict(item.split("=", 1) for item in line[i:].split()[1:])
        gaps = [tuple(map(float, gap.split("-"))) for gap in fields.pop("gaps").split(";") if gap]
        row = {key: float(value) for key, value in fields.items()}
        row["gaps"] = gaps
        rows.append(row)
    return rows


def replay_trace(rows, policy, warmup_ms=6000.0):
    """Mean budget, and shortened ticks outside and inside the game's bursts."""
    origin = rows[0]["start"]
    seen = collections.Counter(
        int(((a - origin) % 5000) // 100) for row in rows for a, b in row["gaps"] if b - a >= 30
    )
    burst = {k for k, count in seen.items() if count >= 2}

    def in_burst(t):
        return any(int(((t - origin + shift) % 5000) // 100) in burst for shift in (0, 100))

    budgets, outside, inside, replied_at = [], 0, 0, None
    for row in rows:
        if replied_at is not None:
            policy.observe_wait(replied_at, row["arrived"], row["arrived"] - row["start"])
        budget = policy.budget(row["start"], row["arrived"] + 0.5)
        budgets.append(budget)
        if budget < policy.big_ms - 1 and row["start"] - origin > warmup_ms:
            if in_burst(row["start"] + budget):
                inside += 1
            else:
                outside += 1
        overrun = row["end"] - (row["start"] + budget)
        policy.observe_search(row["arrived"], row["end"], row["gaps"], row["off"], overrun)
        replied_at = row["end"]
    return statistics.fmean(budgets), outside, inside


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=100)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--big", type=float, nargs="+", default=[110, 120, 125, 130, 135])
    parser.add_argument("--cut", type=float, default=85)
    parser.add_argument("--no-bursts", action="store_true", help="host without the 5 s bursts")
    parser.add_argument("--isolated", type=float, default=0.02, help="isolated 20-40 ms freezes per second")
    parser.add_argument("--probe", action="store_true", help="also replay probe game 24273")
    parser.add_argument("--trace", nargs="+", help="replay on server bot logs with PHASE lines instead")
    args = parser.parse_args()

    if args.trace:
        for path in args.trace:
            for big in args.big:
                mean, outside, inside = replay_trace(load_trace(path), PhaseBudget(big_ms=big, cut_ms=args.cut))
                print(f"{path}  big {big:g}: mean budget {mean:.1f} ms, shortened outside bursts {outside}, inside {inside}")
        return

    variants = [
        ("fixed 95", lambda f: Fixed(95)),
        ("fixed 85", lambda f: Fixed(85)),
        ("phase base 95", lambda f: PhaseBudget(cut_ms=args.cut)),
    ]
    for big in args.big:
        variants.append((f"phase {big:g}", lambda f, b=big: PhaseBudget(big_ms=b, cut_ms=args.cut)))
        variants.append((f"oracle {big:g}", lambda f, b=big: Oracle(f, b)))

    for name, make in variants:
        games = []
        for g in range(args.games):
            rng = random.Random(args.seed * 100003 + g)
            freezes = synthetic_freezes(rng, 200_000, bursts=not args.no_bursts, isolated_per_s=args.isolated)
            games.append(play(make(freezes), freezes, random.Random(g)))
        summarise(name, games)

    if args.probe:
        freezes, horizon = probe_freezes()
        print(f"\nprobe game 24273 replay, first {horizon / 1000:.0f} s")
        for name, make in variants:
            games = [play(make(freezes), freezes, random.Random(g), horizon_ms=horizon) for g in range(20)]
            summarise(name, games)


if __name__ == "__main__":
    main()
