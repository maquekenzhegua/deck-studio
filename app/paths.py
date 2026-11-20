"""Where Deck Studio keeps things.

Two worlds have to work with the same code path:

* running from source, where the repo is the root, and
* running from a PyInstaller build, where read-only assets live under
  ``sys._MEIPASS`` but user files must go somewhere writable and permanent.

Everything that touches the filesystem goes through this module so the
desktop build never writes next to the executable (Program Files is not
writable, and a temp directory loses the user's work on exit).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
ROOT = BUNDLE


def resource(*parts: str) -> Path:
    """Read-only asset shipped with the app (static UI, sample data)."""
    return BUNDLE.joinpath(*parts)


def home() -> Path:
    """Writable workspace that survives restarts."""
    override = os.environ.get("DECK_HOME")
    if override:
        base = Path(override).expanduser()
    else:
        docs = Path.home() / "Documents"
        base = (docs if docs.exists() else Path.home()) / "Deck Studio"
    base.mkdir(parents=True, exist_ok=True)
    return base


def ensure() -> dict[str, Path]:
    base = home()
    dirs = {
        "home": base,
        "uploads": base / "uploads",
        "out": base / "out",
        "media": base / "media",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs
