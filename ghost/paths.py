"""Waar Ghost persoonlijke bestanden bewaart (logs, later ook favorieten).

Bewust BUITEN de projectmap: wat niet in de repo staat, kan ook nooit per
ongeluk op GitHub belanden.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    """De map met persoonlijke gegevens. Op Windows: %LOCALAPPDATA%\\Ghost."""
    override = os.environ.get("GHOST_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Local") / "Ghost"
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / "ghost"


def log_dir() -> Path:
    return data_dir() / "logs"


def log_file() -> Path:
    return log_dir() / "ghost.log"
