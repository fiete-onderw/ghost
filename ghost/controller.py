"""De regisseur: houdt bij wat er aan de hand is en voert opdrachten van de webpagina uit.

De webpagina praat nooit rechtstreeks met de iPhone. Hij vraagt het aan de
regisseur, en die laat alles wat de locatie verandert lopen via de
veiligheidsbewaker (safety.py).

De regisseur heeft ook een "bewaking": een taak die elke seconde even kijkt.

- Is de tijd van de auto-stop-timer om?            -> echte locatie terugvragen.
- Is de verbinding weg terwijl er iets actief is?  -> hard waarschuwen en blijven
                                                      proberen de echte locatie
                                                      terug te vragen.
- Is de iPhone nog niet klaar?                     -> af en toe opnieuw controleren,
                                                      zodat de pagina vanzelf
                                                      bijwerkt als je iets oplost.
- Loopt er een route?                              -> het volgende punt uitrekenen
                                                      en naar de iPhone sturen.

Een route werkt zo: de regisseur weet hoeveel meter er is afgelegd. Elke
seconde telt hij daar `snelheid x verstreken tijd` bij op, zoekt het punt dat
bij die afstand hoort (de wiskunde staat in geo.py) en zet de nep-locatie daar.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ghost import __version__
from ghost.coords import format_for_log, format_for_screen
from ghost.device.base import DeviceError, DeviceStatus, Problem
from ghost.geo import (
    SPEED_MAX_KMH,
    SPEED_MIN_KMH,
    SPEED_PRESETS_KMH,
    LatLon,
    Route,
    destination_point,
    distance_after,
    eta_seconds,
)
from ghost.osm import MAX_WAYPOINTS, OsmError, RoutingClient
from ghost.safety import ClosingError, Reason, RestoreResult, SafetyManager
from ghost.uitleg import DEVELOPER_MODE_REMINDER, uitleg

log = logging.getLogger(__name__)

#: Auto-stop-timer, in minuten. De timer staat ALTIJD aan: hij is het vangnet
#: voor als je wegloopt of het tabblad sluit.
TIMER_DEFAULT_MIN = 30
TIMER_CHOICES_MIN = (5, 15, 30, 60, 120, 240)
TIMER_MAX_MIN = 480

TICK_S = 1.0  #: hoe vaak de bewaking kijkt
STATUS_RETRY_S = 5.0  #: hoe vaak de status opnieuw wordt gecontroleerd zolang de iPhone niet klaar is
RESTORE_RETRY_S = 5.0  #: hoe vaak het terugvragen opnieuw wordt geprobeerd na een mislukking
PAIR_TIMEOUT_S = 60.0

CHECK_APPLE_MAPS = "Controleer in Apple Kaarten of je echte locatie weer klopt."

#: De controles in het statuspaneel: (veld in DeviceStatus, tekst goed, tekst fout).
CHECKS = (
    ("apple_service", "Apple USB-service gevonden", "Apple USB-service niet gevonden"),
    ("connected", "iPhone verbonden", "Geen iPhone verbonden"),
    ("trusted", "iPhone vertrouwt deze laptop", "iPhone vertrouwt deze laptop nog niet"),
    ("ios_supported", "iOS-versie ondersteund (17.4 of hoger)", "iOS-versie te oud (17.4 of hoger nodig)"),
    ("developer_mode", "Developer Mode staat aan", "Developer Mode staat uit"),
    ("tunnel", "Tunnel naar de iPhone werkt", "Tunnel naar de iPhone werkt niet"),
    ("location_service", "Locatiedienst bereikbaar", "Locatiedienst niet bereikbaar"),
)

#: Soorten routes: een rechte lijn, of over de weg te voet, met de fiets of met de auto.
ROUTE_MODES = ("line", "foot", "bike", "car")
ROUTE_MODE_TEXT = {"line": "rechte lijn", "foot": "lopen", "bike": "fietsen", "car": "auto"}
#: Was Ghost even bezig of sliep de laptop, dan springt de route niet ineens ver vooruit.
MAX_STEP_S = 3.0
KEPT_PLANS = 5

#: Windrichtingen voor het verschuiven, in graden (0 = noord).
DIRECTIONS = {"n": 0, "ne": 45, "e": 90, "se": 135, "s": 180, "sw": 225, "w": 270, "nw": 315}
NUDGE_MAX_M = 1000

ACTIONS = {
    "pair": "het koppelen",
    "reveal": "het zichtbaar maken van Developer Mode",
    "mount": "het plaatsen van het ontwikkelaars-image",
}


class ControllerError(Exception):
    """Een opdracht kon niet worden uitgevoerd. `message` is Nederlands en bedoeld voor de gebruiker."""

    def __init__(self, message: str, *, problem: Problem | None = None, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.problem = problem
        self.detail = detail


@dataclass
class RoutePlan:
    """Een uitgerekende route die klaarstaat om gestart te worden."""

    id: int
    waypoints: list[LatLon]  #: de punten die je hebt aangeklikt
    mode: str  #: "line", "foot", "bike" of "car"
    round_trip: bool
    repeat: bool
    route: Route  #: de wiskunde: waar ben je na x meter?

    @property
    def length_m(self) -> float:
        return self.route.length_m


@dataclass
class ActiveRoute:
    """Een route die nu loopt."""

    plan: RoutePlan
    speed_kmh: float
    last_mono: float  #: tijdstip van de vorige stap
    travelled_m: float = 0.0  #: totaal afgelegd, over alle rondes
    paused: bool = False
    finished: bool = False


@dataclass(frozen=True)
class Notice:
    """Een melding voor de gebruiker. `kind` is "ok", "info", "warn" of "error"."""

    id: int
    kind: str
    title: str
    text: str
    hint: str
    at: float


class Controller:
    def __init__(
        self,
        safety: SafetyManager,
        *,
        demo: bool = False,
        debug: bool = False,
        log_path: Path | None = None,
        startup_delay: float = 0.0,
        routing: RoutingClient | None = None,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.safety = safety
        self.demo = demo
        self.debug = debug
        self.log_path = log_path
        self._startup_delay = startup_delay
        self._clock = clock
        self._monotonic = monotonic

        self.status: DeviceStatus | None = None  #: laatste controle van de iPhone
        self.checking = False  #: er loopt nu een controle
        self.busy: str | None = None  #: "pair", "reveal" of "mount" als die nu loopt
        self.connection_lost = False  #: verbinding weg terwijl er een nep-locatie actief is
        self.notice: Notice | None = None
        self.route: ActiveRoute | None = None  #: de route die nu loopt, of None bij een vaste plek

        self._routing = routing or RoutingClient()
        self._plans: OrderedDict[int, RoutePlan] = OrderedDict()
        self._plan_count = 0

        self._started_at: float | None = None
        self._minutes: int | None = None
        # De timer loopt op twee klokken tegelijk: de gewone klok en een
        # "stopwatch" (monotonic). Wie het eerst om is, wint. Zo stopt de timer
        # ook op tijd als de laptop heeft geslapen of de klok is verzet.
        self._deadline_wall: float | None = None
        self._deadline_mono: float | None = None

        self._status_lock = asyncio.Lock()
        self._next_status = 0.0
        self._next_restore = 0.0
        self._notice_count = 0
        self._monitor: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()  # seintje aan de bewaking: stop nu

    # ------------------------------------------------------------------
    # Starten en stoppen van de bewaking
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start de bewaking op de achtergrond. De eerste actie is de iPhone controleren."""
        if self._monitor is None:
            self._monitor = asyncio.create_task(self._run_monitor(), name="ghost-bewaking")

    async def close(self) -> None:
        """Stop de bewaking. Loopt er nog een actie, dan krijgt die even de tijd om af te ronden."""
        monitor, self._monitor = self._monitor, None
        self._stopping.set()
        if monitor is None:
            return
        _, pending = await asyncio.wait({monitor}, timeout=2.0)
        if pending:
            monitor.cancel()
            await asyncio.wait({monitor}, timeout=2.0)

    async def _run_monitor(self) -> None:
        # Even wachten met de eerste controle. De verbinding met de iPhone
        # opbouwen houdt Ghost een paar tellen bezig; zo kan de browser eerst
        # de pagina laden en zie je meteen "Bezig met controleren".
        if self._startup_delay > 0:
            try:
                await asyncio.wait_for(self._stopping.wait(), self._startup_delay)
            except TimeoutError:
                pass
        first = True
        while not self.safety.closing and not self._stopping.is_set():
            try:
                if first:
                    first = False
                    await self.refresh_status()
                else:
                    await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Een fout in de bewaking mag nooit betekenen dat een nep-locatie
                # onbewaakt blijft staan. Bij twijfel: echte locatie terugvragen.
                log.exception("Onverwachte fout in de bewaking")
                try:
                    if self.safety.needs_restore and not self.safety.closing:
                        await self._restore(Reason.ERROR)
                except Exception:
                    # De bewaking zelf mag hoe dan ook niet stoppen: zonder
                    # bewaking gaat ook de auto-stop-timer niet meer af.
                    log.exception("Terugvragen na een fout in de bewaking mislukte ook")
            # Wacht een seconde, of korter als Ghost intussen moet stoppen.
            try:
                await asyncio.wait_for(self._stopping.wait(), TICK_S)
            except TimeoutError:
                pass

    async def tick(self) -> None:
        """Eén ronde van de bewaking. Los aan te roepen, zodat tests niet hoeven te wachten."""
        safety = self.safety
        if safety.closing:
            return
        now = self._monotonic()

        # 1. Auto-stop-timer.
        if safety.point is not None and self._deadline_passed():
            log.info("Auto-stop: de ingestelde tijd is om")
            self._deadline_wall = self._deadline_mono = None  # één keer afgaan is genoeg
            await self._restore(Reason.TIMER)
            return

        # 2. Verbinding weg terwijl er een nep-locatie actief is.
        alive = safety.device.connection_alive()
        if safety.point is not None and not alive and not self.connection_lost:
            self.connection_lost = True
            self._next_restore = now  # meteen een eerste poging
            log.warning("Verbinding met de iPhone weg terwijl er een nep-locatie actief is")
            self._notify(
                "error",
                "Verbinding met de iPhone weg",
                "Je iPhone staat waarschijnlijk nog op de nep-locatie. Steek de kabel erin en ontgrendel "
                "de iPhone. Ghost vraagt dan vanzelf je echte locatie terug.",
            )

        # 3. Er moet nog opgeruimd worden: blijven proberen.
        if safety.uncertain or self.connection_lost:
            if now >= self._next_restore:
                self._next_restore = now + RESTORE_RETRY_S
                result = await self._restore(Reason.CONNECTION, announce_failure=False)
                if not result.ok:
                    await self.refresh_status()  # zodat het statuspaneel laat zien wat er mis is
            return

        # 4. Loopt er een route? Dan het volgende punt zetten.
        if self.route is not None and safety.point is not None:
            await self._advance_route(now)
            return

        # 5. Niets actief: houd het statuspaneel actueel zolang de iPhone niet klaar is.
        if safety.point is None and self.busy is None and not self.checking:
            not_ready = self.status is None or not self.status.ready or not alive
            if not_ready and now >= self._next_status:
                await self.refresh_status()

    # ------------------------------------------------------------------
    # Status van de iPhone
    # ------------------------------------------------------------------

    async def refresh_status(self) -> DeviceStatus:
        """Controleer de iPhone opnieuw. Doet bij de eerste keer "klaar" ook de schone start."""
        async with self._status_lock:
            self.checking = True
            try:
                status = await self.safety.device.status()
            except Exception as error:
                log.exception("Status opvragen gaf een onverwachte fout")
                status = DeviceStatus(problem=Problem.UNKNOWN, detail=repr(error))
            finally:
                self.checking = False
            self.status = status
            self._next_status = self._monotonic() + STATUS_RETRY_S
            if status.problem is not None:
                log.info("Status: probleem %s", status.problem.value)

            # Schone start: de eerste keer dat de iPhone klaar is, eerst de echte
            # locatie terugvragen. Ook als er volgens Ghost niets actief is.
            safety = self.safety
            if status.ready and safety.point is None and not safety.clean_start_done and not safety.closing:
                await self._restore(Reason.STARTUP)
            return status

    # ------------------------------------------------------------------
    # Opdrachten van de webpagina
    # ------------------------------------------------------------------

    async def set_fixed(self, point: LatLon, minutes: int | None = None) -> None:
        """Zet een vaste nep-locatie en start de auto-stop-timer."""
        minutes = self._check_minutes(TIMER_DEFAULT_MIN if minutes is None else minutes)
        await self._require_ready()
        await self._apply(point)

        self.route = None  # een vaste plek vervangt een lopende route
        self._started_at = self._clock()
        self._start_timer(minutes)
        self.connection_lost = False
        log.info(
            "Nep-locatie gezet: %s (auto-stop na %d min)", format_for_log(point, precise=self.debug), minutes
        )
        self._notify(
            "info",
            "Nep-locatie actief",
            "Je iPhone geeft deze locatie nu door aan alle apps. Controleer het in Apple Kaarten.",
        )

    async def nudge(self, direction: str, meters: int) -> None:
        """Verschuif de actieve nep-locatie een stukje in een windrichting. De timer loopt gewoon door."""
        if direction not in DIRECTIONS:
            raise ControllerError("Onbekende richting.")
        if not isinstance(meters, int) or isinstance(meters, bool) or not 1 <= meters <= NUDGE_MAX_M:
            raise ControllerError(f"Kies een stap tussen 1 en {NUDGE_MAX_M} meter.")
        point = self.safety.point
        if point is None:
            raise ControllerError("Er is geen nep-locatie actief om te verschuiven.")
        if self.route is not None and not self.route.finished:
            raise ControllerError("Tijdens een route kun je niet verschuiven. Stop de route eerst.")
        await self._require_ready()
        await self._apply(destination_point(point, DIRECTIONS[direction], meters))
        self.route = None

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    async def plan_route(
        self, waypoints: Sequence[LatLon], mode: str, *, round_trip: bool = False, repeat: bool = False
    ) -> RoutePlan:
        """Reken een route uit langs deze punten. Er gaat nog niets naar de iPhone."""
        if mode not in ROUTE_MODES:
            raise ControllerError("Onbekend soort route.")
        # Twee keer hetzelfde punt achter elkaar telt als één punt.
        points: list[LatLon] = []
        for point in waypoints:
            if not points or point != points[-1]:
                points.append(point)
        if len(points) < 2:
            raise ControllerError("Een route heeft minstens twee verschillende punten nodig.")
        if len(points) > MAX_WAYPOINTS:
            raise ControllerError(f"Een route mag hooguit {MAX_WAYPOINTS} punten hebben.")

        path: Sequence[LatLon] = points
        if mode != "line":
            try:
                path = (await self._routing.route(points, mode)).points
            except OsmError as error:
                raise ControllerError(error.message) from error
        route = Route(path, round_trip=round_trip, repeat=repeat)
        if route.length_m < 1:
            raise ControllerError("Deze route is te kort. Zet de punten verder uit elkaar.")

        self._plan_count += 1
        plan = RoutePlan(self._plan_count, points, mode, round_trip, repeat, route)
        self._plans[plan.id] = plan
        while len(self._plans) > KEPT_PLANS:
            self._plans.popitem(last=False)
        log.info("Route uitgerekend: %s, %.1f km", ROUTE_MODE_TEXT[mode], plan.length_m / 1000)
        return plan

    async def start_route(self, plan_id: int, speed_kmh: float, minutes: int | None = None) -> None:
        """Start een uitgerekende route: zet de nep-locatie op het beginpunt en ga bewegen."""
        plan = self._plans.get(plan_id)
        if plan is None:
            raise ControllerError("Deze route is verlopen. Klik de punten opnieuw aan.")
        speed = self._check_speed(speed_kmh)
        minutes = self._check_minutes(TIMER_DEFAULT_MIN if minutes is None else minutes)
        await self._require_ready()
        await self._apply(plan.route.position_at(0).point)

        self.route = ActiveRoute(plan, speed, last_mono=self._monotonic())
        self._started_at = self._clock()
        self._start_timer(minutes)
        self.connection_lost = False
        log.info(
            "Route gestart: %s, %.1f km, %g km/u (auto-stop na %d min)",
            ROUTE_MODE_TEXT[plan.mode],
            plan.length_m / 1000,
            speed,
            minutes,
        )
        self._notify(
            "info",
            "Route gestart",
            "Je nep-locatie beweegt nu langs de route. Controleer het in Apple Kaarten.",
        )

    def pause_route(self) -> None:
        route = self._moving_route()
        route.paused = True

    def resume_route(self) -> None:
        route = self._moving_route()
        route.paused = False
        route.last_mono = self._monotonic()  # de pauze telt niet mee als afgelegde tijd

    def set_route_speed(self, speed_kmh: float) -> None:
        route = self._moving_route()
        route.speed_kmh = self._check_speed(speed_kmh)

    def route_path(self, plan_id: int | None = None) -> list[LatLon]:
        """De lijn van een route, om op de kaart te tekenen. Zonder nummer: de route die nu loopt."""
        plan = self._plans.get(plan_id) if plan_id is not None else (self.route.plan if self.route else None)
        if plan is None:
            raise ControllerError("Deze route bestaat niet (meer).")
        return plan.route.path

    def _moving_route(self) -> ActiveRoute:
        if self.route is None or self.safety.point is None:
            raise ControllerError("Er loopt geen route.")
        if self.route.finished:
            raise ControllerError("Deze route is al klaar.")
        return self.route

    async def _advance_route(self, now: float) -> None:
        """Eén stap van de route: reken uit waar we nu horen te zijn en zet de nep-locatie daar."""
        route = self.route
        if route is None:
            return
        elapsed = max(0.0, min(now - route.last_mono, MAX_STEP_S))
        route.last_mono = now
        if route.paused or route.finished:
            return
        route.travelled_m += distance_after(route.speed_kmh, elapsed)
        position = route.plan.route.position_at(route.travelled_m)
        try:
            await self.safety.set_location(position.point)
        except ClosingError:
            return
        except Exception as error:
            self._set_failed(error)  # zet zelf een melding neer en stopt de route
            return
        if position.finished:
            route.finished = True
            log.info("Route klaar")
            self._notify(
                "ok",
                "Route klaar",
                "Je nep-locatie blijft op het eindpunt staan tot de timer afloopt of je op de rode knop klikt.",
            )

    # ------------------------------------------------------------------
    # Hulp bij het zetten
    # ------------------------------------------------------------------

    async def _require_ready(self) -> None:
        """Mag er nu een nep-locatie gezet worden? Zo niet: zeg in gewone taal waarom niet."""
        safety = self.safety
        if safety.closing:
            raise ControllerError("Ghost wordt afgesloten. Start Ghost opnieuw om een locatie te zetten.")
        if self.busy is not None:
            raise ControllerError(f"Even wachten: {ACTIONS[self.busy]} is nog bezig.")
        if safety.uncertain or self.connection_lost:
            raise ControllerError(
                "Eerst moet je echte locatie teruggevraagd zijn. Sluit je iPhone aan en klik op de rode knop."
            )
        if self.status is None or not self.status.ready:
            status = await self.refresh_status()
            if not status.ready:
                raise ControllerError(
                    "De iPhone is nog niet klaar. Los eerst het probleem bij '1. Je iPhone' op.",
                    problem=status.problem,
                    detail=status.detail,
                )

    async def _apply(self, point: LatLon) -> None:
        """Zet de nep-locatie via de veiligheidsbewaker. Mislukt het, dan volgt een nette foutmelding."""
        try:
            await self.safety.set_location(point)
        except ClosingError:
            raise ControllerError("Ghost wordt afgesloten. Start Ghost opnieuw om een locatie te zetten.") from None
        except Exception as error:
            raise self._set_failed(error) from error

    def _set_failed(self, error: Exception) -> ControllerError:
        """Het zetten is mislukt. De veiligheidsbewaker heeft de echte locatie dan al teruggevraagd."""
        problem = error.problem if isinstance(error, DeviceError) else Problem.UNKNOWN
        detail = error.detail if isinstance(error, DeviceError) else repr(error)
        if not isinstance(error, DeviceError):
            log.error("Onverwachte fout bij het zetten van de nep-locatie", exc_info=error)
        self.route = None
        restored = self.safety.last_restore is not None and self.safety.last_restore.ok
        if restored:
            self._clear_timer()
            message = "De nep-locatie zetten is mislukt. Voor de zekerheid is je echte locatie teruggevraagd."
        else:
            message = (
                "De nep-locatie zetten is mislukt, en je echte locatie kon NIET worden teruggevraagd. "
                "Ghost blijft het proberen."
            )
        self._next_status = 0.0  # de bewaking controleert de iPhone meteen opnieuw
        self._notify("error", "Nep-locatie zetten mislukt", f"{message} {CHECK_APPLE_MAPS}")
        return ControllerError(message, problem=problem, detail=detail)

    async def stop(self, reason: Reason = Reason.BUTTON) -> RestoreResult:
        """Vraag de echte locatie terug. Mag altijd, ook als er niets actief lijkt."""
        return await self._restore(reason)

    def extend_timer(self, add_minutes: int) -> None:
        """Geef de auto-stop-timer extra tijd. De resterende tijd wordt nooit langer dan het maximum."""
        if self.safety.point is None or self._deadline_wall is None or self._deadline_mono is None:
            raise ControllerError("Er is geen nep-locatie actief, dus er loopt geen timer.")
        if not isinstance(add_minutes, int) or isinstance(add_minutes, bool) or not 1 <= add_minutes <= TIMER_MAX_MIN:
            raise ControllerError("Kies een aantal minuten tussen 1 en 480.")
        remaining = max(0.0, self.remaining_seconds() or 0.0)
        new_remaining = min(remaining + add_minutes * 60, TIMER_MAX_MIN * 60)
        self._deadline_wall = self._clock() + new_remaining
        self._deadline_mono = self._monotonic() + new_remaining
        log.info("Auto-stop-timer verlengd; nog %d min", round(new_remaining / 60))

    async def run_action(self, action: str) -> None:
        """Een van de knoppen in het statuspaneel: koppelen, Developer Mode tonen of het image plaatsen."""
        if action not in ACTIONS:
            raise ControllerError("Onbekende actie.")
        safety = self.safety
        if safety.closing:
            raise ControllerError("Ghost wordt afgesloten.")
        if safety.needs_restore:
            raise ControllerError("Stop eerst de nep-locatie met de rode knop.")
        if self.busy is not None:
            raise ControllerError(f"Even wachten: {ACTIONS[self.busy]} is nog bezig.")

        device = safety.device
        self.busy = action
        try:
            if action == "pair":
                await device.pair(timeout=PAIR_TIMEOUT_S)
            elif action == "reveal":
                await device.reveal_developer_mode()
            else:
                await device.mount_ddi()
        except DeviceError as error:
            log.error("Actie %s mislukt: %s", action, error)
            info = uitleg(error.problem)
            detail = error.detail if error.problem in (Problem.NOT_TRUSTED, Problem.NO_DEVICE) else ""
            message = f"{info.titel}. {detail}".strip()
            self._notify("error", f"Mislukt: {ACTIONS[action]}", message)
            raise ControllerError(message, problem=error.problem, detail=error.detail) from error
        finally:
            self.busy = None

        if action == "pair":
            self._notify("ok", "Gekoppeld", "De iPhone vertrouwt deze laptop nu.")
        elif action == "reveal":
            self._notify(
                "info",
                "De schakelaar is nu zichtbaar op je iPhone",
                "Zet hem zelf aan: Instellingen > Privacy en beveiliging > Developer Mode (helemaal onderaan). "
                "De iPhone herstart daarna. Ontgrendel hem en bevestig met 'Schakel in'.",
            )
        else:
            self._notify("ok", "Ontwikkelaars-image geplaatst", "Ghost controleert de iPhone nu opnieuw.")
        await self.refresh_status()

    # ------------------------------------------------------------------
    # Intern
    # ------------------------------------------------------------------

    async def _restore(self, reason: Reason, *, announce_failure: bool = True) -> RestoreResult:
        """Vraag de echte locatie terug en werk daarna timer en melding bij."""
        first_of_session = not self.safety.clean_start_done
        result = await self.safety.restore(reason)
        if result.ok:
            self._clear_timer()
            self.connection_lost = False
            self.route = None
            self._announce_restored(result, first_of_session)
        elif announce_failure:
            self._notify(
                "error",
                "Je echte locatie kon NIET worden teruggevraagd",
                "Je iPhone staat mogelijk nog op de nep-locatie. Controleer de kabel en ontgrendel de iPhone. "
                "Ghost blijft het elke paar seconden opnieuw proberen.",
            )
        return result

    def _announce_restored(self, result: RestoreResult, first_of_session: bool) -> None:
        if result.confirmed:
            kind, text = "ok", f"Het verzoek is bij de iPhone aangekomen. {CHECK_APPLE_MAPS}"
        else:
            kind = "warn"
            text = (
                "Het verzoek is verstuurd, maar de iPhone heeft de ontvangst niet bevestigd. "
                f"{CHECK_APPLE_MAPS} Klopt het niet, klik dan nog een keer op de rode knop."
            )

        reason = result.reason
        clean_start = reason is Reason.STARTUP or (reason is Reason.CONNECTION and first_of_session)
        error_showing = self.notice is not None and self.notice.kind == "error"
        if clean_start and self.safety.unclean_previous_exit:
            self.safety.unclean_previous_exit = False
            title = "Ghost was de vorige keer niet netjes afgesloten"
            text = f"Daarom is bij het opstarten meteen je echte locatie teruggevraagd. {text}"
        elif clean_start and not error_showing:
            return  # de gewone schone start: geen melding nodig, het statuspaneel toont het
        elif reason is Reason.TIMER:
            title = "Auto-stop: de tijd was om. Echte locatie teruggevraagd"
        elif reason is Reason.CONNECTION:
            title = "Verbinding terug: echte locatie teruggevraagd"
        elif reason is Reason.ERROR:
            title = "Er ging iets mis. Echte locatie teruggevraagd"
        else:
            title = "Echte locatie teruggevraagd"
        hint = DEVELOPER_MODE_REMINDER if reason in (Reason.BUTTON, Reason.TIMER, Reason.QUIT) else ""
        self._notify(kind, title, text, hint)

    def _notify(self, kind: str, title: str, text: str, hint: str = "") -> None:
        self._notice_count += 1
        self.notice = Notice(self._notice_count, kind, title, text, hint, self._clock())

    @staticmethod
    def _check_minutes(minutes: Any) -> int:
        if not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= TIMER_MAX_MIN:
            raise ControllerError(f"Kies voor de timer een aantal minuten tussen 1 en {TIMER_MAX_MIN}.")
        return minutes

    @staticmethod
    def _check_speed(speed_kmh: Any) -> float:
        if isinstance(speed_kmh, bool) or not isinstance(speed_kmh, (int, float)):
            raise ControllerError("De snelheid moet een getal zijn.")
        if not SPEED_MIN_KMH <= speed_kmh <= SPEED_MAX_KMH:
            raise ControllerError(f"Kies een snelheid tussen {SPEED_MIN_KMH:g} en {SPEED_MAX_KMH:g} km/u.")
        return float(speed_kmh)

    def _start_timer(self, minutes: int) -> None:
        self._minutes = minutes
        self._deadline_wall = self._clock() + minutes * 60
        self._deadline_mono = self._monotonic() + minutes * 60

    def _clear_timer(self) -> None:
        self._started_at = self._minutes = None
        self._deadline_wall = self._deadline_mono = None

    def _deadline_passed(self) -> bool:
        if self._deadline_wall is None or self._deadline_mono is None:
            return False
        return self._clock() >= self._deadline_wall or self._monotonic() >= self._deadline_mono

    def remaining_seconds(self) -> float | None:
        """Hoeveel seconden tot de auto-stop, of None als er geen timer loopt."""
        if self._deadline_wall is None or self._deadline_mono is None:
            return None
        return max(0.0, min(self._deadline_wall - self._clock(), self._deadline_mono - self._monotonic()))

    # ------------------------------------------------------------------
    # Alles wat de webpagina moet weten, in één keer
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        safety = self.safety
        point = safety.point
        remaining = self.remaining_seconds()
        return {
            "app": {
                "version": __version__,
                "demo": self.demo,
                "log": str(self.log_path) if self.log_path else "",
                "closing": safety.closing,
            },
            "device": self._device_snapshot(),
            "simulation": {
                "active": point is not None,
                "lat": point.lat if point else None,
                "lon": point.lon if point else None,
                "text": format_for_screen(point) if point else "",
                "since": self._started_at,
                "minutes": self._minutes,
                "remaining_s": None if remaining is None else int(remaining + 0.999),
                "uncertain": safety.uncertain,
                "connection_lost": self.connection_lost,
                "clean_start_done": safety.clean_start_done,
                "route": self._route_snapshot(),
            },
            "speeds": {"presets": dict(SPEED_PRESETS_KMH), "min": SPEED_MIN_KMH, "max": SPEED_MAX_KMH},
            "timer": {
                "default": TIMER_DEFAULT_MIN,
                "choices": list(TIMER_CHOICES_MIN),
                "max": TIMER_MAX_MIN,
            },
            "notice": None
            if self.notice is None
            else {
                "id": self.notice.id,
                "kind": self.notice.kind,
                "title": self.notice.title,
                "text": self.notice.text,
                "hint": self.notice.hint,
                "at": self.notice.at,
            },
        }

    def _route_snapshot(self) -> dict[str, Any] | None:
        route = self.route
        if route is None or self.safety.point is None:
            return None
        plan = route.plan
        position = plan.route.position_at(route.travelled_m)
        eta = eta_seconds(position.remaining_m, route.speed_kmh)
        return {
            "plan_id": plan.id,
            "mode": plan.mode,
            "round_trip": plan.round_trip,
            "repeat": plan.repeat,
            "speed_kmh": route.speed_kmh,
            "paused": route.paused,
            "finished": route.finished,
            "length_m": round(plan.length_m, 1),
            "travelled_m": round(position.travelled_m, 1),
            "remaining_m": round(position.remaining_m, 1),
            "lap": position.lap + 1,
            "eta_s": None if route.finished else int(eta + 0.999),
        }

    def _device_snapshot(self) -> dict[str, Any]:
        status = self.status
        result: dict[str, Any] = {
            "checked": status is not None,
            "checking": self.checking,
            "busy": self.busy,
            "ready": bool(status and status.ready),
            "name": status.name if status else None,
            "model": status.model if status else None,
            "ios": status.ios_version if status else None,
            "count": status.device_count if status else 0,
            "ddi_mounted": status.ddi_mounted if status else None,
            "checks": [],
            "problem": None,
        }
        if status is None:
            return result
        for field, good, bad in CHECKS:
            value = getattr(status, field)
            result["checks"].append({"id": field, "ok": value, "text": bad if value is False else good})
        if status.problem is not None:
            result["problem"] = problem_snapshot(status.problem, status.detail)
        return result


def problem_snapshot(problem: Problem, detail: str = "") -> dict[str, Any]:
    """De uitleg bij een probleem, in de vorm die de webpagina verwacht."""
    info = uitleg(problem)
    return {
        "code": problem.value,
        "title": info.titel,
        "what": info.wat,
        "steps": list(info.stappen_voor_web()),
        # Technische details tonen we alleen als ze voor de gebruiker bedoeld zijn.
        "detail": detail if problem in (Problem.NOT_TRUSTED, Problem.NO_DEVICE) else "",
        "action": {"id": info.actie, "label": info.actie_tekst} if info.actie else None,
        "link": {"url": info.link, "label": info.link_tekst} if info.link else None,
    }
