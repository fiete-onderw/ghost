"""Vraagt aan Windows zelf of er een iPhone of iPad aan een USB-poort hangt.

Waarom: "Ghost vindt geen iPhone" kan twee heel verschillende dingen betekenen.

1. Er hangt echt niets aan de kabel (of de kabel geeft alleen stroom door).
2. Windows ziet de iPhone wel, maar het onderdeel van iTunes dat met de
   iPhone praat (de Apple-service) geeft hem niet door. Dan is de kabel goed
   en ligt het aan vertrouwen, ontgrendelen of het stuurprogramma van Apple.

Met dit bestand kan Ghost die twee uit elkaar houden en de juiste uitleg geven.

Het antwoord is altijd een van drie: een lijst (leeg = Windows ziet niets),
of None als het niet te bepalen was. Bij twijfel dus None, nooit een gok.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
from collections.abc import Callable

log = logging.getLogger(__name__)

# Apple heeft leveranciersnummer 05AC. iPhones melden zich als product 12A8,
# iPads als 12AB. Zo tellen een Apple-toetsenbord of -muis niet mee.
_COMMAND = (
    "Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | "
    "Where-Object { $_.InstanceId -match '^USB\\\\VID_05AC&PID_12A[8B]' } | "
    "ForEach-Object { $_.FriendlyName + ' | ' + $_.Class + ' | ' + $_.Status }"
)
TIMEOUT_S = 8.0
CACHE_S = 15.0  #: zo lang wordt een antwoord hergebruikt; PowerShell starten kost ongeveer een seconde

Runner = Callable[[], str]

_cache: tuple[float, list[str] | None] | None = None


def _run_powershell() -> str:
    result = subprocess.run(  # noqa: S603 - vaste opdracht, geen invoer van buiten
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _COMMAND],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=True,
    )
    return result.stdout


def apple_devices_seen_by_windows(runner: Runner | None = None) -> list[str] | None:
    """Regels als "Apple Mobile Device USB Composite Device | USB | OK", of None als het onbekend is."""
    if runner is None:
        if sys.platform != "win32":
            return None
        runner = _run_powershell
    try:
        output = runner()
    except Exception:
        log.info("Kon Windows niet vragen welke Apple-toestellen zijn aangesloten", exc_info=True)
        return None
    return [line.strip() for line in output.splitlines() if line.strip()]


async def apple_devices_seen_by_windows_cached() -> list[str] | None:
    """Hetzelfde, maar zonder Ghost stil te zetten en hooguit eens per kwart minuut."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_S:
        return _cache[1]
    seen = await asyncio.to_thread(apple_devices_seen_by_windows)
    _cache = (now, seen)
    return seen
