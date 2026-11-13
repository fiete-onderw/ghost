"""De veiligheidsbewaker (bouwstap 3).

Uit de test met een echte iPhone (docs/VOORTGANG.md, proef C) weten we: een
nep-locatie BLIJFT staan als Ghost stopt zonder hem op te ruimen. De iPhone
doet dat dus niet zelf. Daarom loopt ALLES wat de locatie verandert via deze
ene bewaker. Die regelt drie dingen:

1. Schone start    - de eerste keer dat de iPhone bereikbaar is, vraagt Ghost
                     eerst de echte locatie terug (ook als er niets actief leek).
2. Altijd opruimen - bij elke manier van stoppen (knop, timer, Ctrl+C, venster
                     dicht, onverwachte fout) gaat er eerst een clear-verzoek
                     naar de iPhone. Zie ook exit_hooks.py.
3. Eerlijk blijven - lukt dat niet, dan onthoudt de bewaker dat (`uncertain`).
                     De webpagina blijft dan waarschuwen en Ghost blijft het
                     opnieuw proberen. Dankzij een klein merkbestand op schijf
                     weet Ghost het zelfs nog na een herstart.

Wat de bewaker NIET kan: de iPhone laat nooit weten of de echte locatie echt
terug is. "Gelukt" betekent hier: het verzoek is verstuurd, en als het kan is
bevestigd dat het is aangekomen. Controleren doe je zelf in Apple Kaarten.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ghost.device.base import DeviceError, LocationDevice, Problem
from ghost.geo import LatLon

log = logging.getLogger(__name__)

#: Zo lang mag het terugvragen bij het afsluiten hooguit duren.
SHUTDOWN_TIMEOUT_S = 20.0


class Reason(Enum):
    """Waarom de echte locatie wordt teruggevraagd."""

    STARTUP = "opstarten"
    BUTTON = "knop"
    TIMER = "timer"
    ERROR = "fout"
    CONNECTION = "verbinding"
    CTRL_C = "ctrl_c"
    WINDOW_CLOSED = "venster_gesloten"
    QUIT = "afsluiten"


@dataclass(frozen=True)
class RestoreResult:
    """Hoe een poging om de echte locatie terug te vragen is afgelopen."""

    ok: bool  #: het verzoek is verstuurd
    confirmed: bool  #: de iPhone heeft bevestigd dat het is aangekomen
    reason: Reason
    problem: Problem | None = None  #: alleen gevuld als het mislukt is
    detail: str = ""  #: technische info voor het log
    at: float = 0.0  #: tijdstip (seconden sinds 1970)


class ClosingError(Exception):
    """Ghost wordt afgesloten; er mag geen nieuwe nep-locatie meer gezet worden."""


class SafetyManager:
    def __init__(
        self,
        device: LocationDevice,
        *,
        marker: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.device = device
        self._marker = marker
        self._clock = clock
        self._lock = asyncio.Lock()  # nooit tegelijk zetten en terugvragen

        #: De nep-locatie die volgens Ghost nu actief is, of None.
        self.point: LatLon | None = None
        #: True als de iPhone MOGELIJK op een nep-locatie staat zonder dat Ghost
        #: het zeker weet: een mislukte set of clear, of een vorige keer die niet
        #: netjes is afgesloten.
        self.uncertain: bool = self._marker_exists()
        #: True als Ghost de vorige keer is weggevallen terwijl er iets actief was.
        self.unclean_previous_exit: bool = self.uncertain
        #: True zodra er deze sessie minstens één clear-verzoek gelukt is.
        self.clean_start_done = False
        #: True vanaf het moment dat Ghost aan het afsluiten is.
        self.closing = False
        self.last_restore: RestoreResult | None = None

    @property
    def needs_restore(self) -> bool:
        """Is er (mogelijk) een nep-locatie actief die nog opgeruimd moet worden?"""
        return self.point is not None or self.uncertain

    # ------------------------------------------------------------------
    # Zetten
    # ------------------------------------------------------------------

    async def set_location(self, point: LatLon) -> None:
        """Zet een nep-locatie. Mislukt het, dan wordt meteen de echte teruggevraagd."""
        async with self._lock:
            if self.closing:
                raise ClosingError
            # Eerst opschrijven, dan pas doen. Valt Ghost hierna weg, dan weet
            # de volgende start dat er mogelijk nog een nep-locatie actief is.
            self.uncertain = True
            self._write_marker()
            try:
                await self.device.set_location(point)
            except Exception:
                # We weten niet of de opdracht is aangekomen. Bij twijfel: opruimen.
                log.warning("Nep-locatie zetten mislukt; de echte locatie wordt voor de zekerheid teruggevraagd")
                await self._restore_locked(Reason.ERROR)
                raise
            self.point = point
            self.uncertain = False

    # ------------------------------------------------------------------
    # Terugvragen
    # ------------------------------------------------------------------

    async def restore(self, reason: Reason) -> RestoreResult:
        """Vraag de echte locatie terug. Mag altijd en zo vaak als je wilt."""
        async with self._lock:
            return await self._restore_locked(reason)

    async def _restore_locked(self, reason: Reason) -> RestoreResult:
        result = await self._clear_once(reason)
        if result.ok and not result.confirmed:
            # Verstuurd, maar de iPhone gaf geen teken van leven. Eén keer
            # opnieuw over een verse verbinding, zoals je zelf ook zou doen.
            log.warning("Clear-verzoek niet bevestigd; nieuwe poging over een verse verbinding")
            await self._close_device_quietly()
            result = await self._clear_once(reason)
        self.last_restore = result
        return result

    async def _clear_once(self, reason: Reason) -> RestoreResult:
        try:
            confirmed = await self.device.clear_location()
        except DeviceError as error:
            log.error("Echte locatie terugvragen mislukt (%s): %s", reason.value, error)
            self.uncertain = True
            return RestoreResult(False, False, reason, error.problem, error.detail, self._clock())
        except Exception as error:
            log.exception("Echte locatie terugvragen gaf een onverwachte fout (%s)", reason.value)
            self.uncertain = True
            return RestoreResult(False, False, reason, Problem.UNKNOWN, repr(error), self._clock())
        self.point = None
        self.uncertain = False
        self.clean_start_done = True
        self._remove_marker()
        log.info("Echte locatie teruggevraagd (%s, aankomst bevestigd: %s)", reason.value, bool(confirmed))
        return RestoreResult(True, bool(confirmed), reason, at=self._clock())

    # ------------------------------------------------------------------
    # Afsluiten
    # ------------------------------------------------------------------

    async def shutdown(self, reason: Reason, *, timeout: float = SHUTDOWN_TIMEOUT_S) -> RestoreResult | None:
        """Ruim op voordat Ghost stopt. Elke afsluitroute roept dit aan.

        Geeft None terug als er niets te doen was. Mag meerdere keren worden
        aangeroepen; de tweede keer is er dan niets meer te doen.
        De verbinding blijft open; die sluit je daarna met `close()`.
        """
        self.closing = True
        try:
            return await asyncio.wait_for(self._shutdown_restore(reason), timeout)
        except TimeoutError:
            log.error("Echte locatie terugvragen bij het afsluiten duurde langer dan %.0f seconden", timeout)
            self.uncertain = True
            self.last_restore = RestoreResult(
                False, False, reason, Problem.CONNECTION_LOST, "tijd verstreken bij afsluiten", self._clock()
            )
            return self.last_restore

    async def _shutdown_restore(self, reason: Reason) -> RestoreResult | None:
        async with self._lock:
            if not self._should_clear_on_exit():
                return None
            return await self._restore_locked(reason)

    def _should_clear_on_exit(self) -> bool:
        if self.needs_restore:
            return True  # altijd proberen, ook als de verbinding weg lijkt
        if not self.clean_start_done:
            # Er is deze sessie nog nooit een clear gelukt. Voor de zekerheid
            # alsnog doen, maar alleen als er al een verbinding is: voor "er is
            # niets gebeurd" hoeven we bij het afsluiten geen tunnel op te bouwen.
            return self.device.connection_alive()
        return False  # de laatste actie was een gelukte clear: er valt niets op te ruimen

    async def close(self) -> None:
        """Sluit de verbinding met het toestel. Vraagt NIETS terug; doe eerst `shutdown()`."""
        self.closing = True
        await self._close_device_quietly()

    async def _close_device_quietly(self) -> None:
        try:
            await self.device.close()
        except Exception:
            log.info("Verbinding sluiten gaf een fout", exc_info=True)

    # ------------------------------------------------------------------
    # Merkbestand
    # ------------------------------------------------------------------
    # Schrijven en wissen mag NOOIT een fout geven: een volle schijf mag het
    # terugvragen van de echte locatie niet in de weg staan.

    def _marker_exists(self) -> bool:
        try:
            return self._marker is not None and self._marker.exists()
        except OSError:
            return False

    def _write_marker(self) -> None:
        if self._marker is None:
            return
        try:
            self._marker.parent.mkdir(parents=True, exist_ok=True)
            self._marker.write_text(
                "Ghost heeft een nep-locatie gezet en nog niet opgeruimd.\n"
                "Dit bestand verdwijnt vanzelf zodra de echte locatie is teruggevraagd.\n"
                f"Gezet op: {time.strftime('%Y-%m-%d %H:%M:%S')}\n",
                encoding="utf-8",
            )
        except OSError:
            log.warning("Merkbestand schrijven mislukt", exc_info=True)

    def _remove_marker(self) -> None:
        if self._marker is None:
            return
        try:
            self._marker.unlink(missing_ok=True)
        except OSError:
            log.warning("Merkbestand verwijderen mislukt", exc_info=True)
