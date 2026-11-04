"""Check a server game against a local replay of the same seed.

Reads the server's bot.log and game.log, replays that seed locally with the same
bot, then compares the per-tick STATE hashes up to the first stall test.

    python tools/compare_probe.py blitz-games/<gameId>
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from platform_paths import ROOT, launcher_path

LAUNCHER = launcher_path()
BOT = ROOT / "experiments" / "probe_bot"
STATE_RE = re.compile(r"STATE (\d+) (\d+) (\w+)")
REPLAY_RE = re.compile(r"--biome (\S+) --randomSeed (\S+)")
PARITY_END_TICK = 899


def states(text: str) -> dict[int, tuple[int, str]]:
    return {
        int(tick): (int(score), digest)
        for tick, score, digest in STATE_RE.findall(text)
        if int(tick) <= PARITY_END_TICK
    }


def replay_locally(biome: str, seed: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        record = Path(tmp) / "game.json"
        server = subprocess.Popen(
            [
                str(LAUNCHER),
                "--keepAlive", "false",
                "--delayBetweenTicksMs", "0",
                "--timePerTickMs", "150",
                "--maxTimeoutsBeforeKick", "50",
                "--recordPath", str(record),
                "--biome", biome,
                "--randomSeed", seed,
            ],
            cwd=LAUNCHER.parent,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1.0)
        bot = subprocess.run(
            [sys.executable, "application.py"],
            cwd=BOT,
            capture_output=True,
            text=True,
            encoding="utf8",
            errors="replace",
        )
        server.wait(timeout=600)
    shutil.rmtree(BOT / "__pycache__", ignore_errors=True)
    return bot.stdout


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("game_dir", type=Path)
    args = parser.parse_args()

    game_log = (args.game_dir / "game.log").read_text(encoding="utf8", errors="replace")
    bot_log = (args.game_dir / "bot.log").read_text(encoding="utf8", errors="replace")

    replay = REPLAY_RE.search(game_log)
    if replay is None:
        raise SystemExit("no Replay: line in game.log")
    biome, seed = replay.groups()
    print(f"biome={biome} seed={seed}")

    remote = states(bot_log)
    local = states(replay_locally(biome, seed))
    print(f"ticks compared: server {len(remote)}, local {len(local)}")

    for tick in sorted(remote.keys() & local.keys()):
        if remote[tick] != local[tick]:
            print(f"FIRST DIFFERENCE at tick {tick}: server {remote[tick]}, local {local[tick]}")
            raise SystemExit(1)
    last = max(remote.keys() & local.keys())
    print(f"IDENTICAL through tick {last}, score {remote[last][0]}")


if __name__ == "__main__":
    main()
