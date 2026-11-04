"""Package a bot folder for upload, with its files at the root of the zip.

    python tools/make_zip.py experiments/probe_bot

Writes <folder>.zip next to the folder, so the next zip doesn't contain the last
one. The archive is created and integrity-checked with Python's zipfile module.
"""

import argparse
import zipfile
from pathlib import Path

MARKER = "application.py"
MAX_BYTES = 10 * 1024 * 1024
SKIPPED_DIRS = {"__pycache__", ".venv", "venv", "blitz-games"}
SKIPPED_FILES = {"AGENTS.md", "game.json", "game_logs.txt", "error.log", "launcher.log"}
SKIPPED_SUFFIXES = {".pyc", ".dll", ".pdb", ".lib", ".exp"}  # The server runs Linux.


def files_to_pack(folder: Path) -> list[Path]:
    paths = []
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder)
        if not path.is_file() or SKIPPED_DIRS.intersection(relative.parts):
            continue
        if path.name in SKIPPED_FILES or path.name.startswith(".env") or path.suffix in SKIPPED_SUFFIXES:
            continue
        paths.append(path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()

    folder = args.folder.resolve()
    if not (folder / MARKER).is_file():
        raise SystemExit(f"{folder} has no {MARKER}, the server needs it to detect Python")

    target = folder.with_suffix(".zip")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files_to_pack(folder):
            archive.write(path, path.relative_to(folder).as_posix())

    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise SystemExit(f"{target} failed its integrity check")
        for name in archive.namelist():
            print(name)

    size = target.stat().st_size
    if size > MAX_BYTES:
        raise SystemExit(f"{target} is {size} bytes, over the 10 MB limit")
    print(f"{target} ({size} bytes)")


if __name__ == "__main__":
    main()
