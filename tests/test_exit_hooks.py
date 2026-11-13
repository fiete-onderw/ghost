"""Tests voor Ctrl+C en "venster dicht".

De Windows-koppeling zelf (SetConsoleCtrlHandler) kan hier niet getest worden;
die test je met de hand (zie docs/VOORTGANG.md). Wat hier WEL getest wordt, is
wat onze functie doet als Windows hem aanroept.
"""

import asyncio
import signal
import time

from ghost.device.fake import FakeDevice
from ghost.exit_hooks import CTRL_BREAK_EVENT, CTRL_C_EVENT, CTRL_CLOSE_EVENT, CTRL_SHUTDOWN_EVENT, ExitHooks
from ghost.geo import LatLon
from ghost.safety import Reason, SafetyManager


def haken(noodstop, *, budget=2.0):
    redenen = []
    hooks = ExitHooks(
        asyncio.get_running_loop(), request_stop=redenen.append, emergency=noodstop, close_budget_s=budget
    )
    return hooks, redenen


async def test_venster_dicht_vraagt_de_echte_locatie_terug_voordat_windows_ghost_stopt():
    toestel = FakeDevice()
    safety = SafetyManager(toestel)
    await safety.set_location(LatLon(48.8584, 2.2945))
    hooks, redenen = haken(safety.shutdown)

    # Windows roept de functie aan in een aparte draad; dat doen we hier na.
    afgehandeld = await asyncio.to_thread(hooks.handle_console_event, CTRL_CLOSE_EVENT)

    assert afgehandeld is True
    assert toestel.location is None
    assert toestel.history[-1] == ("clear", None)
    assert safety.last_restore.reason is Reason.WINDOW_CLOSED
    assert redenen == [Reason.WINDOW_CLOSED]


async def test_windows_afsluiten_wordt_net_zo_behandeld():
    toestel = FakeDevice()
    safety = SafetyManager(toestel)
    await safety.set_location(LatLon(1.0, 2.0))
    hooks, _ = haken(safety.shutdown)
    assert await asyncio.to_thread(hooks.handle_console_event, CTRL_SHUTDOWN_EVENT) is True
    assert toestel.location is None


async def test_ctrl_c_laten_we_aan_python_over():
    aangeroepen = []

    async def noodstop(reden):
        aangeroepen.append(reden)

    hooks, redenen = haken(noodstop)
    assert hooks.handle_console_event(CTRL_C_EVENT) is False
    assert hooks.handle_console_event(CTRL_BREAK_EVENT) is False
    assert aangeroepen == [] and redenen == []


async def test_venster_dicht_wacht_niet_langer_dan_het_budget():
    """Windows geeft maar een paar seconden. Hangt het terugvragen, dan keren we toch op tijd terug."""

    async def hangt(reden):
        await asyncio.sleep(30)

    hooks, _ = haken(hangt, budget=0.2)
    start = time.monotonic()
    assert await asyncio.to_thread(hooks.handle_console_event, CTRL_CLOSE_EVENT) is True
    assert time.monotonic() - start < 2.0


async def test_ctrl_c_geeft_een_seintje_aan_de_app_en_stopt_python_niet():
    async def noodstop(reden):
        pass

    hooks, redenen = haken(noodstop)
    vorige = signal.getsignal(signal.SIGINT)
    hooks.install()
    try:
        signal.raise_signal(signal.SIGINT)  # zonder onze haak zou dit een KeyboardInterrupt geven
        await asyncio.sleep(0.05)
        assert redenen == [Reason.CTRL_C]
    finally:
        hooks.uninstall()
    assert signal.getsignal(signal.SIGINT) is vorige
