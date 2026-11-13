"""Ghost starten als webpagina: `python -m ghost web` (of dubbelklik op start.bat).

De volgorde bij het starten:
1. Poort reserveren, ALLEEN op 127.0.0.1 (je eigen laptop).
2. Veiligheidsbewaker, regisseur en webserver klaarzetten.
3. Ctrl+C en "venster dicht" opvangen.
4. De browser openen. De iPhone wordt intussen op de achtergrond gecontroleerd.

De volgorde bij het stoppen is belangrijker, en staat daarom in één `finally`-blok
dat ALTIJD draait (knop, Ctrl+C, onverwachte fout):
1. Eerst de echte locatie terugvragen.
2. Dan pas de bewaking, de verbinding en de webserver stoppen.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
import sys
import urllib.request
import webbrowser
from collections.abc import Generator
from pathlib import Path

import uvicorn

from ghost import __version__, paths
from ghost.cli import EXIT_OK, EXIT_PROBLEM, say
from ghost.controller import Controller
from ghost.device.base import LocationDevice
from ghost.exit_hooks import ExitHooks
from ghost.safety import Reason, RestoreResult, SafetyManager
from ghost.uitleg import DEVELOPER_MODE_REMINDER
from ghost.web.app import create_app
from ghost.web.security import new_token

log = logging.getLogger(__name__)

HOST = "127.0.0.1"  # NOOIT 0.0.0.0: dan zou iedereen op je wifi erbij kunnen
DEFAULT_PORT = 8765
#: Zo lang wacht Ghost na het starten met de eerste controle van de iPhone,
#: zodat de pagina in de browser eerst kan laden.
STARTUP_DELAY_S = 1.0

_REASON_TEXT = {
    Reason.CTRL_C: "Ctrl+C",
    Reason.QUIT: "afsluiten gevraagd",
    Reason.WINDOW_CLOSED: "venster gesloten",
    Reason.ERROR: "onverwachte fout",
}


class GhostServer(uvicorn.Server):
    """De webserver, maar dan zonder eigen afhandeling van Ctrl+C.

    Normaal vangt uvicorn Ctrl+C zelf op, en bij een tweede Ctrl+C stopt het
    dan meteen. Dat willen we niet: Ghost moet eerst de echte locatie kunnen
    terugvragen. Daarom doet exit_hooks.py dit, en uvicorn niet.
    """

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        yield


class StopRequest:
    """Het seintje "Ghost moet stoppen", met de reden erbij. De eerste reden telt."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self.reason: Reason | None = None

    def request(self, reason: Reason) -> None:
        if self.reason is None:
            self.reason = reason
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()


def bind_local(port: int) -> socket.socket:
    """Reserveer de poort, uitsluitend op 127.0.0.1. Geeft OSError als de poort bezet is."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            # Op Windows: eis de poort exclusief op, zodat geen ander programma
            # hem kan "meedelen" en zich als Ghost kan voordoen.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((HOST, port))
        sock.listen(64)
        sock.setblocking(False)
    except BaseException:
        sock.close()
        raise
    return sock


def ghost_is_running(port: int) -> bool:
    """Draait er op deze poort al een Ghost?"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # nooit via een proxy
    try:
        with opener.open(f"http://{HOST}:{port}/api/hello", timeout=2.0) as response:
            return json.loads(response.read(1000)).get("app") == "ghost"
    except Exception:
        return False


def open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        log.warning("Browser openen mislukt", exc_info=True)


def run_web(
    *,
    device: LocationDevice,
    demo: bool,
    debug: bool,
    port: int,
    browser: bool,
    log_path: Path,
) -> int:
    """Start de webinterface en blijf draaien tot Ghost gestopt wordt. Geeft de afsluitcode terug."""
    coroutine = serve(device=device, demo=demo, debug=debug, port=port, browser=browser, log_path=log_path)
    try:
        if sys.platform == "win32":
            # pymobiledevice3 heeft op Windows dit type "event loop" nodig.
            return asyncio.run(coroutine, loop_factory=asyncio.SelectorEventLoop)
        return asyncio.run(coroutine)
    except KeyboardInterrupt:
        # Alleen mogelijk in de eerste of laatste fractie van een seconde, als
        # exit_hooks nog niet of niet meer actief is. Er is dan niets actief.
        say()
        say("Gestopt met Ctrl+C.")
        return EXIT_OK


async def serve(
    *,
    device: LocationDevice,
    demo: bool,
    debug: bool,
    port: int,
    browser: bool,
    log_path: Path,
    stop: StopRequest | None = None,
) -> int:
    url = f"http://{HOST}:{port}"
    try:
        sock = bind_local(port)
    except OSError as error:
        log.warning("Poort %s is bezet: %s", port, error)
        if await asyncio.to_thread(ghost_is_running, port):
            say("Ghost draait al. Je hoeft hem niet nog een keer te starten.")
            say(f"Ik open de pagina die al draait: {url}")
            if browser:
                await asyncio.to_thread(open_browser, url)
            return EXIT_OK
        say(f"Poort {port} is al in gebruik door een ander programma.")
        say("Probeer een andere poort, bijvoorbeeld:")
        say(f"  uv run python -m ghost web --port {port + 1}")
        return EXIT_PROBLEM

    loop = asyncio.get_running_loop()
    stop = stop or StopRequest()
    # In demomodus geen merkbestand: een nep-iPhone hoeft na een herstart niets op te ruimen.
    safety = SafetyManager(device, marker=None if demo else paths.active_marker())
    controller = Controller(safety, demo=demo, debug=debug, log_path=log_path, startup_delay=STARTUP_DELAY_S)
    app = create_app(controller, token=new_token(), port=port, on_quit=lambda: stop.request(Reason.QUIT))
    config = uvicorn.Config(
        app,
        loop="none",  # wij kiezen de event loop zelf (zie run_web)
        lifespan="off",
        ws="none",
        access_log=False,
        log_config=None,  # uvicorn schrijft via onze eigen logging naar het logbestand
        server_header=False,
        timeout_graceful_shutdown=3,
    )
    server = GhostServer(config)
    hooks = ExitHooks(loop, request_stop=stop.request, emergency=safety.shutdown)
    exit_code = EXIT_OK
    server_task: asyncio.Task[None] | None = None

    hooks.install()
    try:
        server_task = asyncio.create_task(server.serve(sockets=[sock]), name="ghost-webserver")
        while not server.started and not server_task.done():
            await asyncio.sleep(0.02)
        if server_task.done():
            await server_task  # gooit de fout waardoor de server niet kon starten
            raise RuntimeError("De webserver is meteen weer gestopt.")

        controller.start()
        log.info("Webinterface gestart op %s (demo=%s)", url, demo)
        say(f"Ghost {__version__} draait.")
        say()
        say(f"  Open in je browser:  {url}")
        say()
        say("  Laat dit venster open zolang je Ghost gebruikt.")
        say("  Stoppen: klik in de pagina op 'Ghost afsluiten', of druk hier op Ctrl+C.")
        say("  Bij het stoppen vraagt Ghost altijd eerst je echte locatie terug.")
        say()
        if browser:
            await asyncio.to_thread(open_browser, url)

        stop_task = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait({stop_task, server_task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop_task.cancel()
        if stop.reason is None:
            log.error("De webserver is onverwacht gestopt")
            exit_code = EXIT_PROBLEM
    except Exception:
        log.exception("Onverwachte fout in de webinterface")
        exit_code = EXIT_PROBLEM
        say()
        say("Er ging iets onverwachts mis. Technische details staan in:")
        say(f"  {log_path}")
    finally:
        # Dit blok draait ALTIJD. Eerst doen, dan pas praten.
        reason = stop.reason or Reason.ERROR
        result = await safety.shutdown(reason)
        say()
        say(f"Ghost stopt ({_REASON_TEXT.get(reason, reason.value)}).")
        _report(safety, result, reason)
        if safety.needs_restore:
            exit_code = EXIT_PROBLEM

        await controller.close()
        await safety.close()
        server.should_exit = True
        if server_task is not None:
            _, pending = await asyncio.wait({server_task}, timeout=6.0)
            for task in pending:
                task.cancel()
        with contextlib.suppress(OSError):
            sock.close()
        hooks.uninstall()
    return exit_code


def _report(safety: SafetyManager, result: RestoreResult | None, reason: Reason) -> None:
    """Vertel in het zwarte venster eerlijk hoe het opruimen is afgelopen."""
    if safety.needs_restore:
        say()
        say("LET OP: de echte locatie kon NIET worden teruggevraagd.")
        say("Je iPhone staat mogelijk nog op de nep-locatie.")
        say("Zo los je het op:")
        say("  1. Controleer de kabel en ontgrendel de iPhone.")
        say("  2. Start Ghost opnieuw. Ghost vraagt bij het opstarten meteen je echte locatie terug.")
        say("  3. Kijk in Apple Kaarten of je echte locatie weer klopt.")
        return
    if result is None and safety.last_restore is not None and safety.last_restore.reason is reason:
        result = safety.last_restore  # de knop 'Ghost afsluiten' had het terugvragen al gedaan
    if result is None or not result.ok:
        say("Er was geen nep-locatie actief.")
        return
    if result.confirmed:
        say("Echte locatie teruggevraagd. Het verzoek is bij de iPhone aangekomen.")
    else:
        say("Echte locatie teruggevraagd. De iPhone heeft de ontvangst niet bevestigd.")
    say("Controleer in Apple Kaarten of je echte locatie weer klopt.")
    say()
    say(DEVELOPER_MODE_REMINDER)
