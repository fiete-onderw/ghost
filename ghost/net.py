"""Eén plek waar Ghost iets van internet ophaalt.

Ghost haalt maar twee dingen op, en alleen als jij daarom vraagt:
- een route over de weg (routing.openstreetmap.de);
- een adres (nominatim.openstreetmap.org), en dat alleen als je het hebt aangezet.

Alles hier gaat via `fetch_json`. Zo is op één plek te zien en te testen wat
Ghost naar buiten stuurt. Er gaan nooit gegevens over jou of je iPhone mee:
alleen de plekken die je zelf hebt aangeklikt of de tekst die je zelf zoekt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

from ghost import __version__

log = logging.getLogger(__name__)

#: Zo maakt Ghost zich bekend bij de diensten van OpenStreetMap. Die eisen een
#: herkenbare naam; de standaardnaam van een programmeertaal mag niet.
USER_AGENT = f"Ghost-locatiesimulator/{__version__}"

TIMEOUT_S = 15.0
MAX_BYTES = 8_000_000


class NetError(Exception):
    """Ophalen is mislukt. `status` is het antwoordnummer van de server, of None als er geen antwoord kwam."""

    def __init__(self, detail: str, status: int | None = None, data: Any = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status
        self.data = data


def _fetch_blocking(url: str, timeout: float) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - alleen vaste https-adressen
            raw = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        data = None
        try:
            data = json.loads(error.read(100_000))
        except Exception:
            pass
        raise NetError(f"HTTP {error.code}", error.code, data) from error
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        raise NetError(repr(error)) from error
    if len(raw) > MAX_BYTES:
        raise NetError("antwoord te groot")
    try:
        return json.loads(raw)
    except ValueError as error:
        raise NetError("antwoord is geen JSON") from error


async def fetch_json(url: str, *, timeout: float = TIMEOUT_S) -> Any:
    """Haal een JSON-antwoord op. Blokkeert Ghost niet: het ophalen gebeurt in een aparte draad."""
    if not url.startswith("https://"):
        raise NetError("alleen https is toegestaan")
    return await asyncio.to_thread(_fetch_blocking, url, timeout)


class Throttle:
    """Zorgt dat er minstens `min_interval` seconden tussen twee verzoeken zit.

    De diensten van OpenStreetMap zijn gratis en draaien op giften. Hun regel
    is: hooguit één verzoek per seconde. Ghost houdt zich daar hier aan,
    hoe vaak je ook klikt.
    """

    def __init__(self, min_interval: float = 1.0) -> None:
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def __aenter__(self) -> None:
        await self._lock.acquire()
        wait = self._last + self._min_interval - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)

    async def __aexit__(self, *error: object) -> None:
        self._last = time.monotonic()
        self._lock.release()
