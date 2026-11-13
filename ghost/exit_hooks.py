"""Alle manieren waarop Ghost van buitenaf gestopt kan worden.

Er zijn er drie die we kunnen opvangen:

1. Ctrl+C (en Ctrl+Break) in het zwarte venster.
   Python krijgt dan een "signaal". Wij vragen de app netjes te stoppen; de
   app vraagt eerst de echte locatie terug en sluit daarna pas af.

2. Het zwarte venster sluiten met het kruisje (alleen Windows).
   Windows roept dan een functie van ons aan en geeft ons daarna ongeveer
   5 seconden. Na die tijd, of zodra onze functie klaar is, stopt Windows het
   programma hoe dan ook. In die paar seconden sturen we het clear-verzoek.

3. Een onverwachte fout in Ghost zelf.
   Die loopt niet via dit bestand, maar via `try/finally` in web/server.py.

Wat NIET op te vangen is: de stekker eruit, een lege accu, of Taakbeheer >
"Proces beëindigen". Daarvoor bestaat de schone start: de volgende keer dat
Ghost start, wordt als eerste de echte locatie teruggevraagd.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from ghost.safety import Reason

log = logging.getLogger(__name__)

# Codes die Windows meegeeft aan de venster-functie.
CTRL_C_EVENT = 0
CTRL_BREAK_EVENT = 1
CTRL_CLOSE_EVENT = 2  # het venster wordt gesloten
CTRL_LOGOFF_EVENT = 5  # de gebruiker logt uit
CTRL_SHUTDOWN_EVENT = 6  # Windows sluit af

#: Windows geeft ons 5 seconden na het sluiten van het venster. We houden marge.
CLOSE_BUDGET_S = 4.0


class ExitHooks:
    """Koppelt Ctrl+C en "venster dicht" aan de veiligheidsbewaker.

    - `request_stop(reason)` wordt aangeroepen bij Ctrl+C. Het moet snel zijn
      en alleen een seintje geven; het echte werk gebeurt in de app.
    - `emergency(reason)` wordt aangeroepen als het venster dichtgaat. Het moet
      de echte locatie terugvragen en krijgt daar `close_budget_s` seconden voor.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        *,
        request_stop: Callable[[Reason], None],
        emergency: Callable[[Reason], Awaitable[Any]],
        close_budget_s: float = CLOSE_BUDGET_S,
    ) -> None:
        self._loop = loop
        self._request_stop = request_stop
        self._emergency = emergency
        self._budget = close_budget_s
        self._previous: dict[int, Any] = {}
        self._console_handler: Any = None  # bewaren, anders ruimt Python de functie op terwijl Windows hem nog kent

    # ------------------------------------------------------------------
    # Aan- en uitzetten
    # ------------------------------------------------------------------

    def install(self) -> None:
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):  # SIGBREAK bestaat alleen op Windows
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                self._previous[sig] = signal.signal(sig, self._on_signal)
            except (ValueError, OSError):
                log.warning("Signaal %s kon niet worden opgevangen", name, exc_info=True)
        if sys.platform == "win32":
            self._install_console_handler()

    def uninstall(self) -> None:
        for sig, previous in self._previous.items():
            try:
                signal.signal(sig, previous)
            except (ValueError, OSError, TypeError):
                pass
        self._previous.clear()
        if sys.platform == "win32":
            self._uninstall_console_handler()

    # ------------------------------------------------------------------
    # Ctrl+C
    # ------------------------------------------------------------------

    def _on_signal(self, signum: int, frame: Any) -> None:
        reason = Reason.QUIT if signum == getattr(signal, "SIGTERM", None) else Reason.CTRL_C
        # call_soon_threadsafe maakt de wachtende app meteen wakker.
        try:
            self._loop.call_soon_threadsafe(self._request_stop, reason)
        except RuntimeError:
            pass  # de app is al gestopt

    # ------------------------------------------------------------------
    # Venster dicht (Windows)
    # ------------------------------------------------------------------

    def handle_console_event(self, event: int) -> bool:
        """Windows roept dit aan in een APARTE draad (thread), niet in de app zelf.

        Geeft True terug als wij de gebeurtenis hebben afgehandeld. Bij Ctrl+C
        geven we False terug: dan handelt Python hem af op de gewone manier.
        """
        if event not in (CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT):
            return False
        log.warning("Het venster wordt gesloten (code %s): echte locatie wordt teruggevraagd", event)
        try:
            # Geef de opdracht door aan de app en wacht hier tot hij klaar is.
            # Zolang deze functie niet terugkeert, laat Windows Ghost leven
            # (tot maximaal ongeveer 5 seconden).
            future = asyncio.run_coroutine_threadsafe(self._run_emergency(), self._loop)
            future.result(timeout=self._budget)
        except Exception:
            log.error("Terugvragen bij het sluiten van het venster is niet (op tijd) gelukt", exc_info=True)
        return True

    async def _run_emergency(self) -> None:
        await self._emergency(Reason.WINDOW_CLOSED)
        self._request_stop(Reason.WINDOW_CLOSED)

    def _install_console_handler(self) -> None:  # pragma: no cover - alleen op Windows
        try:
            import ctypes
            from ctypes import wintypes

            handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.SetConsoleCtrlHandler.argtypes = (handler_type, wintypes.BOOL)
            kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL
            handler = handler_type(lambda event: bool(self.handle_console_event(int(event))))
            if not kernel32.SetConsoleCtrlHandler(handler, True):
                raise ctypes.WinError(ctypes.get_last_error())
            self._console_handler = (kernel32, handler)
        except Exception:
            log.error("Kon 'venster dicht' niet opvangen; gebruik Ctrl+C of de knop in de pagina", exc_info=True)

    def _uninstall_console_handler(self) -> None:  # pragma: no cover - alleen op Windows
        if self._console_handler is None:
            return
        kernel32, handler = self._console_handler
        try:
            kernel32.SetConsoleCtrlHandler(handler, False)
        except Exception:
            log.debug("Venster-functie afmelden gaf een fout", exc_info=True)
        # `handler` blijft bewust bestaan (in self._console_handler): mocht
        # Windows hem toch nog aanroepen, dan is dat geen crash.
