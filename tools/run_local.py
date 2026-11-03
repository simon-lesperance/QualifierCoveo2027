"""Play local games and print the final scores.

Starts the launcher, runs a bot against it, and reads the result from the
launcher's recorded game. Uses the server's tick budget by default.

    python tools/run_local.py --games 3
    python tools/run_local.py --bot starterkit/python --biome mountain_range --seed abc
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "localRunner" / "blitz-challenge-win.exe"
BIOMES = ["deep_crevasse", "mountain_range", "volcanic_plains"]
REPLAY_RE = re.compile(r"--biome (\S+) --randomSeed (\S+)")


def play(bot_dir: Path, biome: str | None, seed: str | None, tick_ms: int) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        record = Path(tmp) / "game.json"
        log_path = Path(tmp) / "launcher.log"
        cmd = [
            str(LAUNCHER),
            "--keepAlive", "false",
            "--delayBetweenTicksMs", "0",
            "--timePerTickMs", str(tick_ms),
            "--maxTimeoutsBeforeKick", "50",
            "--recordPath", str(record),
        ]
        if biome:
            cmd += ["--biome", biome]
        if seed:
            cmd += ["--randomSeed", seed]

        with log_path.open("wb") as log:
            server = subprocess.Popen(cmd, cwd=LAUNCHER.parent, stdout=log, stderr=log)
            time.sleep(1.0)
            bot = subprocess.run(
                [sys.executable, "application.py"],
                cwd=bot_dir,
                capture_output=True,
                text=True,
                encoding="utf8",
                errors="replace",
            )
            server.wait(timeout=600)

        text = log_path.read_text(encoding="utf8", errors="replace")
        replay = REPLAY_RE.search(text)
        results = json.loads(record.read_text(encoding="utf8"))["gameResults"]
        result = results[0] if isinstance(results, list) else results
        return {
            "biome": replay.group(1) if replay else biome,
            "seed": replay.group(2) if replay else seed,
            "score": result.get("score"),
            "timedOut": result.get("didTimeout"),
            "late": text.count("No command was received in time"),
            "botErrors": bot.stderr.count("Errors during last tick"),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot", default="starterkit/python")
    parser.add_argument("--games", type=int, default=1)
    parser.add_argument("--biome", choices=BIOMES)
    parser.add_argument("--seed")
    parser.add_argument("--tick-ms", type=int, default=150)
    args = parser.parse_args()

    bot_dir = (ROOT / args.bot).resolve()
    scores = []
    for _ in range(args.games):
        row = play(bot_dir, args.biome, args.seed, args.tick_ms)
        print(row, flush=True)
        scores.append(row["score"] or 0)
    if len(scores) > 1:
        print(f"mean {sum(scores) / len(scores):.0f}  min {min(scores)}  max {max(scores)}")


if __name__ == "__main__":
    main()
