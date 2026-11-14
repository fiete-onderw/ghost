"""Tests voor routes en verschuiven: beweegt de nep-locatie zoals bedoeld, en blijft het veilig?"""

import pytest

from ghost.controller import MAX_STEP_S, RECONNECT_GRACE_S, RESTORE_RETRY_S, Controller, ControllerError
from ghost.device.base import Problem
from ghost.device.fake import FakeDevice
from ghost.geo import LatLon, haversine_m
from ghost.osm import OsmError, RoadRoute
from ghost.safety import SafetyManager

# Drie punten op een rechte lijn naar het noorden, steeds ongeveer 111 meter uit elkaar.
A = LatLon(52.0000, 5.0)
B = LatLon(52.0010, 5.0)
C = LatLon(52.0020, 5.0)


class Klok:
    def __init__(self):
        self.wall = 1_800_000_000.0
        self.mono = 1000.0

    def vooruit(self, seconden):
        self.wall += seconden
        self.mono += seconden


class NepRouteserver:
    """Doet alsof hij de routeserver is. Geeft een route met een extra bocht terug."""

    def __init__(self, fout=None):
        self.fout = fout
        self.vragen = []

    async def route(self, waypoints, profile):
        self.vragen.append((list(waypoints), profile))
        if self.fout:
            raise OsmError(self.fout)
        bocht = LatLon(waypoints[0].lat, waypoints[-1].lon + 0.001)
        return RoadRoute([waypoints[0], bocht, waypoints[-1]], 0.0)


async def regisseur(toestel=None, routeserver=None):
    toestel = toestel or FakeDevice()
    klok = Klok()
    controller = Controller(
        SafetyManager(toestel, clock=lambda: klok.wall),
        routing=routeserver or NepRouteserver(),
        clock=lambda: klok.wall,
        monotonic=lambda: klok.mono,
    )
    await controller.refresh_status()
    return controller, toestel, klok


async def loop(controller, klok, seconden):
    """Laat de bewaking `seconden` keer een ronde van één seconde doen."""
    for _ in range(seconden):
        klok.vooruit(1)
        await controller.tick()


# --- plannen -------------------------------------------------------------


async def test_rechte_lijn_plannen_raakt_iphone_en_internet_niet():
    routeserver = NepRouteserver()
    controller, toestel, _ = await regisseur(routeserver=routeserver)
    plan = await controller.plan_route([A, B, C], "line")
    assert plan.length_m == pytest.approx(haversine_m(A, C), rel=1e-6)
    assert routeserver.vragen == []
    assert toestel.history == [("clear", None)]  # alleen de schone start
    assert controller.snapshot()["simulation"]["active"] is False


async def test_route_over_de_weg_vraagt_de_routeserver():
    routeserver = NepRouteserver()
    controller, _, _ = await regisseur(routeserver=routeserver)
    plan = await controller.plan_route([A, C], "bike")
    assert routeserver.vragen == [([A, C], "bike")]
    assert len(plan.route.path) == 3  # met de bocht van de server
    assert plan.length_m > haversine_m(A, C)


async def test_fout_van_de_routeserver_wordt_doorgegeven_in_gewone_taal():
    controller, _, _ = await regisseur(routeserver=NepRouteserver(fout="De routeserver is niet bereikbaar."))
    with pytest.raises(ControllerError) as fout:
        await controller.plan_route([A, C], "car")
    assert "niet bereikbaar" in fout.value.message


@pytest.mark.parametrize(
    ("punten", "soort"),
    [([A], "line"), ([A, A, A], "line"), ([A, B], "vliegen"), ([A, B] * 13, "line"), ([], "line")],
)
async def test_onmogelijke_routes_worden_geweigerd(punten, soort):
    controller, _, _ = await regisseur()
    with pytest.raises(ControllerError):
        await controller.plan_route(punten, soort)


async def test_dubbel_aangeklikte_punten_tellen_als_een():
    controller, _, _ = await regisseur()
    plan = await controller.plan_route([A, A, B, B, C], "line")
    assert plan.waypoints == [A, B, C]


# --- rijden --------------------------------------------------------------


async def test_route_start_op_het_beginpunt_en_beweegt_met_de_gevraagde_snelheid():
    controller, toestel, klok = await regisseur()
    plan = await controller.plan_route([A, C], "line")
    await controller.start_route(plan.id, 36.0, 30)  # 36 km/u = 10 meter per seconde
    assert toestel.location == A

    await loop(controller, klok, 5)
    assert haversine_m(A, toestel.location) == pytest.approx(50, abs=0.01)
    beeld = controller.snapshot()["simulation"]
    assert beeld["active"] is True
    assert beeld["route"]["travelled_m"] == pytest.approx(50, abs=0.1)
    assert beeld["route"]["remaining_m"] == pytest.approx(plan.length_m - 50, abs=0.1)
    assert beeld["route"]["speed_kmh"] == 36.0
    assert beeld["route"]["finished"] is False


async def test_aan_het_eind_blijft_de_nep_locatie_op_het_eindpunt_staan():
    controller, toestel, klok = await regisseur()
    plan = await controller.plan_route([A, B], "line")  # ongeveer 111 meter
    await controller.start_route(plan.id, 36.0, 30)
    await loop(controller, klok, 15)
    assert toestel.location == B
    beeld = controller.snapshot()
    assert beeld["simulation"]["active"] is True  # nog steeds actief, tot de timer of de rode knop
    assert beeld["simulation"]["route"]["finished"] is True
    assert beeld["simulation"]["route"]["remaining_m"] == 0
    assert beeld["notice"]["title"] == "Route klaar"

    zetten = len(toestel.history)
    await loop(controller, klok, 5)
    assert len(toestel.history) == zetten  # klaar is klaar: er wordt niets meer gestuurd


async def test_heen_en_terug_eindigt_weer_op_het_beginpunt():
    controller, toestel, klok = await regisseur()
    plan = await controller.plan_route([A, B], "line", round_trip=True)
    assert plan.length_m == pytest.approx(2 * haversine_m(A, B), rel=1e-6)
    await controller.start_route(plan.id, 36.0, 30)
    await loop(controller, klok, 30)
    assert toestel.location == A
    assert controller.snapshot()["simulation"]["route"]["finished"] is True


async def test_herhalen_begint_opnieuw_en_wordt_nooit_klaar():
    controller, toestel, klok = await regisseur()
    plan = await controller.plan_route([A, B], "line", round_trip=True, repeat=True)
    await controller.start_route(plan.id, 36.0, 60)
    await loop(controller, klok, 60)  # 600 meter, de ronde is ongeveer 222 meter
    route = controller.snapshot()["simulation"]["route"]
    assert route["finished"] is False
    assert route["lap"] == 3
    assert toestel.location is not None


async def test_pauze_houdt_de_nep_locatie_op_zijn_plek():
    controller, toestel, klok = await regisseur()
    plan = await controller.plan_route([A, C], "line")
    await controller.start_route(plan.id, 36.0, 30)
    await loop(controller, klok, 3)
    controller.pause_route()
    plek = toestel.location
    zetten = len(toestel.history)
    await loop(controller, klok, 10)
    assert toestel.location == plek
    assert len(toestel.history) == zetten
    assert controller.snapshot()["simulation"]["route"]["paused"] is True

    controller.resume_route()
    await loop(controller, klok, 2)
    # De tien seconden pauze tellen niet mee: 3 + 2 seconden gereden.
    assert haversine_m(A, toestel.location) == pytest.approx(50, abs=0.01)


async def test_snelheid_veranderen_onderweg():
    controller, toestel, klok = await regisseur()
    plan = await controller.plan_route([A, C], "line")
    await controller.start_route(plan.id, 36.0, 30)
    await loop(controller, klok, 2)  # 20 meter
    controller.set_route_speed(18.0)  # half zo snel
    await loop(controller, klok, 2)  # nog 10 meter
    assert haversine_m(A, toestel.location) == pytest.approx(30, abs=0.01)


@pytest.mark.parametrize("snelheid", [0, 0.5, 121, -5, "snel", True, None])
async def test_onmogelijke_snelheid_wordt_geweigerd(snelheid):
    controller, toestel, _ = await regisseur()
    plan = await controller.plan_route([A, C], "line")
    with pytest.raises(ControllerError):
        await controller.start_route(plan.id, snelheid, 30)
    assert toestel.location is None


async def test_na_een_hapering_springt_de_route_niet_ver_vooruit():
    """Was Ghost even bezig of sliep de laptop, dan telt hooguit een paar seconden mee."""
    controller, toestel, klok = await regisseur()
    plan = await controller.plan_route([A, LatLon(53.0, 5.0)], "line")
    await controller.start_route(plan.id, 36.0, 240)
    klok.mono += 600  # tien minuten "weg" op de stopwatch, de gewone klok staat stil
    await controller.tick()
    assert haversine_m(A, toestel.location) == pytest.approx(10 * MAX_STEP_S, abs=0.01)


async def test_onbekende_of_verlopen_route_start_niet():
    controller, toestel, _ = await regisseur()
    with pytest.raises(ControllerError) as fout:
        await controller.start_route(999, 5.0, 30)
    assert "verlopen" in fout.value.message
    assert toestel.location is None


async def test_alleen_de_laatste_routes_worden_onthouden():
    controller, _, _ = await regisseur()
    eerste = await controller.plan_route([A, B], "line")
    for _ in range(6):
        await controller.plan_route([A, C], "line")
    with pytest.raises(ControllerError):
        await controller.start_route(eerste.id, 5.0, 30)


# --- veiligheid tijdens een route ------------------------------------------


async def test_rode_knop_stopt_de_route():
    controller, toestel, klok = await regisseur()
    plan = await controller.plan_route([A, C], "line")
    await controller.start_route(plan.id, 36.0, 30)
    await loop(controller, klok, 2)
    await controller.stop()
    assert toestel.location is None
    assert controller.snapshot()["simulation"]["route"] is None
    zetten = len(toestel.history)
    await loop(controller, klok, 5)
    assert len(toestel.history) == zetten  # er wordt niets meer gezet
    with pytest.raises(ControllerError):
        controller.pause_route()


async def test_timer_stopt_ook_een_route():
    controller, toestel, klok = await regisseur()
    plan = await controller.plan_route([A, LatLon(53.0, 5.0)], "line", repeat=True)
    await controller.start_route(plan.id, 5.0, 1)
    await loop(controller, klok, 61)
    assert toestel.location is None
    beeld = controller.snapshot()
    assert beeld["simulation"]["active"] is False
    assert "Auto-stop" in beeld["notice"]["title"]


async def test_hapering_tijdens_een_route_wordt_hersteld_en_de_route_loopt_door():
    """Het probleem uit de praktijk: één mislukt punt gooide de hele route weg. Nu niet meer."""
    controller, toestel, klok = await regisseur()
    plan = await controller.plan_route([A, C], "line")
    await controller.start_route(plan.id, 3.6, 30)  # 1 meter per seconde
    await loop(controller, klok, 5)
    voor = controller.snapshot()["simulation"]["route"]["travelled_m"]

    toestel.fail_next = Problem.CONNECTION_LOST  # één punt komt niet aan
    await loop(controller, klok, 1)
    beeld = controller.snapshot()
    assert beeld["simulation"]["reconnecting"] is not None
    assert beeld["simulation"]["route"] is not None  # de route is er nog
    assert ("clear", None) not in toestel.history[1:]

    await loop(controller, klok, 3)
    beeld = controller.snapshot()
    assert beeld["simulation"]["reconnecting"] is None
    assert beeld["simulation"]["reconnects"] == 1
    assert beeld["simulation"]["route"]["travelled_m"] > voor  # en hij beweegt weer
    assert beeld["simulation"]["connection_lost"] is False
    assert "route gaat verder" in beeld["notice"]["text"].lower() or beeld["notice"]["kind"] == "ok"


async def test_route_springt_niet_vooruit_na_een_hapering():
    controller, toestel, klok = await regisseur()
    plan = await controller.plan_route([A, C], "line")
    await controller.start_route(plan.id, 3.6, 30)
    await loop(controller, klok, 5)
    voor = controller.snapshot()["simulation"]["route"]["travelled_m"]
    toestel.unplug()
    await loop(controller, klok, 15)  # een kwart minuut geen verbinding
    toestel.plug_in()
    await loop(controller, klok, 3)
    na = controller.snapshot()["simulation"]["route"]["travelled_m"]
    assert controller.snapshot()["simulation"]["reconnecting"] is None
    assert na - voor < 8  # de tijd zonder verbinding telt niet als afgelegd


async def test_kabel_lang_eruit_tijdens_een_route_stopt_het_bewegen_en_ruimt_op_zodra_het_kan():
    controller, toestel, klok = await regisseur()
    plan = await controller.plan_route([A, C], "line")
    await controller.start_route(plan.id, 3.6, 30)
    await loop(controller, klok, 2)
    toestel.unplug()
    zetten = len(toestel.history)
    await loop(controller, klok, int(RECONNECT_GRACE_S) + 3)
    assert controller.snapshot()["simulation"]["connection_lost"] is True
    assert len(toestel.history) == zetten  # geen nieuwe punten naar een iPhone die er niet is

    toestel.plug_in()
    klok.vooruit(RESTORE_RETRY_S + 1)
    await controller.tick()
    beeld = controller.snapshot()
    assert toestel.location is None
    assert beeld["simulation"]["route"] is None
    # De route is niet weg: je kunt hem hervatten vanaf waar hij was.
    assert beeld["simulation"]["interrupted"] is not None
    assert beeld["simulation"]["interrupted"]["travelled_m"] > 0
    assert "Hervat route" in beeld["notice"]["hint"]


async def test_onderbroken_route_hervatten_gaat_verder_waar_hij_was():
    controller, toestel, klok = await regisseur()
    plan = await controller.plan_route([A, C], "line")
    await controller.start_route(plan.id, 3.6, 60)
    await loop(controller, klok, 10)
    toestel.unplug()
    await loop(controller, klok, int(RECONNECT_GRACE_S) + 3)
    toestel.plug_in()
    klok.vooruit(RESTORE_RETRY_S + 1)
    await controller.tick()
    onderbroken = controller.snapshot()["simulation"]["interrupted"]
    await controller.refresh_status()

    await controller.continue_route()
    beeld = controller.snapshot()
    assert beeld["simulation"]["interrupted"] is None
    assert beeld["simulation"]["active"] is True
    assert beeld["simulation"]["minutes"] == 60  # dezelfde timer als eerst
    assert beeld["simulation"]["route"]["travelled_m"] == pytest.approx(onderbroken["travelled_m"], abs=0.2)
    assert haversine_m(toestel.location, A) == pytest.approx(onderbroken["travelled_m"], abs=0.5)
    await loop(controller, klok, 5)
    assert controller.snapshot()["simulation"]["route"]["travelled_m"] > onderbroken["travelled_m"]


async def test_rode_knop_vergeet_een_onderbroken_route():
    controller, toestel, klok = await regisseur()
    plan = await controller.plan_route([A, C], "line")
    await controller.start_route(plan.id, 3.6, 30)
    await loop(controller, klok, 3)
    toestel.unplug()
    await loop(controller, klok, int(RECONNECT_GRACE_S) + 3)
    toestel.plug_in()
    klok.vooruit(RESTORE_RETRY_S + 1)
    await controller.tick()
    assert controller.snapshot()["simulation"]["interrupted"] is not None
    await controller.stop()
    assert controller.snapshot()["simulation"]["interrupted"] is None
    with pytest.raises(ControllerError):
        await controller.continue_route()


async def test_zelf_gestopte_route_wordt_niet_als_onderbroken_bewaard():
    controller, toestel, klok = await regisseur()
    plan = await controller.plan_route([A, C], "line")
    await controller.start_route(plan.id, 3.6, 30)
    await loop(controller, klok, 3)
    await controller.stop()
    assert controller.snapshot()["simulation"]["interrupted"] is None


async def test_vaste_plek_zetten_vervangt_een_lopende_route():
    controller, toestel, klok = await regisseur()
    plan = await controller.plan_route([A, C], "line")
    await controller.start_route(plan.id, 36.0, 30)
    await controller.set_fixed(LatLon(48.8584, 2.2945), 15)
    await loop(controller, klok, 5)
    assert toestel.location == LatLon(48.8584, 2.2945)
    assert controller.snapshot()["simulation"]["route"] is None


async def test_route_start_niet_als_de_iphone_niet_klaar_is():
    controller, toestel, _ = await regisseur(FakeDevice(problem=Problem.NO_DEVICE))
    plan = await controller.plan_route([A, C], "line")  # plannen mag altijd
    with pytest.raises(ControllerError) as fout:
        await controller.start_route(plan.id, 5.0, 30)
    assert fout.value.problem is Problem.NO_DEVICE
    assert toestel.history == []


async def test_elke_seconde_een_punt_schrijft_het_merkbestand_maar_een_keer(tmp_path):
    merk = tmp_path / "merk.txt"
    toestel = FakeDevice()
    klok = Klok()
    controller = Controller(SafetyManager(toestel, marker=merk), clock=lambda: klok.wall, monotonic=lambda: klok.mono)
    await controller.refresh_status()
    plan = await controller.plan_route([A, C], "line")
    await controller.start_route(plan.id, 36.0, 30)
    eerste_keer = merk.stat().st_mtime_ns
    await loop(controller, klok, 5)
    assert merk.stat().st_mtime_ns == eerste_keer
    await controller.stop()
    assert not merk.exists()


# --- verschuiven -----------------------------------------------------------


async def test_verschuiven_zet_de_nep_locatie_een_stukje_verder_en_laat_de_timer_lopen():
    controller, toestel, klok = await regisseur()
    await controller.set_fixed(A, 30)
    klok.vooruit(60)
    await controller.nudge("e", 100)
    assert haversine_m(A, toestel.location) == pytest.approx(100, abs=0.01)
    assert toestel.location.lon > A.lon
    assert controller.snapshot()["simulation"]["remaining_s"] == 29 * 60  # de timer is niet opnieuw begonnen

    await controller.nudge("s", 50)
    assert toestel.location.lat < A.lat


@pytest.mark.parametrize(("richting", "meters"), [("omhoog", 10), ("n", 0), ("n", 1001), ("n", 2.5), ("n", True)])
async def test_onmogelijk_verschuiven_wordt_geweigerd(richting, meters):
    controller, toestel, _ = await regisseur()
    await controller.set_fixed(A, 30)
    with pytest.raises(ControllerError):
        await controller.nudge(richting, meters)
    assert toestel.location == A


async def test_verschuiven_kan_niet_zonder_actieve_nep_locatie_en_niet_tijdens_een_route():
    controller, toestel, klok = await regisseur()
    with pytest.raises(ControllerError):
        await controller.nudge("n", 10)
    assert toestel.location is None

    plan = await controller.plan_route([A, B], "line")
    await controller.start_route(plan.id, 36.0, 30)
    with pytest.raises(ControllerError):
        await controller.nudge("n", 10)

    await loop(controller, klok, 15)  # route klaar: nu mag het wel
    await controller.nudge("n", 10)
    assert haversine_m(B, toestel.location) == pytest.approx(10, abs=0.01)
    assert controller.snapshot()["simulation"]["route"] is None
