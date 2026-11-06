"""Archive every game returned by the authenticated launcher's games command."""

import argparse
import csv
import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from platform_paths import ROOT, launcher_path


def collect(launcher: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [str(launcher), "games"], cwd=launcher.parent, capture_output=True, text=True, check=True
    )
    (output / "server-games.tsv").write_text(result.stdout, encoding="utf8")
    games = list(csv.DictReader(result.stdout.splitlines(), delimiter="\t"))
    if not games or "gameId" not in games[0]:
        raise ValueError("launcher returned no parseable game inventory")
    failures = []
    manifest = []
    for game in games:
        game_id = game["gameId"]
        if not game_id.isdecimal():
            raise ValueError(f"invalid game id: {game_id!r}")
        folder = output / "raw" / game_id
        files = [folder / name for name in ("bot.log", "game.log", "replay.jsonl")]
        if not all(path.exists() for path in files):
            for attempt in range(4):
                result = subprocess.run(
                    [str(launcher), "download", game_id, "--out", str(folder.resolve())],
                    cwd=launcher.parent, capture_output=True, text=True,
                )
                if result.returncode != 3:
                    break
                print(f"{game_id}: rate limited, waiting 65 seconds", flush=True)
                time.sleep(65)
            if result.returncode != 0:
                failures.append({"game_id": game_id, "error": result.stderr or result.stdout})
                print(f"{game_id}: download failed: {failures[-1]['error']}", flush=True)
                continue
        if not all(path.exists() for path in files):
            failures.append({"game_id": game_id, "error": "download omitted expected files"})
            continue
        for path in files:
            with path.open("rb") as file:
                digest = hashlib.file_digest(file, "sha256").hexdigest()
            manifest.append({
                "game_id": game_id, "file": str(path.relative_to(output)),
                "bytes": path.stat().st_size, "sha256": digest,
            })
        print(f"{game_id}: archived", flush=True)
    with (output / "manifest.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["game_id", "file", "bytes", "sha256"])
        writer.writeheader()
        writer.writerows(manifest)
    (output / "collection-errors.json").write_text(json.dumps(failures, indent=2) + "\n")
    (output / "collection.json").write_text(json.dumps({
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "inventory_command": "blitz-challenge-linux games",
        "listed_games": len(games), "archived_games": len(manifest) // 3,
        "failed_games": len(failures),
    }, indent=2) + "\n")
    print(f"{len(games)} listed; {len(manifest) // 3} archived; {len(failures)} failures")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launcher", type=Path, default=launcher_path())
    parser.add_argument("--output", type=Path, default=ROOT / "runs/freeze-analysis")
    args = parser.parse_args()
    collect(args.launcher.resolve(), args.output.resolve())
