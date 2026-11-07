"""Play a local game while emulating the server's CPU bursts on the bot.

Every 5,000 ms the bot is stopped (SIGSTOP) for 50 ms on each point of a 100 ms grid, 8 or 9
times in a row, like the bursts seen on the server (docs/notes/phase-budget.md). Linux only.

    BOT_PHASE_BIG_MS=120 python tools/emulate_freezes.py --seed abc
"""

import argparse
import json
import os
import random
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from platform_paths import ROOT, launcher_path

LAUNCHER = launcher_path()


def stopper(pid: int, rng: random.Random, done: threading.Event, freeze_ms: float, burst_points: tuple) -> None:
    grid = time.perf_counter() * 1000 + rng.uniform(0, 100)
    cycle = grid + rng.uniform(0, 5000)
    while not done.is_set():
        points = rng.choice(burst_points)
        for k in range(points):
            point = cycle + 100 * k
            delay = point - time.perf_counter() * 1000
            if delay > 0 and done.wait(delay / 1000):
                return
            try:
                os.kill(pid, signal.SIGSTOP)
                time.sleep(freeze_ms / 1000)
                os.kill(pid, signal.SIGCONT)
            except ProcessLookupError:
                return
        cycle += 5000


def play(bot_dir: Path, biome: str, seed: str, rng: random.Random, freeze_ms: float) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        record = Path(tmp) / "game.json"
        log_path = Path(tmp) / "launcher.log"
        cmd = [
            str(LAUNCHER), "--keepAlive", "false", "--delayBetweenTicksMs", "0", "--timePerTickMs", "150",
            "--maxTimeoutsBeforeKick", "50", "--recordPath", str(record), "--biome", biome, "--randomSeed", seed,
        ]
        with log_path.open("wb") as log:
            server = subprocess.Popen(cmd, cwd=LAUNCHER.parent, stdout=log, stderr=log)
            time.sleep(1.0)
            bot = subprocess.Popen(
                [sys.executable, "application.py"], cwd=bot_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf8", errors="replace",
            )
            done = threading.Event()
            thread = threading.Thread(target=stopper, args=(bot.pid, rng, done, freeze_ms, (8, 9)), daemon=True)
            thread.start()
            stdout, stderr = bot.communicate()
            done.set()
            thread.join()
            server.wait(timeout=600)
        text = log_path.read_text(encoding="utf8", errors="replace")
        results = json.loads(record.read_text(encoding="utf8"))["gameResults"]
        result = results[0] if isinstance(results, list) else results
        budgets = [line for line in stdout.splitlines() if line.startswith("BUDGET")]
        return {
            "seed": seed,
            "score": result.get("score"),
            "late": text.count("No command was received in time"),
            "botErrors": stderr.count("Errors during last tick"),
            "last": budgets[-1] if budgets else None,
            "stdout": stdout,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot", default="bots/search_bot")
    parser.add_argument("--biome", default="volcanic_plains")
    parser.add_argument("--seed", default="emulate")
    parser.add_argument("--rng", type=int, default=1, help="seed for the burst and grid phases")
    parser.add_argument("--freeze-ms", type=float, default=50.0)
    parser.add_argument("--log", help="write the bot's stdout here")
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        raise SystemExit("needs Linux (SIGSTOP)")
    result = play(ROOT / args.bot, args.biome, args.seed, random.Random(args.rng), args.freeze_ms)
    if args.log:
        Path(args.log).write_text(result["stdout"], encoding="utf8")
    del result["stdout"]
    print(result)


if __name__ == "__main__":
    main()
