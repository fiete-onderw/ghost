"""De deurwachter van de webserver (bouwstap 5).

Ghost draait een kleine webserver op je eigen laptop. Die kan je iPhone een
andere locatie geven, dus niet iedereen mag ermee praten. Het gevaar zit niet
in "het internet" (de server luistert alleen op 127.0.0.1, je eigen laptop),
maar in je eigen browser: elke website die je bezoekt, mag je browser vragen
om een verzoek naar 127.0.0.1 te sturen. Zonder deurwachter zou een kwaadwillende
pagina zo ongemerkt je locatie kunnen verzetten.

De deurwachter controleert daarom elk verzoek op drie dingen:

1. Host - "Aan wie is dit verzoek gericht?"
   Moet 127.0.0.1 of localhost zijn, met onze poort. Dit stopt een truc die
   "DNS-rebinding" heet: een website die zijn eigen naam (bv. evil.example)
   stiekem naar 127.0.0.1 laat wijzen om zich als onze pagina voor te doen.

2. Origin - "Vanaf welke website komt dit verzoek?"
   Stuurt de browser dit mee, dan moet het onze eigen pagina zijn. Dit stopt
   verzoeken die een andere website door jouw browser laat versturen.

3. Sessietoken - "Ken je het wachtwoord van deze sessie?"
   Bij elke start verzint Ghost een nieuw, lang, willekeurig wachtwoord. Het
   staat alleen in onze eigen pagina. Andere websites kunnen die pagina niet
   lezen, en dus het token niet meesturen. Het token staat nooit in een
   webadres (URL), zodat het niet in je browsergeschiedenis belandt of naar
   de kaartserver uitlekt.

En wat we bewust NIET doen: CORS aanzetten. CORS is de manier waarop een
server tegen de browser zegt: "andere websites mogen mijn antwoorden lezen".
Dat willen we juist niet, dus Ghost stuurt die toestemming nooit.

Eerlijk over de grens: dit beschermt tegen websites, niet tegen een programma
dat al op je laptop draait. Zo'n programma kan toch al alles wat jij kunt.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass

TOKEN_HEADER = "x-ghost-token"

#: Deze adressen hebben geen token nodig. /api/hello verklapt alleen dat hier
#: Ghost draait; dat gebruikt een tweede Ghost om te zien dat er al een loopt.
PUBLIC_API_PATHS = frozenset({"/api/hello"})

#: Extra regels die de browser moet volgen voor onze pagina.
SECURITY_HEADERS = {
    # Laad alleen onze eigen scripts en stijlen, en kaartplaatjes alleen van
    # OpenStreetMap. Niemand mag onze pagina in een kader (frame) op een andere
    # website zetten: zo kan niemand je laten klikken op een onzichtbare knop.
    "Content-Security-Policy": (
        "default-src 'none'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data: https://tile.openstreetmap.org; "
        "connect-src 'self'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    # Andere websites mogen onze bestanden (plaatjes, scripts) niet inladen.
    # Zo kunnen ze ook niet aftasten of Ghost op deze laptop draait.
    "Cross-Origin-Resource-Policy": "same-origin",
    # Geen strengere Referrer-Policy dan de standaard van de browser: de
    # kaartserver van OpenStreetMap wil weten dat de aanvraag van een pagina
    # komt. Hij krijgt alleen "http://127.0.0.1:poort/" te zien, nooit meer.
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # Altijd de nieuwste versie tonen, en de pagina met het token niet bewaren.
    "Cache-Control": "no-store",
}


@dataclass(frozen=True)
class Refusal:
    """Waarom een verzoek geweigerd is."""

    status: int
    message: str


def new_token() -> str:
    """Een nieuw sessietoken: 43 willekeurige tekens, niet te raden."""
    return secrets.token_urlsafe(32)


class LocalGuard:
    def __init__(self, *, port: int, token: str) -> None:
        self._token = token
        self.allowed_hosts = frozenset({f"127.0.0.1:{port}", f"localhost:{port}"})
        self.allowed_origins = frozenset({f"http://127.0.0.1:{port}", f"http://localhost:{port}"})

    def check(self, path: str, headers: Mapping[str, str]) -> Refusal | None:
        """Controleer één verzoek. Geeft None terug als het door mag.

        `headers` moet de namen in kleine letters hebben.
        """
        host = headers.get("host", "").strip().lower()
        if host not in self.allowed_hosts:
            return Refusal(403, "Geweigerd: dit verzoek is niet aan Ghost op deze laptop gericht (Host).")

        origin = headers.get("origin")
        if origin is not None and origin.strip().lower() not in self.allowed_origins:
            return Refusal(403, "Geweigerd: dit verzoek komt van een andere website (Origin).")

        if path.startswith("/api/") and path not in PUBLIC_API_PATHS:
            given = headers.get(TOKEN_HEADER, "")
            # compare_digest vergelijkt zo dat je aan de reactietijd niet kunt
            # aflezen hoeveel tekens er al goed waren.
            if not secrets.compare_digest(given.encode(), self._token.encode()):
                return Refusal(401, "Geweigerd: het sessietoken ontbreekt of klopt niet. Herlaad de pagina.")
        return None
