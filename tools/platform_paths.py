"""Native artifact paths shared by the development tools."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"


def executable_suffix(platform: str = sys.platform) -> str:
    return ".exe" if platform == "win32" else "_linux"


def simulator_path(name: str, platform: str = sys.platform) -> Path:
    return BUILD / f"{name}{executable_suffix(platform)}"


def library_name(name: str, platform: str = sys.platform) -> str:
    return f"lib{name}.dll" if platform == "win32" else f"lib{name}.so"


def launcher_path(platform: str = sys.platform) -> Path:
    name = "blitz-challenge-win.exe" if platform == "win32" else "blitz-challenge-linux"
    return ROOT / "localRunner" / name
