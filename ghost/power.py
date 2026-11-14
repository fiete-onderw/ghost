"""Houdt de laptop wakker zolang er een nep-locatie actief is.

Valt de laptop in slaap, dan valt de verbinding met de iPhone weg, terwijl de
iPhone op de nep-locatie blijft staan. Vooral bij een route die je laat lopen
terwijl je iets anders doet, gebeurt dat snel: Windows gaat op de accu vaak al
na een paar minuten slapen.

Dit vraagt Windows alleen om niet VANZELF te gaan slapen. Het scherm mag
gewoon uitgaan. Klap je de laptop dicht of kies je zelf "Slaapstand", dan
slaapt hij nog steeds; daar kan en mag een programma niets aan doen.

Zodra er niets meer actief is, of Ghost stopt, geldt weer het gewone gedrag.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable

log = logging.getLogger(__name__)

# Waarden uit de Windows-documentatie van SetThreadExecutionState.
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

Setter = Callable[[int], object]


def _windows_setter() -> Setter | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        return ctypes.windll.kernel32.SetThreadExecutionState
    except Exception:
        log.info("Wakker houden is op deze computer niet beschikbaar", exc_info=True)
        return None


class KeepAwake:
    def __init__(self, setter: Setter | None = None) -> None:
        self._setter = setter if setter is not None else _windows_setter()
        self.active = False

    def set(self, on: bool) -> None:
        """Zet het wakker houden aan of uit. Vaak aanroepen mag: er gebeurt alleen iets bij een verandering."""
        if on == self.active:
            return
        self.active = on
        if self._setter is None:
            return
        try:
            self._setter(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
            log.info("Laptop wakker houden: %s", "aan" if on else "uit")
        except Exception:
            # Mag nooit iets anders in de weg zitten.
            log.info("Wakker houden instellen mislukt", exc_info=True)
