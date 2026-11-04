"""Check the C++ simulator against the real engine, tick by tick.

Starts with deterministic boundary/cap actions, follows with seeded random
actions, replays them in the simulator, and compares the full state after every
tick. Stops at the first difference and prints it.

    python tools/check_sim.py --games 12
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "localRunner" / (
    "blitz-challenge-win.exe" if sys.platform == "win32" else "blitz-challenge-linux"
)
SIM_REPLAY = ROOT / "build" / "sim_replay.exe"
BOT = ROOT / "experiments" / "chaos_bot"
STARTER = ROOT / "starterkit" / "python"
BIOMES = ["deep_crevasse", "mountain_range", "volcanic_plains"]

IMPASSABLE, MOUNTAIN, LAVA = 1, 2, 4


def prepare_bot(folder: Path) -> None:
    for name in ("application.py", "game_message.py"):
        shutil.copy(STARTER / name, folder / name)
    shutil.copy(BOT / "bot.py", folder / "bot.py")


def play(bot_dir: Path, biome: str, seed: str, record: Path, actions: Path, extra_env: dict | None = None) -> None:
    server = subprocess.Popen(
        [
            str(LAUNCHER),
            "--keepAlive", "false",
            "--delayBetweenTicksMs", "0",
            "--timePerTickMs", "0",
            "--recordPath", str(record),
            "--biome", biome,
            "--randomSeed", seed,
        ],
        cwd=LAUNCHER.parent,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.0)
    env = {**os.environ, "ACTIONS_LOG": str(actions), "CHAOS_SEED": seed, **(extra_env or {})}
    subprocess.run(
        [sys.executable, "application.py"],
        cwd=bot_dir,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=180,
    )
    server.wait(timeout=60)


def engine_state(state: dict) -> dict:
    tiles = [tile for row in state["map"]["tiles"] for tile in row]
    return {
        "score": state["score"],
        "originY": state["map"]["origin"]["y"],
        "kills": state["totalKills"],
        "dinos": sorted(
            [d["id"], d["name"], d["position"]["x"], d["position"]["y"], d["age"]] for d in state["dinosaurs"]
        ),
        "meteors": sorted([m["target"]["x"], m["target"]["y"], m["turnsUntilImpact"]] for m in state["meteors"]),
        "volcanoes": sorted([v["position"]["x"], v["position"]["y"]] for v in state["volcanoes"]),
        "corpses": sorted([c["x"], c["y"]] for c in state["corpses"]),
        "elevation": [tile["elevation"] for tile in tiles],
        "flags": [
            (IMPASSABLE if tile["isImpassable"] else 0)
            | (MOUNTAIN if tile["isMountain"] else 0)
            | (LAVA if tile["hasLava"] else 0)
            for tile in tiles
        ],
    }


def sim_state(state: dict) -> dict:
    return {
        "score": state["score"],
        "originY": state["originY"],
        "kills": state["kills"],
        "dinos": sorted(state["dinos"]),
        "meteors": sorted(state["meteors"]),
        "volcanoes": sorted(state["volcanoes"]),
        "corpses": sorted(state["corpses"]),
        "elevation": state["elevation"],
        "flags": state["flags"],
    }


def first_difference(record: Path, replay_lines: list[str]) -> str | None:
    states = json.loads(record.read_text(encoding="utf8"))["commandsAndTicks"]
    if len(replay_lines) != 1001:
        return f"simulator replayed {len(replay_lines)} states, expected 1001"
    if len(states) != len(replay_lines) + 1 or states[-1].get("currentTick") != 1001:
        return f"engine recorded {len(states)} states, expected 1001 ticks plus its terminal state"
    # Entry 0 is the setup state, and entry t follows update(t).
    for tick in range(0, 1001):
        expected = engine_state(states[tick])
        actual = sim_state(json.loads(replay_lines[tick]))
        for key in expected:
            if expected[key] != actual[key]:
                if key in ("elevation", "flags"):
                    cells = [i for i in range(400) if expected[key][i] != actual[key][i]]
                    detail = f"{len(cells)} cells differ, first at row {cells[0] // 20} col {cells[0] % 20}: " \
                             f"engine {expected[key][cells[0]]}, sim {actual[key][cells[0]]}"
                elif key == "dinos":
                    only_engine = [d for d in expected[key] if d not in actual[key]]
                    only_sim = [d for d in actual[key] if d not in expected[key]]
                    detail = f"engine only {only_engine}, sim only {only_sim}"
                else:
                    detail = f"engine {expected[key]}, sim {actual[key]}"
                return f"tick {tick}, {key}: {detail}"
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=12)
    parser.add_argument("--first", type=int, default=0)
    args = parser.parse_args()

    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        bot_dir = folder / "bot"
        bot_dir.mkdir()
        prepare_bot(bot_dir)
        for index in range(args.first, args.first + args.games):
            biome = BIOMES[index % len(BIOMES)]
            seed = f"check-{index}"
            record = folder / "game.json"
            actions = folder / "actions.txt"
            play(bot_dir, biome, seed, record, actions)
            lines = subprocess.run(
                [str(SIM_REPLAY), biome, seed, str(actions)], check=True, capture_output=True, text=True
            ).stdout.splitlines()
            difference = first_difference(record, lines)
            final = json.loads(lines[-1])
            status = "OK" if difference is None else f"DIFF {difference}"
            print(f"{biome:16} {seed:10} score {final['score']:6} kills {final['kills']:4}  {status}", flush=True)
            failures += difference is not None
            if difference is not None and failures >= 3:
                break
    print("all games identical" if failures == 0 else f"{failures} game(s) differ")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
