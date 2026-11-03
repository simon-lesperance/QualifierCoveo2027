"""Play a search plan on the real engine and compare it with the simulator, tick by tick.

    python tools/verify_plan.py runs/plans/mountain_range-est-0-w200.txt mountain_range est-0
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_sim

REPLAY_BOT = check_sim.ROOT / "experiments" / "replay_bot" / "bot.py"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    parser.add_argument("biome")
    parser.add_argument("seed")
    args = parser.parse_args()

    plan = args.plan.resolve()
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        bot_dir = folder / "bot"
        bot_dir.mkdir()
        check_sim.prepare_bot(bot_dir)
        shutil.copy(REPLAY_BOT, bot_dir / "bot.py")
        record = folder / "game.json"

        check_sim.play(bot_dir, args.biome, args.seed, record, plan, {"REPLAY_ACTIONS": str(plan)})

        lines = subprocess.run(
            [str(check_sim.SIM_REPLAY), args.biome, args.seed, str(plan)],
            check=True, capture_output=True, text=True,
        ).stdout.splitlines()
        engine_score = json.loads(record.read_text(encoding="utf8"))["commandsAndTicks"][-1]["score"]
        difference = check_sim.first_difference(record, lines)

    print(f"{args.biome} {args.seed}: engine score {engine_score}, sim score {json.loads(lines[-1])['score']}")
    print("IDENTICAL on every tick" if difference is None else f"DIFF {difference}")
    sys.exit(0 if difference is None else 1)


if __name__ == "__main__":
    main()
