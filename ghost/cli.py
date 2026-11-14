"""Ghost starten vanuit de terminal.

De gewone manier is de webpagina (dat doet start.bat ook):

    uv run python -m ghost web

Daarnaast zijn er losse commando's om de basis te testen zonder webpagina:

    uv run python -m ghost status
    uv run python -m ghost set 52.3676 4.9041
    uv run python -m ghost clear

Met --demo praat Ghost tegen een nep-iPhone, zodat je alles kunt proberen
zonder toestel:

    uv run python -m ghost --demo web
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from ghost import __version__
from ghost.coords import CoordinateError, format_for_log, format_for_screen, parse_latlon
from ghost.device.base import DeviceError, DeviceStatus, LocationDevice, Problem
from ghost.logging_setup import setup_logging
from ghost.uitleg import DEVELOPER_MODE_REMINDER, RUN, uitleg

log = logging.getLogger("ghost.cli")

EXIT_OK = 0
EXIT_PROBLEM = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130


def say(text: str = "") -> None:
    """Toon een regel tekst. Mag NOOIT een fout geven.

    Als het venster al dicht is, kan er niets meer getoond worden. Dat mag
    het terugzetten van de echte locatie nooit in de weg staan.
    """
    try:
        print(text, flush=True)
    except (OSError, ValueError):
        pass


# ----------------------------------------------------------------------
# Weergave
# ----------------------------------------------------------------------

_CHECKS = [
    # (veld in DeviceStatus, tekst als het goed is, tekst als het niet goed is)
    ("apple_service", "Apple USB-service gevonden", "Apple USB-service niet gevonden"),
    ("connected", "iPhone verbonden", "Geen iPhone verbonden"),
    ("trusted", "iPhone vertrouwt deze laptop", "iPhone vertrouwt deze laptop nog niet"),
    ("ios_supported", "iOS-versie ondersteund (17.4 of hoger)", "iOS-versie te oud (17.4 of hoger nodig)"),
    ("developer_mode", "Developer Mode staat aan", "Developer Mode staat uit"),
    ("tunnel", "Tunnel naar de iPhone werkt", "Tunnel naar de iPhone werkt niet"),
    ("location_service", "Locatiedienst bereikbaar", "Locatiedienst niet bereikbaar"),
]


def print_status(status: DeviceStatus) -> None:
    for field, good, bad in _CHECKS:
        value = getattr(status, field)
        if value is True:
            line = f"  [OK] {good}"
            if field == "connected":
                line += f": {_describe(status)}"
        elif value is False:
            line = f"  [X]  {bad}"
        else:
            line = f"  [ ]  {good} - niet gecontroleerd"
        say(line)

    if status.ddi_mounted is not None:
        state = "gemount" if status.ddi_mounted else "niet gemount"
        say(f"  [i]  Ontwikkelaars-image (DDI): {state}")
    if status.device_count > 1:
        say(f"  [i]  Er zijn {status.device_count} iPhones aangesloten; Ghost gebruikt de eerste.")


def _describe(status: DeviceStatus) -> str:
    details = ", ".join(part for part in (status.model, f"iOS {status.ios_version}" if status.ios_version else "") if part)
    name = status.name or "naam onbekend"
    return f"{name} ({details})" if details else name


def print_problem(problem: Problem, detail: str, log_path: Path) -> None:
    info = uitleg(problem)
    say()
    say(f"Probleem: {info.titel}")
    say(f"  {info.wat}")
    if detail and problem in (Problem.NOT_TRUSTED, Problem.NO_DEVICE):
        say(f"  {detail}")
    say("Zo los je het op:")
    for number, step in enumerate(info.stappen, start=1):
        say(f"  {number}. {step}")
    say(f"Logbestand met technische details: {log_path}")


# ----------------------------------------------------------------------
# Wachten
# ----------------------------------------------------------------------


async def wait_for_enter() -> None:
    """Wacht tot de gebruiker op Enter drukt, zonder de rest van het programma stil te zetten."""
    if sys.platform == "win32":
        import msvcrt

        while True:
            if msvcrt.kbhit() and msvcrt.getwch() in ("\r", "\n"):
                return
            await asyncio.sleep(0.1)

    loop = asyncio.get_running_loop()
    try:
        fd = sys.stdin.fileno()
        done = loop.create_future()

        def on_input() -> None:
            sys.stdin.readline()
            if not done.done():
                done.set_result(None)

        loop.add_reader(fd, on_input)
    except (ValueError, OSError):
        await asyncio.to_thread(sys.stdin.readline)
        return
    try:
        await done
    finally:
        loop.remove_reader(fd)


async def _keep_responsive() -> None:
    """Zorgt dat Ctrl+C op Windows meteen werkt.

    Windows onderbreekt een wachtend Python-programma niet uit zichzelf. Door
    elke kwart seconde even wakker te worden, krijgt Ctrl+C de kans.
    """
    while True:
        await asyncio.sleep(0.25)


# ----------------------------------------------------------------------
# Commando's
# ----------------------------------------------------------------------


async def cmd_status(device: LocationDevice, log_path: Path) -> int:
    say(f"Ghost {__version__} - status")
    say()
    status = await device.status()
    print_status(status)
    await device.close()
    if status.problem is not None:
        log.info("Status: probleem %s (%s)", status.problem.value, status.detail)
        print_problem(status.problem, status.detail, log_path)
        return EXIT_PROBLEM
    say()
    say("Alles in orde. Je kunt een locatie zetten met:")
    say(f"  {RUN} set 52.3676 4.9041")
    return EXIT_OK


async def cmd_pair(device: LocationDevice) -> int:
    say("Kijk op je iPhone: tik op 'Vertrouw' en voer je toegangscode in.")
    say("Ik wacht maximaal 60 seconden...")
    await device.pair(timeout=60.0)
    say("Gelukt: de iPhone vertrouwt deze laptop.")
    return EXIT_OK


async def cmd_reveal(device: LocationDevice) -> int:
    await device.reveal_developer_mode()
    say("De schakelaar is nu zichtbaar op je iPhone. Zet hem zelf aan:")
    say("  1. Instellingen > Privacy en beveiliging > Developer Mode (helemaal onderaan).")
    say("  2. Zet de schakelaar aan. De iPhone herstart.")
    say("  3. Ontgrendel de iPhone en bevestig met 'Schakel in'.")
    say("Je hoeft je toegangscode hiervoor NIET te verwijderen.")
    return EXIT_OK


async def cmd_mount(device: LocationDevice) -> int:
    say("Ontwikkelaars-image (DDI) op de iPhone zetten...")
    say("Hierbij maakt je laptop contact met GitHub (download) en Apple (ondertekening).")
    await device.mount_ddi()
    await device.close()
    say("Klaar. Controleer nu de status:")
    say(f"  {RUN} status")
    return EXIT_OK


async def cmd_set(device: LocationDevice, args: argparse.Namespace, log_path: Path, debug: bool) -> int:
    try:
        point = parse_latlon(" ".join(args.coords))
    except CoordinateError as error:
        say(str(error))
        return EXIT_USAGE

    status = await device.status()
    if status.problem is not None:
        print_status(status)
        print_problem(status.problem, status.detail, log_path)
        await device.close()
        return EXIT_PROBLEM

    attempted = False
    try:
        attempted = True
        await device.set_location(point)
        log.info("Nep-locatie gezet: %s", format_for_log(point, precise=debug))
        say(f"NEP-LOCATIE ACTIEF: {format_for_screen(point)}")
        say("  Je iPhone geeft deze locatie nu door aan alle apps.")
        say("  Controleer het in Apple Kaarten.")
        if args.seconds is not None:
            unit = "seconde" if args.seconds == 1 else "seconden"
            say(f"Stopt vanzelf na {args.seconds:g} {unit} (of eerder met Ctrl+C).")
            await asyncio.sleep(args.seconds)
        else:
            say("Druk op Enter om te stoppen en je echte locatie terug te vragen.")
            await wait_for_enter()
    finally:
        # Dit blok draait ALTIJD: na Enter, na Ctrl+C en na een fout.
        if attempted:
            if args.no_clear:
                log.warning("Afgesloten ZONDER de echte locatie terug te vragen (--no-clear)")
                say()
                say("LET OP: de echte locatie is NIET teruggevraagd (test met --no-clear).")
                say("Kijk wat je iPhone nu doet. Zet daarna terug met:")
                say(f"  {RUN} clear")
            else:
                await restore(device, log_path)
        await device.close()
    return EXIT_OK


async def cmd_clear(device: LocationDevice, log_path: Path) -> int:
    status = await device.status()
    if status.problem is not None:
        print_status(status)
        print_problem(status.problem, status.detail, log_path)
        await device.close()
        return EXIT_PROBLEM
    ok = await restore(device, log_path)
    await device.close()
    return EXIT_OK if ok else EXIT_PROBLEM


async def restore(device: LocationDevice, log_path: Path) -> bool:
    """Vraag de echte locatie terug en vertel eerlijk wat we wel en niet weten.

    Eerst doen, dan pas praten: het verzoek gaat de deur uit voordat er iets
    op het scherm komt.
    """
    try:
        confirmed = await device.clear_location()
    except DeviceError as error:
        log.error("Echte locatie terugvragen mislukt: %s", error)
        say()
        say("LET OP: de echte locatie kon NIET worden teruggevraagd.")
        say("Je iPhone staat mogelijk nog op de nep-locatie.")
        print_problem(Problem.CONNECTION_LOST, error.detail, log_path)
        return False
    log.info("Echte locatie teruggevraagd (aankomst bevestigd: %s)", confirmed)
    say()
    if confirmed:
        say("Echte locatie teruggevraagd. Het verzoek is bij de iPhone aangekomen.")
    else:
        say("Echte locatie teruggevraagd. De iPhone heeft de ontvangst niet bevestigd.")
    say("Controleer in Apple Kaarten of je echte locatie weer klopt.")
    say()
    say(DEVELOPER_MODE_REMINDER)
    return True


# ----------------------------------------------------------------------
# Opstarten
# ----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ghost",
        description="Ghost - zichtbare, tijdelijke locatiesimulatie voor je eigen iPhone.",
    )
    parser.add_argument("--version", action="version", version=f"Ghost {__version__}")
    parser.add_argument("--demo", action="store_true", help="gebruik een nep-iPhone (er is geen toestel nodig)")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="log alles, inclusief exacte coördinaten (lees het log na voordat je het deelt)",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="commando")

    commands.add_parser("status", help="controleer of alles klaar is voor gebruik")
    commands.add_parser("pair", help="laat de iPhone deze laptop vertrouwen")
    commands.add_parser("reveal-developer-mode", help="maak de schakelaar Developer Mode zichtbaar op de iPhone")
    commands.add_parser("mount", help="zet Apple's ontwikkelaars-image op de iPhone (alleen als status erom vraagt)")

    set_parser = commands.add_parser("set", help="zet een nep-locatie, bijvoorbeeld: set 52.3676 4.9041")
    set_parser.add_argument("coords", nargs="+", metavar="COORDINAAT", help="breedtegraad en lengtegraad")
    set_parser.add_argument("--seconds", type=float, metavar="N", help="stop vanzelf na N seconden")
    set_parser.add_argument(
        "--no-clear",
        action="store_true",
        help="ALLEEN OM TE TESTEN: sluit af zonder de echte locatie terug te vragen",
    )

    commands.add_parser("clear", help="vraag de echte locatie terug")
    commands.add_parser("diagnose", help="maak een rapport om een probleem te laten uitzoeken (zie diagnose.bat)")

    web_parser = commands.add_parser("web", help="start de webpagina in je browser (de gewone manier)")
    web_parser.add_argument(
        "--port", type=_port, default=8765, metavar="N", help="poort op je eigen laptop (standaard 8765)"
    )
    web_parser.add_argument("--no-browser", action="store_true", help="open de browser niet vanzelf")
    # Alleen voor ontwikkelaars, samen met --demo: laat de nep-iPhone een probleem nabootsen.
    web_parser.add_argument("--demo-problem", choices=[p.value for p in Problem], help=argparse.SUPPRESS)
    return parser


def _port(text: str) -> int:
    try:
        port = int(text)
    except ValueError:
        port = 0
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("kies een poort tussen 1024 en 65535")
    return port


def make_device(demo: bool, demo_problem: str | None = None) -> LocationDevice:
    if demo:
        from ghost.device.fake import FakeDevice

        return FakeDevice(problem=Problem(demo_problem) if demo_problem else None)
    # Pas hier importeren: het laden van pymobiledevice3 duurt even.
    from ghost.device.iphone import IPhone

    return IPhone()


async def run(args: argparse.Namespace, device: LocationDevice, log_path: Path) -> int:
    ticker = asyncio.create_task(_keep_responsive()) if sys.platform == "win32" else None
    try:
        match args.command:
            case "status":
                return await cmd_status(device, log_path)
            case "pair":
                return await cmd_pair(device)
            case "reveal-developer-mode":
                return await cmd_reveal(device)
            case "mount":
                return await cmd_mount(device)
            case "set":
                return await cmd_set(device, args, log_path, args.debug)
            case "clear":
                return await cmd_clear(device, log_path)
            case "diagnose":
                from ghost.diagnose import run_diagnose

                await run_diagnose(device, log_path, say)
                return EXIT_OK
        return EXIT_USAGE
    except DeviceError as error:
        log.error("Commando %s mislukt: %s", args.command, error)
        print_problem(error.problem, error.detail, log_path)
        await device.close()
        return EXIT_PROBLEM
    finally:
        if ticker is not None:
            ticker.cancel()


def main(argv: list[str] | None = None, device: LocationDevice | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")  # nooit crashen op een teken dat de terminal niet kent

    args = build_parser().parse_args(argv)
    log_path = setup_logging(debug=args.debug)
    log.info("Ghost %s gestart: commando=%s demo=%s", __version__, args.command, args.demo)
    if args.demo:
        say("[DEMO] Er wordt een nep-iPhone gebruikt. Er gebeurt niets met een echt toestel.")
    if device is None:
        device = make_device(args.demo, getattr(args, "demo_problem", None))

    if args.command == "web":
        # Pas hier importeren: de webserver laden kost tijd die de losse commando's niet nodig hebben.
        from ghost.web.server import run_web

        return run_web(
            device=device,
            demo=args.demo,
            debug=args.debug,
            port=args.port,
            browser=not args.no_browser,
            log_path=log_path,
        )

    coroutine = run(args, device, log_path)
    try:
        if sys.platform == "win32":
            # pymobiledevice3 heeft op Windows dit type "event loop" nodig.
            return asyncio.run(coroutine, loop_factory=asyncio.SelectorEventLoop)
        return asyncio.run(coroutine)
    except KeyboardInterrupt:
        say()
        say("Gestopt met Ctrl+C.")
        return EXIT_INTERRUPTED
    except Exception:
        log.exception("Onverwachte fout")
        say()
        say("Er ging iets onverwachts mis. Technische details staan in:")
        say(f"  {log_path}")
        return EXIT_PROBLEM
