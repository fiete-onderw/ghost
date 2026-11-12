"""Logging naar een bestand, zodat je fouten kunt delen.

Privacyregels:
- Ghost zelf schrijft coördinaten afgerond op 2 decimalen (zie coords.format_for_log).
- Meldingen van andere libraries komen alleen in het log vanaf niveau WARNING.
- Alleen met --debug wordt alles gelogd, inclusief exacte coördinaten.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ghost import paths

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(*, debug: bool = False) -> Path:
    """Zet logging aan en geef het pad van het logbestand terug."""
    paths.log_dir().mkdir(parents=True, exist_ok=True)
    log_path = paths.log_file()

    handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_FORMAT))

    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.WARNING)
    logging.getLogger("ghost").setLevel(logging.DEBUG if debug else logging.INFO)

    if debug:
        logging.getLogger("ghost").warning(
            "DEBUGMODUS: dit logbestand kan exacte coördinaten en apparaatgegevens bevatten. "
            "Lees het na voordat je het deelt."
        )
    return log_path
