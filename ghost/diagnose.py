"""Diagnose: verzamelt in één tekstbestand wat nodig is om een probleem uit te zoeken.

Bedoeld voor als Ghost bij iemand niet (goed) werkt. Je dubbelklikt op
diagnose.bat en stuurt het bestand dat eruit komt naar de maker.

Wat er WEL in staat: versies (Ghost, Windows, de iPhone-library), of de
Apple-service draait, wat Windows aan de USB-poort ziet, het model en de
iOS-versie van de iPhone, de uitslag van elke controle, een korte test van
hoe stabiel de verbinding is, en de laatste regels van het logbestand.

Wat er NIET in staat: de naam van je iPhone, het serienummer, en exacte
coördinaten. Het logbestand bevat coördinaten afgerond op ongeveer 1 km
(behalve als je Ghost zelf met --debug hebt gestart).
"""

from __future__ import annotations

import asyncio
import logging
import platform
import re
import subprocess
import sys
import time
from importlib import metadata
from pathlib import Path

from ghost import __version__, hostusb, paths
from ghost.device.base import DeviceError, DeviceStatus, LocationDevice

log = logging.getLogger(__name__)

STABILITY_ROUNDS = 20  #: zoveel keer achter elkaar, één keer per seconde, net als bij een route
LOG_LINES = 200

# Serienummers (UDID's) van iPhones: 8 en 16 hextekens met een streepje, of 40 hextekens.
_SERIALS = re.compile(r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}\b|\b[0-9A-Fa-f]{40}\b")

_CHECKS = (
    ("apple_service", "Apple USB-service"),
    ("connected", "iPhone verbonden"),
    ("trusted", "iPhone vertrouwt deze laptop"),
    ("ios_supported", "iOS-versie ondersteund"),
    ("developer_mode", "Developer Mode aan"),
    ("tunnel", "Tunnel naar de iPhone"),
    ("location_service", "Locatiedienst bereikbaar"),
)


def report_file() -> Path:
    return paths.log_dir() / "ghost-diagnose.txt"


def redact(text: str) -> str:
    """Haal serienummers van iPhones uit een tekst."""
    return _SERIALS.sub("[serienummer verwijderd]", text)


def _mark(value: bool | None) -> str:
    return {True: "ja", False: "NEE", None: "niet gecontroleerd"}[value]


def _versions() -> list[str]:
    try:
        library = metadata.version("pymobiledevice3")
    except metadata.PackageNotFoundError:
        library = "onbekend"
    return [
        f"Ghost: {__version__}",
        f"Tijdstip: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Computer: {platform.platform()}",
        f"Python: {platform.python_version()}",
        f"pymobiledevice3: {library}",
    ]


def _apple_service() -> list[str]:
    if sys.platform != "win32":
        return ["(alleen op Windows)"]
    lines = []
    try:
        result = subprocess.run(  # noqa: S603 - vaste opdracht
            ["sc.exe", "query", "Apple Mobile Device Service"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        state = next((line.strip() for line in result.stdout.splitlines() if "STATE" in line), "")
        lines.append(f"Apple Mobile Device Service: {state or 'niet gevonden (iTunes van apple.com geinstalleerd?)'}")
    except Exception as error:
        lines.append(f"Apple Mobile Device Service: kon niet worden opgevraagd ({error!r})")
    classic = Path(r"C:\Program Files\Common Files\Apple\Mobile Device Support")
    lines.append(f"iTunes van apple.com geinstalleerd (map Mobile Device Support): {_mark(classic.exists())}")
    return lines


def _windows_usb() -> list[str]:
    seen = hostusb.apple_devices_seen_by_windows()
    if seen is None:
        return ["onbekend (kon het Windows niet vragen)"]
    return seen or ["Windows ziet geen iPhone of iPad op USB"]


def _status_lines(status: DeviceStatus) -> list[str]:
    lines = [f"Model: {status.model or 'onbekend'}", f"iOS: {status.ios_version or 'onbekend'}"]
    if status.device_count > 1:
        lines.append(f"Aantal iPhones aan de kabel: {status.device_count}")
    lines += [f"{label}: {_mark(getattr(status, field))}" for field, label in _CHECKS]
    lines.append(f"Ontwikkelaars-image gemount: {_mark(status.ddi_mounted)} (alleen ter informatie)")
    if status.problem is None:
        lines.append("Probleem: geen")
    else:
        lines.append(f"Probleem: {status.problem.value}")
        if status.detail:
            lines.append(f"Detail: {redact(status.detail)}")
    return lines


async def _stability(device: LocationDevice, rounds: int, pause: float) -> list[str]:
    """Stuur een reeks onschuldige verzoeken, één per seconde, en meet hoe het gaat.

    Het verzoek is "geef de echte locatie terug". Uit tests weten we dat dit
    geen kwaad kan als er niets actief is, en het is meteen een schone start.
    """
    times: list[float] = []
    failures: list[str] = []
    unconfirmed = 0
    before = getattr(device, "reconnects", 0)
    for number in range(1, rounds + 1):
        started = time.monotonic()
        try:
            confirmed = await device.clear_location()
        except DeviceError as error:
            failures.append(f"ronde {number}: {error.problem.value} {redact(error.detail)}")
        except Exception as error:  # een onverwachte fout hoort ook in het rapport
            failures.append(f"ronde {number}: {redact(repr(error))}")
        else:
            times.append(time.monotonic() - started)
            unconfirmed += 0 if confirmed else 1
        if number < rounds:
            await asyncio.sleep(pause)
    reconnects = getattr(device, "reconnects", 0) - before

    lines = [f"Verzoeken gelukt: {len(times)} van {rounds}"]
    if times:
        lines.append(
            f"Antwoordtijd: gemiddeld {sum(times) / len(times):.2f} s, snelste {min(times):.2f} s, "
            f"traagste {max(times):.2f} s"
        )
    lines.append(f"Zonder ontvangstbevestiging: {unconfirmed}")
    lines.append(f"Verbinding onderweg opnieuw opgebouwd: {reconnects} keer")
    lines += failures
    if not failures and not reconnects:
        lines.append("Oordeel: de verbinding bleef stabiel tijdens deze test.")
    else:
        lines.append("Oordeel: de verbinding is NIET stabiel. Probeer een andere kabel en een andere USB-poort.")
    return lines


def _log_tail(log_path: Path, count: int) -> list[str]:
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as error:
        return [f"(logbestand niet te lezen: {error!r})"]
    return [redact(line) for line in lines[-count:]]


async def build_report(
    device: LocationDevice, log_path: Path, *, rounds: int | None = None, pause: float = 1.0
) -> str:
    rounds = STABILITY_ROUNDS if rounds is None else rounds
    sections: list[tuple[str, list[str]]] = [
        ("Versies", _versions()),
        ("Apple-service", await asyncio.to_thread(_apple_service)),
        ("Wat Windows aan de USB-poort ziet", await asyncio.to_thread(_windows_usb)),
    ]
    try:
        status = await device.status()
    except Exception as error:
        log.exception("Status opvragen voor de diagnose mislukt")
        sections.append(("iPhone", [f"Status opvragen gaf een fout: {redact(repr(error))}"]))
        status = None
    if status is not None:
        sections.append(("iPhone", _status_lines(status)))
        if status.ready:
            sections.append((f"Stabiliteit van de verbinding ({rounds} verzoeken)", await _stability(device, rounds, pause)))
        else:
            sections.append(("Stabiliteit van de verbinding", ["Overgeslagen: de iPhone is niet klaar (zie hierboven)."]))
    try:
        await device.close()
    except Exception:
        log.info("Verbinding sluiten na de diagnose gaf een fout", exc_info=True)
    sections.append((f"Laatste {LOG_LINES} regels van het logbestand", _log_tail(log_path, LOG_LINES)))

    out = [
        "GHOST DIAGNOSE",
        "Dit bestand bevat geen naam of serienummer van je iPhone en geen exacte coordinaten.",
        "Lees het gerust door voordat je het deelt.",
    ]
    for title, lines in sections:
        out += ["", f"== {title} ==", *lines]
    return "\n".join(out) + "\n"


async def run_diagnose(device: LocationDevice, log_path: Path, say=print) -> Path:
    """Maak het rapport, schrijf het weg en geef het pad terug."""
    say("Ghost maakt een diagnose. Dit duurt ongeveer een halve minuut.")
    say("Sluit Ghost eerst als dat nog open staat, en laat de iPhone aangesloten en ontgrendeld.")
    say()
    report = await build_report(device, log_path)
    target = report_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report, encoding="utf-8")
    # Toon alles behalve het lange logstuk; dat staat in het bestand.
    say(report.split("== Laatste ")[0].rstrip())
    say()
    say("Het volledige rapport staat in:")
    say(f"  {target}")
    say("Stuur dat bestand naar degene die je helpt.")
    return target
