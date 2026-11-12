"""De afspraak waar elk toestel (echt of nep) aan voldoet."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from ghost.geo import LatLon

#: Oudste iOS-versie die Ghost ondersteunt (tunnel zonder administratorrechten).
MIN_IOS = (17, 4)


class Problem(Enum):
    """Alles wat er mis kan zijn, in de volgorde waarin het wordt gecontroleerd."""

    APPLE_SERVICE_MISSING = "apple_service_missing"
    NO_DEVICE = "no_device"
    DEVICE_LOCKED = "device_locked"
    NOT_TRUSTED = "not_trusted"
    IOS_TOO_OLD = "ios_too_old"
    DEVELOPER_MODE_OFF = "developer_mode_off"
    TUNNEL_FAILED = "tunnel_failed"
    LOCATION_SERVICE_UNAVAILABLE = "location_service_unavailable"
    CONNECTION_LOST = "connection_lost"
    UNKNOWN = "unknown"


class DeviceError(Exception):
    """Een actie op het toestel is mislukt. `problem` zegt waarom; `detail` is technische info voor het log."""

    def __init__(self, problem: Problem, detail: str = "") -> None:
        super().__init__(f"{problem.value}: {detail}" if detail else problem.value)
        self.problem = problem
        self.detail = detail


@dataclass
class DeviceStatus:
    """De stand van zaken. `None` betekent: nog niet gecontroleerd (een eerdere stap ging mis)."""

    apple_service: bool | None = None
    connected: bool | None = None
    device_count: int = 0  #: aantal iPhones aan de kabel; Ghost gebruikt de eerste
    name: str | None = None
    model: str | None = None
    ios_version: str | None = None
    trusted: bool | None = None
    ios_supported: bool | None = None
    developer_mode: bool | None = None
    ddi_mounted: bool | None = None
    tunnel: bool | None = None
    location_service: bool | None = None
    problem: Problem | None = None
    detail: str = ""

    @property
    def ready(self) -> bool:
        """True als een locatie zetten nu zou moeten werken."""
        return self.problem is None and self.location_service is True


def ios_supported(version: str | None) -> bool:
    """Is deze iOS-versie 17.4 of hoger?"""
    numbers = [int(n) for n in re.findall(r"\d+", version or "")[:2]]
    if not numbers:
        return False
    major = numbers[0]
    minor = numbers[1] if len(numbers) > 1 else 0
    return (major, minor) >= MIN_IOS


class LocationDevice(ABC):
    """Een toestel waarvan Ghost de locatie kan simuleren."""

    @abstractmethod
    async def status(self) -> DeviceStatus:
        """Controleer alles, van USB-service tot locatiedienst. Gooit zelf geen fouten."""

    @abstractmethod
    async def pair(self, timeout: float = 60.0) -> None:
        """Laat de iPhone "Vertrouw je deze computer?" tonen en wacht op het antwoord."""

    @abstractmethod
    async def reveal_developer_mode(self) -> None:
        """Maak de schakelaar Developer Mode zichtbaar in Instellingen (zet hem NIET aan)."""

    @abstractmethod
    async def mount_ddi(self) -> None:
        """Zet het ontwikkelaars-image (DDI) op de iPhone. Maakt verbinding met GitHub en Apple."""

    @abstractmethod
    async def connect(self) -> None:
        """Bouw de tunnel en open de locatiedienst."""

    @abstractmethod
    async def set_location(self, point: LatLon) -> None:
        """Zet de nep-locatie. Wacht op bevestiging van het toestel."""

    @abstractmethod
    async def clear_location(self) -> bool:
        """Vraag het toestel de echte locatie terug te zetten.

        Geeft True terug als is bevestigd dat het verzoek bij het toestel is
        AANGEKOMEN. Het toestel bevestigt nooit dat de echte locatie terug is;
        dat controleer je zelf (bijvoorbeeld in Apple Kaarten).
        """

    @abstractmethod
    async def close(self) -> None:
        """Sluit de verbinding ZONDER de locatie terug te zetten."""
