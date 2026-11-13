"""Routes en adressen van OpenStreetMap-diensten.

Twee gratis diensten, elk met eigen spelregels. Ghost houdt zich daaraan:

1. Routes over de weg: routing.openstreetmap.de (van de vereniging FOSSGIS).
   Regels: hooguit 1 verzoek per seconde, een herkenbare naam meesturen, de
   bron vermelden, geen zwaar gebruik.

2. Adres zoeken: nominatim.openstreetmap.org (van de OpenStreetMap Foundation).
   Beleid: https://operations.osmfoundation.org/policies/nominatim/
   Regels: hooguit 1 verzoek per seconde, een herkenbare naam meesturen, de
   bron vermelden, NIET automatisch aanvullen terwijl je typt, geen reeksen
   van zoekopdrachten, en resultaten bewaren zodat dezelfde vraag niet twee
   keer gesteld wordt. Het beleid zegt ook dat de maker van de app hier
   bewust voor moet kiezen. Daarom staat adres zoeken standaard UIT.
"""

from __future__ import annotations

import logging
import math
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from ghost.coords import CoordinateError, validate
from ghost.geo import LatLon, path_length_m
from ghost.net import NetError, Throttle, fetch_json

log = logging.getLogger(__name__)

ROUTING_BASE = "https://routing.openstreetmap.de"
NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"
NOMINATIM_POLICY = "https://operations.osmfoundation.org/policies/nominatim/"

#: Vervoer -> deel van het adres van de routeserver.
PROFILES = {"foot": "routed-foot", "bike": "routed-bike", "car": "routed-car"}

MAX_WAYPOINTS = 25
#: Langere routes over de weg vragen we niet aan de gratis server.
MAX_ROAD_ROUTE_KM = 500
MAX_QUERY_LENGTH = 200
MAX_RESULTS = 5

Fetch = Callable[[str], Awaitable[Any]]


class OsmError(Exception):
    """Een dienst van OpenStreetMap gaf geen bruikbaar antwoord. `message` is voor de gebruiker."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class RoadRoute:
    points: list[LatLon]
    distance_m: float


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float


class RoutingClient:
    def __init__(self, fetch: Fetch = fetch_json, *, min_interval: float = 1.0) -> None:
        self._fetch = fetch
        self._throttle = Throttle(min_interval)

    async def route(self, waypoints: Sequence[LatLon], profile: str) -> RoadRoute:
        """Vraag een route over de weg langs deze punten."""
        if profile not in PROFILES:
            raise OsmError("Onbekend vervoer.")
        if not 2 <= len(waypoints) <= MAX_WAYPOINTS:
            raise OsmError(f"Een route heeft 2 tot {MAX_WAYPOINTS} punten nodig.")
        if path_length_m(waypoints) > MAX_ROAD_ROUTE_KM * 1000:
            raise OsmError(
                f"Deze route is te lang voor de gratis routeserver (meer dan {MAX_ROAD_ROUTE_KM} km). "
                "Kies 'Rechte lijn' of zet de punten dichter bij elkaar."
            )

        # Let op de volgorde: de server wil lengtegraad,breedtegraad.
        coordinates = ";".join(f"{p.lon:.6f},{p.lat:.6f}" for p in waypoints)
        url = f"{ROUTING_BASE}/{PROFILES[profile]}/route/v1/driving/{coordinates}?overview=full&geometries=geojson"
        try:
            async with self._throttle:
                data = await self._fetch(url)
        except NetError as error:
            data = error.data if isinstance(error.data, dict) else None
            if data is None:
                log.warning("Routeserver niet bereikbaar: %s", error.detail)
                raise OsmError(
                    "De routeserver is niet bereikbaar. Controleer je internet, of kies 'Rechte lijn'."
                ) from error

        code = data.get("code") if isinstance(data, dict) else None
        if code == "NoRoute":
            raise OsmError("Tussen deze punten is geen route over de weg gevonden. Kies 'Rechte lijn'.")
        if code == "NoSegment":
            raise OsmError("Een van de punten ligt te ver van een weg. Zet het dichter bij een weg, of kies 'Rechte lijn'.")
        try:
            if code != "Ok":
                raise ValueError(f"code={code!r}")
            best = data["routes"][0]
            points = [validate(lat, lon) for lon, lat in best["geometry"]["coordinates"]]
            distance = float(best["distance"])
            if len(points) < 2 or not math.isfinite(distance):
                raise ValueError("te weinig punten")
        except (KeyError, IndexError, TypeError, ValueError, CoordinateError) as error:
            log.warning("Onbruikbaar antwoord van de routeserver: %r", error)
            raise OsmError("De routeserver gaf een onbruikbaar antwoord. Probeer het opnieuw, of kies 'Rechte lijn'.") from error
        return RoadRoute(points, distance)


class SearchClient:
    def __init__(self, fetch: Fetch = fetch_json, *, min_interval: float = 1.0, cache_size: int = 50) -> None:
        self._fetch = fetch
        self._throttle = Throttle(min_interval)
        self._cache: OrderedDict[str, list[Place]] = OrderedDict()
        self._cache_size = cache_size

    async def search(self, query: str) -> list[Place]:
        """Zoek een adres of plaatsnaam. Alleen aanroepen als de gebruiker zelf op 'Zoek' klikt."""
        query = " ".join(query.split())
        if not query:
            raise OsmError("Typ eerst een adres of plaatsnaam.")
        if len(query) > MAX_QUERY_LENGTH:
            raise OsmError("Deze zoektekst is te lang.")
        key = query.casefold()
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        url = f"{NOMINATIM_SEARCH}?{urlencode({'q': query, 'format': 'jsonv2', 'limit': MAX_RESULTS, 'accept-language': 'nl'})}"
        try:
            async with self._throttle:
                data = await self._fetch(url)
        except NetError as error:
            log.warning("Adres zoeken mislukt: %s", error.detail)
            if error.status in (403, 429):
                raise OsmError(
                    "De zoekdienst weigert op dit moment verzoeken. Wacht even en probeer het later opnieuw."
                ) from error
            raise OsmError("De zoekdienst is niet bereikbaar. Controleer je internet.") from error

        places: list[Place] = []
        if isinstance(data, list):
            for item in data[:MAX_RESULTS]:
                try:
                    point = validate(float(item["lat"]), float(item["lon"]))
                    name = str(item["display_name"])[:200]
                except (KeyError, TypeError, ValueError, CoordinateError):
                    continue
                places.append(Place(name, point.lat, point.lon))
        self._cache[key] = places
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return places
