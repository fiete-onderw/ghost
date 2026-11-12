"""Nederlandse uitleg bij elk probleem: wat is er aan de hand en wat doe je eraan.

Dit staat los van de rest, zodat de terminal (nu) en de webpagina (later)
precies dezelfde teksten gebruiken.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from ghost.device.base import Problem

ITUNES_URL = "https://www.apple.com/itunes/download/win64"
RUN = "uv run python -m ghost"


@dataclass(frozen=True)
class Uitleg:
    titel: str
    wat: str
    stappen: tuple[str, ...]


def _apple_service() -> Uitleg:
    if sys.platform == "win32":
        return Uitleg(
            "Windows kan nog niet met iPhones praten",
            "De Apple-service die de USB-verbinding regelt ontbreekt of draait niet.",
            (
                f"Installeer iTunes van de website van Apple: {ITUNES_URL}",
                "Gebruik niet de versie uit de Microsoft Store en niet de app 'Apple Devices'.",
                "Herstart daarna je laptop en probeer het opnieuw.",
            ),
        )
    return Uitleg(
        "De USB-service voor iPhones draait niet",
        "Op dit systeem is usbmuxd niet bereikbaar.",
        ("Start usbmuxd (Linux) of sluit de iPhone aan (macOS) en probeer het opnieuw.",),
    )


_UITLEG: dict[Problem, Uitleg] = {
    Problem.NO_DEVICE: Uitleg(
        "Geen iPhone gevonden",
        "Er is geen iPhone via de kabel zichtbaar.",
        (
            "Sluit de iPhone aan met een USB-kabel die ook data doorgeeft (niet alleen opladen).",
            "Ontgrendel de iPhone.",
            "Probeer een andere USB-poort of kabel.",
        ),
    ),
    Problem.DEVICE_LOCKED: Uitleg(
        "De iPhone is vergrendeld",
        "De iPhone geeft pas toegang als hij ontgrendeld is.",
        ("Ontgrendel de iPhone, laat het scherm aan en probeer het opnieuw.",),
    ),
    Problem.NOT_TRUSTED: Uitleg(
        "De iPhone vertrouwt deze laptop nog niet",
        "De iPhone moet eerst toestemming geven aan deze computer.",
        (
            f"Voer uit:  {RUN} pair",
            "Tik op de iPhone op 'Vertrouw' en voer je toegangscode in.",
        ),
    ),
    Problem.IOS_TOO_OLD: Uitleg(
        "Deze iOS-versie wordt niet ondersteund",
        "Update je iPhone naar iOS 17.4 of hoger.",
        ("Op de iPhone: Instellingen > Algemeen > Software-update.",),
    ),
    Problem.DEVELOPER_MODE_OFF: Uitleg(
        "Developer Mode staat uit",
        "Locatiesimulatie is een ontwikkelaarsfunctie van Apple en werkt alleen met Developer Mode aan.",
        (
            f"Voer uit:  {RUN} reveal-developer-mode",
            "Op de iPhone: Instellingen > Privacy en beveiliging > Developer Mode (helemaal onderaan) > aan.",
            "De iPhone herstart. Ontgrendel hem daarna en bevestig met 'Schakel in'.",
            "Je hoeft je toegangscode hiervoor NIET te verwijderen.",
        ),
    ),
    Problem.TUNNEL_FAILED: Uitleg(
        "De beveiligde verbinding met de iPhone kwam niet tot stand",
        "Ghost kon geen tunnel naar de iPhone opzetten.",
        (
            "Ontgrendel de iPhone en laat het scherm aan.",
            "Haal de kabel eruit, steek hem er weer in en probeer het opnieuw.",
            "Sluit andere vensters waarin Ghost of pymobiledevice3 draait.",
            "Blijft het misgaan? Deel dan het logbestand (pad staat hieronder).",
        ),
    ),
    Problem.LOCATION_SERVICE_UNAVAILABLE: Uitleg(
        "De iPhone biedt de locatiedienst niet aan",
        "De ontwikkelaarsdienst voor locatiesimulatie is op dit moment niet bereikbaar.",
        (
            f"Voer uit:  {RUN} mount",
            "Dat zet Apple's ontwikkelaars-image op de iPhone. Daarbij wordt contact gemaakt met "
            "GitHub (download) en Apple (ondertekening).",
            f"Voer daarna opnieuw uit:  {RUN} status",
        ),
    ),
    Problem.CONNECTION_LOST: Uitleg(
        "De verbinding met de iPhone is weggevallen",
        "Het is niet zeker of de opdracht is aangekomen.",
        (
            "Controleer de kabel en ontgrendel de iPhone.",
            f"Voer uit:  {RUN} clear   om de echte locatie terug te vragen.",
            "Kijk in Apple Kaarten of je echte locatie weer klopt.",
        ),
    ),
    Problem.UNKNOWN: Uitleg(
        "Er ging iets onverwachts mis",
        "Ghost herkent deze fout niet.",
        ("Deel het logbestand (pad staat hieronder), dan kan het worden uitgezocht.",),
    ),
}


def uitleg(problem: Problem) -> Uitleg:
    if problem is Problem.APPLE_SERVICE_MISSING:
        return _apple_service()
    return _UITLEG[problem]


DEVELOPER_MODE_REMINDER = (
    "Gebruik je dit even niet? Zet Developer Mode uit:\n"
    "  Instellingen > Privacy en beveiliging > Developer Mode."
)
