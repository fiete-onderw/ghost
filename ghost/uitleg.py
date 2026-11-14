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
    stappen: tuple[str, ...]  #: voor de terminal: met commando's om te plakken
    #: Voor de webpagina: dezelfde stappen, maar met een knop in plaats van een
    #: commando. Leeg betekent: de stappen voor de terminal passen ook hier.
    web_stappen: tuple[str, ...] = ()
    #: Welke knop de webpagina erbij toont: "pair", "reveal" of "mount".
    actie: str | None = None
    actie_tekst: str = ""
    #: Een webadres dat erbij hoort (bijvoorbeeld de download van iTunes).
    link: str | None = None
    link_tekst: str = ""

    def stappen_voor_web(self) -> tuple[str, ...]:
        return self.web_stappen or self.stappen


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
            web_stappen=(
                "Installeer iTunes van de website van Apple (knop hieronder).",
                "Gebruik niet de versie uit de Microsoft Store en niet de app 'Apple Devices'.",
                "Herstart daarna je laptop en start Ghost opnieuw.",
            ),
            link=ITUNES_URL,
            link_tekst="iTunes downloaden bij Apple",
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
            "Ontgrendel de iPhone. Vraagt hij 'Vertrouw je deze computer?', tik dan op 'Vertrouw'.",
            "Steek de kabel rechtstreeks in de laptop, niet in een USB-hub of beeldscherm.",
            "Probeer een andere USB-poort of kabel.",
            "Ziet Windows de iPhone wel (bijvoorbeeld in Verkenner) maar Ghost niet? Open iTunes. "
            "Ziet iTunes hem ook niet, herstart dan je laptop met de iPhone aangesloten.",
        ),
    ),
    Problem.NOT_LISTED: Uitleg(
        "Windows ziet je iPhone, maar de Apple-service geeft hem niet door",
        "De kabel is in orde. Het onderdeel van iTunes dat met de iPhone praat, herkent hem (nog) niet.",
        (
            "Ontgrendel de iPhone. Vraagt hij 'Vertrouw je deze computer?', tik dan op 'Vertrouw'.",
            "Haal de kabel eruit, wacht 5 seconden en steek hem er weer in.",
            "Open iTunes en kijk of je iPhone daar verschijnt (klein telefoon-icoon linksboven).",
            "Ziet iTunes hem ook niet? Herstart je laptop met de iPhone aangesloten.",
            f"Helpt dat niet? Installeer iTunes opnieuw van de website van Apple: {ITUNES_URL} "
            "(niet de versie uit de Microsoft Store).",
        ),
    ),
    Problem.DEVICE_LOCKED: Uitleg(
        "De iPhone is vergrendeld",
        "De iPhone geeft pas toegang als hij ontgrendeld is.",
        ("Ontgrendel de iPhone, laat het scherm aan en probeer het opnieuw.",),
        web_stappen=("Ontgrendel de iPhone en laat het scherm aan. Ghost probeert het vanzelf opnieuw.",),
    ),
    Problem.NOT_TRUSTED: Uitleg(
        "De iPhone vertrouwt deze laptop nog niet",
        "De iPhone moet eerst toestemming geven aan deze computer.",
        (
            f"Voer uit:  {RUN} pair",
            "Tik op de iPhone op 'Vertrouw' en voer je toegangscode in.",
        ),
        web_stappen=(
            "Ontgrendel de iPhone en laat het scherm aan.",
            "Klik hieronder op 'Koppel de iPhone'.",
            "Tik op de iPhone op 'Vertrouw' en voer je toegangscode in. Je hebt daar 60 seconden voor.",
        ),
        actie="pair",
        actie_tekst="Koppel de iPhone",
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
        web_stappen=(
            "Klik hieronder op 'Maak de schakelaar zichtbaar'.",
            "Op de iPhone: Instellingen > Privacy en beveiliging > Developer Mode (helemaal onderaan) > aan.",
            "De iPhone herstart. Ontgrendel hem daarna en bevestig met 'Schakel in'.",
            "Je hoeft je toegangscode hiervoor NIET te verwijderen.",
        ),
        actie="reveal",
        actie_tekst="Maak de schakelaar zichtbaar",
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
        web_stappen=(
            "Ontgrendel de iPhone en laat het scherm aan.",
            "Haal de kabel eruit en steek hem er weer in. Ghost probeert het vanzelf opnieuw.",
            "Sluit andere vensters waarin Ghost of pymobiledevice3 draait.",
            "Blijft het misgaan? Deel dan het logbestand (pad staat onderaan).",
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
        web_stappen=(
            "Klik hieronder op 'Zet het ontwikkelaars-image op de iPhone'.",
            "Daarbij maakt je laptop contact met GitHub (download) en Apple (ondertekening).",
            "Ghost controleert daarna vanzelf opnieuw.",
        ),
        actie="mount",
        actie_tekst="Zet het ontwikkelaars-image op de iPhone",
    ),
    Problem.CONNECTION_LOST: Uitleg(
        "De verbinding met de iPhone is weggevallen",
        "Het is niet zeker of de opdracht is aangekomen.",
        (
            "Controleer de kabel en ontgrendel de iPhone. Steek de kabel rechtstreeks in de laptop.",
            f"Voer uit:  {RUN} clear   om de echte locatie terug te vragen.",
            "Kijk in Apple Kaarten of je echte locatie weer klopt.",
        ),
        web_stappen=(
            "Controleer de kabel en ontgrendel de iPhone. Steek de kabel rechtstreeks in de laptop.",
            "Klik op de rode knop 'Stop - echte locatie terug'.",
            "Kijk in Apple Kaarten of je echte locatie weer klopt.",
        ),
    ),
    Problem.UNKNOWN: Uitleg(
        "Er ging iets onverwachts mis",
        "Ghost herkent deze fout niet.",
        ("Deel het logbestand (pad staat hieronder), dan kan het worden uitgezocht.",),
        web_stappen=(
            "Klik op 'Opnieuw controleren'.",
            "Blijft het misgaan? Deel dan het logbestand (pad staat onderaan), dan kan het worden uitgezocht.",
        ),
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
