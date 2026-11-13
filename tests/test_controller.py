"""Tests voor de regisseur: timer, schone start, weggevallen verbinding en het statuspaneel."""

import asyncio
import time

import pytest

from ghost import paths
from ghost.controller import RESTORE_RETRY_S, STATUS_RETRY_S, TIMER_MAX_MIN, Controller, ControllerError
from ghost.device.base import Problem
from ghost.device.fake import FakeDevice
from ghost.geo import LatLon
from ghost.safety import Reason, SafetyManager

PARIJS = LatLon(48.8584, 2.2945)
AMSTERDAM = LatLon(52.3676, 4.9041)


class Klok:
    """Een klok die alleen loopt als de test dat zegt."""

    def __init__(self):
        self.wall = 1_800_000_000.0
        self.mono = 1000.0

    def vooruit(self, seconden):
        self.wall += seconden
        self.mono += seconden


def regisseur(toestel=None, *, merkbestand=False):
    toestel = toestel or FakeDevice()
    klok = Klok()
    safety = SafetyManager(toestel, marker=paths.active_marker() if merkbestand else None, clock=lambda: klok.wall)
    controller = Controller(safety, clock=lambda: klok.wall, monotonic=lambda: klok.mono)
    return controller, toestel, klok


# --- schone start --------------------------------------------------------


async def test_schone_start_vraagt_bij_de_eerste_controle_de_echte_locatie_terug():
    controller, toestel, _ = regisseur()
    await controller.refresh_status()
    assert toestel.history == [("clear", None)]
    beeld = controller.snapshot()
    assert beeld["simulation"]["clean_start_done"] is True
    assert beeld["device"]["ready"] is True
    assert beeld["notice"] is None  # de gewone schone start hoeft geen melding

    await controller.refresh_status()
    assert toestel.history == [("clear", None)]  # één keer is genoeg


async def test_schone_start_wacht_tot_de_iphone_klaar_is():
    controller, toestel, klok = regisseur(FakeDevice(problem=Problem.NO_DEVICE))
    await controller.refresh_status()
    assert toestel.history == []
    assert controller.snapshot()["simulation"]["clean_start_done"] is False

    toestel.plug_in()
    klok.vooruit(STATUS_RETRY_S + 1)
    await controller.tick()  # de bewaking controleert vanzelf opnieuw
    assert toestel.history == [("clear", None)]
    assert controller.snapshot()["device"]["ready"] is True


async def test_vorige_keer_niet_netjes_afgesloten_wordt_gemeld_en_opgeruimd():
    merk = paths.active_marker()
    merk.parent.mkdir(parents=True, exist_ok=True)
    merk.write_text("vorige keer", encoding="utf-8")
    controller, toestel, _ = regisseur(merkbestand=True)
    assert controller.snapshot()["simulation"]["uncertain"] is True

    await controller.refresh_status()
    assert toestel.history == [("clear", None)]
    beeld = controller.snapshot()
    assert beeld["simulation"]["uncertain"] is False
    assert "niet netjes afgesloten" in beeld["notice"]["title"]
    assert not merk.exists()


async def test_vorige_keer_niet_netjes_en_iphone_nog_niet_aangesloten_blijft_proberen():
    merk = paths.active_marker()
    merk.parent.mkdir(parents=True, exist_ok=True)
    merk.write_text("vorige keer", encoding="utf-8")
    controller, toestel, klok = regisseur(FakeDevice(problem=Problem.NO_DEVICE), merkbestand=True)
    await controller.refresh_status()
    await controller.tick()
    assert controller.snapshot()["simulation"]["uncertain"] is True
    assert controller.snapshot()["device"]["problem"]["code"] == "no_device"

    toestel.plug_in()
    klok.vooruit(RESTORE_RETRY_S + 1)
    await controller.tick()
    assert toestel.history == [("clear", None)]
    assert controller.snapshot()["simulation"]["uncertain"] is False
    assert "niet netjes afgesloten" in controller.snapshot()["notice"]["title"]


# --- vaste plek en timer -------------------------------------------------


async def test_vaste_plek_zetten_en_stoppen():
    controller, toestel, _ = regisseur()
    await controller.refresh_status()
    await controller.set_fixed(PARIJS, 15)
    beeld = controller.snapshot()["simulation"]
    assert beeld["active"] is True
    assert beeld["text"] == "48.85840, 2.29450"
    assert beeld["remaining_s"] == 15 * 60
    assert toestel.location == PARIJS

    resultaat = await controller.stop()
    assert resultaat.ok
    beeld = controller.snapshot()
    assert beeld["simulation"]["active"] is False
    assert beeld["simulation"]["remaining_s"] is None
    assert beeld["notice"]["title"] == "Echte locatie teruggevraagd"
    assert "Developer Mode" in beeld["notice"]["hint"]
    assert toestel.location is None


async def test_timer_stopt_vanzelf_als_de_tijd_om_is():
    controller, toestel, klok = regisseur()
    await controller.refresh_status()
    await controller.set_fixed(PARIJS, 5)

    klok.vooruit(5 * 60 - 1)
    await controller.tick()
    assert toestel.location == PARIJS  # nog één seconde te gaan

    klok.vooruit(1)
    await controller.tick()
    assert toestel.location is None
    beeld = controller.snapshot()
    assert beeld["simulation"]["active"] is False
    assert "Auto-stop" in beeld["notice"]["title"]


async def test_timer_stopt_ook_als_alleen_de_gewone_klok_verder_is():
    """Bijvoorbeeld nadat de laptop heeft geslapen: de stopwatch stond stil, de klok niet."""
    controller, toestel, klok = regisseur()
    await controller.refresh_status()
    await controller.set_fixed(PARIJS, 30)
    klok.wall += 31 * 60
    await controller.tick()
    assert toestel.location is None


async def test_timer_is_verplicht_en_begrensd():
    controller, toestel, _ = regisseur()
    await controller.refresh_status()
    for fout in (0, -5, TIMER_MAX_MIN + 1, 2.5, True, "30"):
        with pytest.raises(ControllerError):
            await controller.set_fixed(PARIJS, fout)
    assert toestel.location is None

    await controller.set_fixed(PARIJS)  # zonder keuze geldt de standaard van 30 minuten
    assert controller.snapshot()["simulation"]["remaining_s"] == 30 * 60


async def test_timer_verlengen():
    controller, _, klok = regisseur()
    await controller.refresh_status()
    await controller.set_fixed(PARIJS, 5)
    klok.vooruit(60)
    controller.extend_timer(15)
    assert controller.snapshot()["simulation"]["remaining_s"] == 19 * 60

    controller.extend_timer(TIMER_MAX_MIN)  # nooit langer dan het maximum vanaf nu
    assert controller.snapshot()["simulation"]["remaining_s"] == TIMER_MAX_MIN * 60


async def test_timer_verlengen_kan_alleen_als_er_iets_actief_is():
    controller, _, _ = regisseur()
    await controller.refresh_status()
    with pytest.raises(ControllerError):
        controller.extend_timer(15)


async def test_verplaatsen_start_de_timer_opnieuw():
    controller, toestel, klok = regisseur()
    await controller.refresh_status()
    await controller.set_fixed(PARIJS, 5)
    klok.vooruit(120)
    await controller.set_fixed(AMSTERDAM, 15)
    assert toestel.location == AMSTERDAM
    assert controller.snapshot()["simulation"]["remaining_s"] == 15 * 60


async def test_zetten_lukt_niet_als_de_iphone_niet_klaar_is():
    controller, toestel, _ = regisseur(FakeDevice(problem=Problem.DEVELOPER_MODE_OFF))
    await controller.refresh_status()
    with pytest.raises(ControllerError) as fout:
        await controller.set_fixed(PARIJS, 5)
    assert fout.value.problem is Problem.DEVELOPER_MODE_OFF
    assert toestel.history == []


async def test_mislukt_zetten_meldt_dat_de_echte_locatie_is_teruggevraagd():
    controller, toestel, _ = regisseur()
    await controller.refresh_status()
    toestel.fail_next = Problem.CONNECTION_LOST
    with pytest.raises(ControllerError) as fout:
        await controller.set_fixed(PARIJS, 5)
    assert "teruggevraagd" in fout.value.message
    beeld = controller.snapshot()
    assert beeld["simulation"]["active"] is False
    assert beeld["simulation"]["uncertain"] is False
    assert beeld["notice"]["kind"] == "error"
    assert toestel.history[-1] == ("clear", None)


# --- verbinding weg ------------------------------------------------------


async def test_kabel_eruit_tijdens_nep_locatie_waarschuwt_hard_en_ruimt_op_zodra_het_kan():
    controller, toestel, klok = regisseur()
    await controller.refresh_status()
    await controller.set_fixed(PARIJS, 30)

    toestel.unplug()
    await controller.tick()
    beeld = controller.snapshot()
    assert beeld["simulation"]["connection_lost"] is True
    assert beeld["simulation"]["active"] is True  # voor zover we weten staat de iPhone nog in Parijs
    assert toestel.location == PARIJS
    assert beeld["device"]["problem"]["code"] == "no_device"

    # Zolang de kabel eruit is, blijft het zo.
    klok.vooruit(RESTORE_RETRY_S + 1)
    await controller.tick()
    assert controller.snapshot()["simulation"]["connection_lost"] is True

    # Kabel erin: bij de volgende poging wordt de echte locatie teruggevraagd.
    toestel.plug_in()
    klok.vooruit(RESTORE_RETRY_S + 1)
    await controller.tick()
    beeld = controller.snapshot()
    assert toestel.location is None
    assert beeld["simulation"]["connection_lost"] is False
    assert beeld["simulation"]["active"] is False
    assert beeld["simulation"]["uncertain"] is False
    assert "Verbinding terug" in beeld["notice"]["title"]


async def test_tijdens_verbinding_weg_kan_er_geen_nieuwe_plek_gezet_worden():
    controller, toestel, _ = regisseur()
    await controller.refresh_status()
    await controller.set_fixed(PARIJS, 30)
    toestel.unplug()
    await controller.tick()
    with pytest.raises(ControllerError):
        await controller.set_fixed(AMSTERDAM, 30)


async def test_mislukte_stop_wordt_vanzelf_opnieuw_geprobeerd():
    controller, toestel, klok = regisseur()
    await controller.refresh_status()
    await controller.set_fixed(PARIJS, 30)
    toestel.fail_next = Problem.CONNECTION_LOST

    resultaat = await controller.stop()
    assert not resultaat.ok
    beeld = controller.snapshot()
    assert beeld["simulation"]["uncertain"] is True
    assert "NIET" in beeld["notice"]["title"]

    klok.vooruit(RESTORE_RETRY_S + 1)
    await controller.tick()
    assert toestel.location is None
    assert controller.snapshot()["simulation"]["uncertain"] is False


async def test_timer_loopt_af_terwijl_de_verbinding_weg_is():
    controller, toestel, klok = regisseur()
    await controller.refresh_status()
    await controller.set_fixed(PARIJS, 5)
    toestel.unplug()
    klok.vooruit(5 * 60 + 1)
    await controller.tick()
    assert controller.snapshot()["simulation"]["uncertain"] is True

    toestel.plug_in()
    klok.vooruit(RESTORE_RETRY_S + 1)
    await controller.tick()
    assert toestel.location is None


# --- statuspaneel --------------------------------------------------------


@pytest.mark.parametrize(
    ("probleem", "actie"),
    [
        (Problem.NOT_TRUSTED, "pair"),
        (Problem.DEVELOPER_MODE_OFF, "reveal"),
        (Problem.LOCATION_SERVICE_UNAVAILABLE, "mount"),
        (Problem.NO_DEVICE, None),
        (Problem.TUNNEL_FAILED, None),
    ],
)
async def test_statuspaneel_legt_het_probleem_uit_met_de_juiste_knop(probleem, actie):
    controller, _, _ = regisseur(FakeDevice(problem=probleem))
    await controller.refresh_status()
    uitleg = controller.snapshot()["device"]["problem"]
    assert uitleg["code"] == probleem.value
    assert uitleg["title"] and uitleg["what"] and uitleg["steps"]
    assert (uitleg["action"] or {}).get("id") == actie
    assert not any("uv run" in stap for stap in uitleg["steps"])  # in de pagina geen terminal-commando's


async def test_developer_mode_uitleg_in_de_pagina_vraagt_nooit_om_de_toegangscode_te_verwijderen():
    controller, _, _ = regisseur(FakeDevice(problem=Problem.DEVELOPER_MODE_OFF))
    await controller.refresh_status()
    stappen = " ".join(controller.snapshot()["device"]["problem"]["steps"])
    assert "NIET te verwijderen" in stappen


async def test_koppelen_via_de_knop_lost_het_probleem_op():
    controller, toestel, _ = regisseur(FakeDevice(problem=Problem.NOT_TRUSTED))
    await controller.refresh_status()
    await controller.run_action("pair")
    beeld = controller.snapshot()
    assert beeld["device"]["ready"] is True
    assert beeld["device"]["busy"] is None
    assert toestel.history == [("clear", None)]  # en meteen de schone start


async def test_knoppen_in_het_statuspaneel_werken_niet_tijdens_een_nep_locatie():
    controller, _, _ = regisseur()
    await controller.refresh_status()
    await controller.set_fixed(PARIJS, 5)
    with pytest.raises(ControllerError):
        await controller.run_action("mount")
    with pytest.raises(ControllerError):
        await controller.run_action("iets-anders")


async def test_bewaking_controleert_niet_onnodig_als_alles_in_orde_is():
    controller, toestel, klok = regisseur()
    keren = []
    echte_status = toestel.status

    async def status():
        keren.append(1)
        return await echte_status()

    toestel.status = status
    await controller.refresh_status()
    for _ in range(5):
        klok.vooruit(STATUS_RETRY_S + 1)
        await controller.tick()
    assert len(keren) == 1


async def test_bewaking_merkt_dat_de_kabel_eruit_is_als_er_niets_actief_is():
    controller, toestel, klok = regisseur()
    await controller.refresh_status()
    toestel.unplug()
    klok.vooruit(STATUS_RETRY_S + 1)
    await controller.tick()
    beeld = controller.snapshot()
    assert beeld["device"]["ready"] is False
    assert beeld["device"]["problem"]["code"] == "no_device"
    assert beeld["simulation"]["uncertain"] is False  # er was niets actief, dus niets aan de hand


async def test_na_afsluiten_doet_de_bewaking_niets_meer():
    controller, toestel, klok = regisseur()
    await controller.refresh_status()
    await controller.set_fixed(PARIJS, 5)
    await controller.safety.shutdown(Reason.QUIT)
    aantal = len(toestel.history)
    klok.vooruit(3600)
    await controller.tick()
    assert len(toestel.history) == aantal
    with pytest.raises(ControllerError):
        await controller.set_fixed(PARIJS, 5)


async def test_bewaking_op_de_achtergrond_start_en_stopt():
    controller, toestel, _ = regisseur()
    controller.start()
    for _ in range(50):
        if controller.status is not None:
            break
        await asyncio.sleep(0.01)
    assert controller.snapshot()["device"]["ready"] is True
    assert toestel.history == [("clear", None)]

    start = time.monotonic()
    await controller.close()
    assert time.monotonic() - start < 0.5  # stoppen hoeft niet te wachten op de volgende ronde


async def test_eerste_controle_wacht_even_zodat_de_pagina_eerst_kan_laden():
    toestel = FakeDevice()
    controller = Controller(SafetyManager(toestel), startup_delay=0.15)
    controller.start()
    await asyncio.sleep(0.05)
    assert controller.status is None  # nog niet begonnen
    await asyncio.sleep(0.3)
    assert controller.status is not None and controller.status.ready
    await controller.close()


async def test_stoppen_tijdens_de_startpauze_gaat_meteen():
    controller = Controller(SafetyManager(FakeDevice()), startup_delay=30)
    controller.start()
    await asyncio.sleep(0.02)
    start = time.monotonic()
    await controller.close()
    assert time.monotonic() - start < 0.5


async def test_fout_in_de_bewaking_stopt_de_bewaking_niet_en_ruimt_op(monkeypatch):
    """Een programmeerfout in de bewaking mag nooit betekenen dat de timer niet meer afgaat."""
    monkeypatch.setattr("ghost.controller.TICK_S", 0.01)
    toestel = FakeDevice()
    controller = Controller(SafetyManager(toestel))
    await controller.refresh_status()
    await controller.set_fixed(PARIJS, 30)

    keren = []
    echte_tick = controller.tick

    async def tick():
        keren.append(1)
        if len(keren) == 1:
            raise RuntimeError("een programmeerfout")
        await echte_tick()

    controller.tick = tick
    controller.start()
    for _ in range(100):
        if len(keren) >= 3:
            break
        await asyncio.sleep(0.01)
    await controller.close()
    assert len(keren) >= 3  # de bewaking draait gewoon door
    assert toestel.location is None  # en bij twijfel is de echte locatie teruggevraagd
