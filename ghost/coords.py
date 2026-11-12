"""Coördinaten lezen, controleren en veilig weergeven."""

from __future__ import annotations

import math
import re

from ghost.geo import LatLon

_EXAMPLE = "52.3676, 4.9041"

# Getal met een punt als decimaalteken, bv. -33.8688
_DOT = r"[+-]?\d+(?:\.\d+)?"
# Getal met een komma als decimaalteken, bv. 52,3676
_COMMA = r"[+-]?\d+(?:,\d+)?"

_PATTERNS = [
    # "52.3676, 4.9041"  "52.3676 4.9041"  "52.3676;4.9041"
    re.compile(rf"^({_DOT})\s*[,;\s]\s*({_DOT})$"),
    # "52,3676 4,9041"  "52,3676; 4,9041"
    re.compile(rf"^({_COMMA})\s*[;\s]\s*({_COMMA})$"),
    # "52,3676, 4,9041"
    re.compile(r"^([+-]?\d+,\d+)\s*,\s*([+-]?\d+,\d+)$"),
]


class CoordinateError(ValueError):
    """Ongeldige coördinaten. De melding is Nederlands en bedoeld voor de gebruiker."""


def validate(lat: float, lon: float) -> LatLon:
    """Controleer het bereik: breedtegraad -90..90, lengtegraad -180..180."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        raise CoordinateError(f"Coördinaten moeten getallen zijn, bijvoorbeeld: {_EXAMPLE}") from None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        raise CoordinateError(f"Coördinaten moeten gewone getallen zijn, bijvoorbeeld: {_EXAMPLE}")
    if not -90 <= lat <= 90:
        hint = ""
        if -90 <= lon <= 90 and -180 <= lat <= 180:
            hint = " Heb je breedte- en lengtegraad omgedraaid?"
        raise CoordinateError(f"De breedtegraad moet tussen -90 en 90 liggen (kreeg {lat:g}).{hint}")
    if not -180 <= lon <= 180:
        raise CoordinateError(f"De lengtegraad moet tussen -180 en 180 liggen (kreeg {lon:g}).")
    return LatLon(lat, lon)


def parse_latlon(text: str) -> LatLon:
    """Lees coördinaten uit tekst zoals "52.3676, 4.9041".

    Geaccepteerd: breedtegraad eerst, dan lengtegraad; gescheiden door een
    komma, puntkomma of spatie; met punt of komma als decimaalteken.
    """
    cleaned = text.strip().replace("°", "").replace("−", "-")
    for pattern in _PATTERNS:
        match = pattern.match(cleaned)
        if match:
            lat, lon = (float(part.replace(",", ".")) for part in match.groups())
            return validate(lat, lon)
    raise CoordinateError(f"Ik begrijp deze coördinaten niet: {text!r}. Typ bijvoorbeeld: {_EXAMPLE}")


def format_for_log(point: LatLon, *, precise: bool = False) -> str:
    """Tekst voor het logbestand.

    Standaard afgerond op 2 decimalen (ongeveer 1 km), zodat een logbestand dat
    je deelt niet verraadt waar je precies was. Alleen in debugmodus exact.
    """
    if precise:
        return f"{point.lat:.6f}, {point.lon:.6f}"
    return f"{point.lat:.2f}, {point.lon:.2f}"


def format_for_screen(point: LatLon) -> str:
    """Tekst voor op het scherm van de gebruiker zelf (exact)."""
    return f"{point.lat:.5f}, {point.lon:.5f}"
