"""Een nep-iPhone, voor de demomodus (`--demo`) en de automatische tests.

Hij doet niets met een echt toestel: hij onthoudt alleen wat er gevraagd is.
"""

from __future__ import annotations

from ghost.device.base import DeviceError, DeviceStatus, LocationDevice, Problem, ios_supported
from ghost.geo import LatLon


class FakeDevice(LocationDevice):
    def __init__(
        self,
        *,
        problem: Problem | None = None,
        name: str = "Demo-iPhone",
        model: str = "iPhone (demo)",
        ios_version: str = "27.0",
    ) -> None:
        self.problem = problem  #: zet dit om een probleem na te bootsen
        self.name = name
        self.model = model
        self.ios_version = ios_version
        self.location: LatLon | None = None  #: de "nep-locatie" die nu actief is, of None
        self.connected = False
        self.history: list[tuple[str, LatLon | None]] = []  #: alles wat er gezet en gewist is
        self.fail_next: Problem | None = None  #: laat de volgende set/clear mislukken (voor tests)
        self.confirm_clear = True  #: zet op False om "ontvangst niet bevestigd" na te bootsen

    async def status(self) -> DeviceStatus:
        status = DeviceStatus()
        # Loop de controles af in dezelfde volgorde als bij een echte iPhone en
        # stop bij het nagebootste probleem.
        steps = [
            (Problem.APPLE_SERVICE_MISSING, "apple_service"),
            (Problem.NO_DEVICE, "connected"),
            (Problem.DEVICE_LOCKED, None),
            (Problem.NOT_TRUSTED, "trusted"),
            (Problem.IOS_TOO_OLD, "ios_supported"),
            (Problem.DEVELOPER_MODE_OFF, "developer_mode"),
            (Problem.TUNNEL_FAILED, "tunnel"),
            (Problem.LOCATION_SERVICE_UNAVAILABLE, "location_service"),
        ]
        problem = self.problem
        if problem is None and not ios_supported(self.ios_version):
            problem = Problem.IOS_TOO_OLD
        for step_problem, field in steps:
            failed = problem is step_problem
            if field is not None:
                setattr(status, field, not failed)
            if field == "connected" and not failed:
                status.name, status.model, status.ios_version = self.name, self.model, self.ios_version
            if failed:
                status.problem = problem
                return status
        status.ddi_mounted = True
        self.connected = True  # net als bij een echte iPhone: een geslaagde controle laat de verbinding open
        return status

    async def pair(self, timeout: float = 60.0) -> None:
        if self.problem in (Problem.NOT_TRUSTED, Problem.DEVICE_LOCKED):
            self.problem = None

    async def reveal_developer_mode(self) -> None:
        self._require_no_problem_before(Problem.DEVELOPER_MODE_OFF)

    async def mount_ddi(self) -> None:
        if self.problem is Problem.LOCATION_SERVICE_UNAVAILABLE:
            self.problem = None

    async def connect(self) -> None:
        if self.problem is not None:
            raise DeviceError(self.problem, "nagebootst probleem")
        self.connected = True

    async def set_location(self, point: LatLon) -> None:
        await self.connect()
        self._maybe_fail()
        self.location = point
        self.history.append(("set", point))

    async def clear_location(self) -> bool:
        await self.connect()
        self._maybe_fail()
        self.location = None
        self.history.append(("clear", None))
        return self.confirm_clear

    def connection_alive(self) -> bool:
        return self.connected

    async def close(self) -> None:
        self.connected = False

    def unplug(self) -> None:
        """Bootst na dat de kabel eruit wordt getrokken. De nep-locatie blijft staan, net als bij een echte iPhone."""
        self.connected = False
        self.problem = Problem.NO_DEVICE

    def plug_in(self) -> None:
        """Bootst na dat de kabel er weer in gaat."""
        self.problem = None

    def _maybe_fail(self) -> None:
        if self.fail_next is not None:
            problem, self.fail_next = self.fail_next, None
            self.connected = False
            raise DeviceError(problem, "nagebootste fout")

    def _require_no_problem_before(self, step: Problem) -> None:
        order = list(Problem)
        if self.problem is not None and order.index(self.problem) < order.index(step):
            raise DeviceError(self.problem, "nagebootst probleem")
