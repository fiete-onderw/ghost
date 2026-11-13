"""Routewiskunde.

Alles in dit bestand is "pure" rekenlogica: er zit geen iPhone-, netwerk- of
klokcode in. Daardoor kun je elke functie testen met gewone getallen (zie
tests/test_geo.py).

Afstanden zijn in meters, snelheden in km/u, tijden in seconden.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple

#: Gemiddelde straal van de aarde in meters.
EARTH_RADIUS_M = 6_371_008.8

SPEED_MIN_KMH = 1.0
SPEED_MAX_KMH = 120.0
SPEED_PRESETS_KMH = {"lopen": 5.0, "fietsen": 15.0, "auto": 40.0}


class LatLon(NamedTuple):
    """Een punt op aarde: breedtegraad (lat) en lengtegraad (lon), in graden."""

    lat: float
    lon: float


def haversine_m(a: LatLon, b: LatLon) -> float:
    """Afstand in meters tussen twee punten, hemelsbreed over de bol (haversine-formule)."""
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlon = math.radians(b.lon - a.lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def intermediate_point(a: LatLon, b: LatLon, fraction: float) -> LatLon:
    """Het punt op `fraction` (0..1) van de kortste weg van a naar b.

    Dit volgt de bolvorm van de aarde, zodat het ook klopt bij lange stukken
    en bij het oversteken van de datumgrens (lengtegraad 180).
    """
    fraction = min(1.0, max(0.0, fraction))
    lat1, lon1 = math.radians(a.lat), math.radians(a.lon)
    lat2, lon2 = math.radians(b.lat), math.radians(b.lon)
    angle = haversine_m(a, b) / EARTH_RADIUS_M  # hoek tussen de punten, in radialen
    sin_angle = math.sin(angle)
    if sin_angle < 1e-12:
        # Punten vallen samen (of liggen exact tegenover elkaar): geen richting te bepalen.
        return a if fraction < 0.5 else b
    wa = math.sin((1 - fraction) * angle) / sin_angle
    wb = math.sin(fraction * angle) / sin_angle
    x = wa * math.cos(lat1) * math.cos(lon1) + wb * math.cos(lat2) * math.cos(lon2)
    y = wa * math.cos(lat1) * math.sin(lon1) + wb * math.cos(lat2) * math.sin(lon2)
    z = wa * math.sin(lat1) + wb * math.sin(lat2)
    return LatLon(math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x)))


def destination_point(start: LatLon, bearing_deg: float, distance_m: float) -> LatLon:
    """Het punt dat je bereikt als je vanaf `start` `distance_m` meter in een richting gaat.

    De richting is in graden: 0 = noord, 90 = oost, 180 = zuid, 270 = west.
    """
    lat1, lon1 = math.radians(start.lat), math.radians(start.lon)
    bearing = math.radians(bearing_deg)
    angle = distance_m / EARTH_RADIUS_M  # afgelegde hoek over de bol
    lat2 = math.asin(math.sin(lat1) * math.cos(angle) + math.cos(lat1) * math.sin(angle) * math.cos(bearing))
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angle) * math.cos(lat1),
        math.cos(angle) - math.sin(lat1) * math.sin(lat2),
    )
    lon_deg = (math.degrees(lon2) + 540) % 360 - 180  # terug naar -180..180
    return LatLon(math.degrees(lat2), lon_deg)


def path_length_m(points: Sequence[LatLon]) -> float:
    """Totale lengte in meters van een lijn langs deze punten."""
    return sum(haversine_m(a, b) for a, b in zip(points, points[1:]))


def clamp_speed_kmh(speed_kmh: float) -> float:
    """Begrens een snelheid op het toegestane bereik (1-120 km/u)."""
    if not math.isfinite(speed_kmh):
        raise ValueError("Snelheid moet een gewoon getal zijn.")
    return min(SPEED_MAX_KMH, max(SPEED_MIN_KMH, float(speed_kmh)))


def distance_after(speed_kmh: float, seconds: float) -> float:
    """Hoeveel meter je aflegt in `seconds` seconden met `speed_kmh` km/u."""
    return speed_kmh / 3.6 * seconds


def eta_seconds(remaining_m: float, speed_kmh: float) -> float:
    """Verwachte resterende tijd in seconden."""
    if speed_kmh <= 0:
        return math.inf
    return max(0.0, remaining_m) / (speed_kmh / 3.6)


@dataclass(frozen=True)
class RoutePosition:
    """Waar je bent op een route na een bepaalde afgelegde afstand."""

    point: LatLon
    travelled_m: float  #: afgelegd binnen de huidige ronde
    remaining_m: float  #: nog te gaan tot het einde van de ronde
    lap: int  #: rondenummer, beginnend bij 0
    finished: bool  #: True als de route klaar is (nooit bij `repeat`)


class Route:
    """Een route langs een reeks punten.

    - `round_trip` (heen-en-terug): aan het eind loop je dezelfde weg terug.
    - `repeat` (herhalen): aan het eind begin je opnieuw. Zonder `round_trip`
      wordt de route een gesloten lus: vanaf het laatste punt loop je in een
      rechte lijn terug naar het eerste, zodat de positie nooit verspringt.

    Eén doorloop van het pad heet een "ronde".
    """

    def __init__(self, points: Sequence[LatLon], *, round_trip: bool = False, repeat: bool = False) -> None:
        if len(points) < 2:
            raise ValueError("Een route heeft minstens twee punten nodig.")
        path = [LatLon(float(p[0]), float(p[1])) for p in points]
        if round_trip:
            path = path + path[-2::-1]
        elif repeat and path[0] != path[-1]:
            path = path + [path[0]]
        self.repeat = repeat
        self.round_trip = round_trip
        self._path = path
        cumulative = [0.0]
        for start, end in zip(path, path[1:]):
            cumulative.append(cumulative[-1] + haversine_m(start, end))
        self._cumulative = cumulative

    @property
    def path(self) -> list[LatLon]:
        """De punten van één ronde, inclusief de terugweg of het sluitstuk."""
        return list(self._path)

    @property
    def length_m(self) -> float:
        """Lengte van één ronde in meters."""
        return self._cumulative[-1]

    def position_at(self, distance_m: float) -> RoutePosition:
        """De positie nadat je in totaal `distance_m` meter hebt afgelegd."""
        length = self.length_m
        distance = max(0.0, distance_m)
        lap = 0
        if self.repeat and length > 0:
            lap = int(distance // length)
            distance = distance - lap * length
        elif distance >= length:
            return RoutePosition(self._path[-1], length, 0.0, 0, finished=not self.repeat)

        # Zoek het stuk (segment) waar deze afstand in valt.
        index = min(bisect_right(self._cumulative, distance) - 1, len(self._path) - 2)
        segment_start = self._cumulative[index]
        segment_length = self._cumulative[index + 1] - segment_start
        fraction = (distance - segment_start) / segment_length if segment_length > 0 else 0.0
        point = intermediate_point(self._path[index], self._path[index + 1], fraction)
        return RoutePosition(point, distance, length - distance, lap, finished=False)
