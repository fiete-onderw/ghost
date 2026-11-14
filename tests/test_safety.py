"""Tests voor de veiligheidsbewaker: wordt de echte locatie ALTIJD teruggevraagd?"""

import asyncio

import pytest

from ghost import paths
from ghost.device.base import DeviceError, Problem
from ghost.device.fake import FakeDevice
from ghost.geo import LatLon
from ghost.safety import ClosingError, Reason, SafetyManager

PARIJS = LatLon(48.8584, 2.2945)
AMSTERDAM = LatLon(52.3676, 4.9041)


class KabelEruitBijZetten(FakeDevice):
    """Een nep-iPhone waarbij de kabel eruit gaat op het moment dat de locatie gezet wordt."""

    async def set_location(self, point):
        self.location = point  # de opdracht kwam nog net aan...
        self.unplug()  # ...maar daarna is de verbinding weg
        raise DeviceError(Problem.CONNECTION_LOST, "kabel eruit")


def bewaker(toestel=None, *, met_merkbestand=True):
    toestel = toestel or FakeDevice()
    merk = paths.active_marker() if met_merkbestand else None
    return SafetyManager(toestel, marker=merk), toestel


async def test_zetten_en_terugvragen():
    safety, toestel = bewaker()
    await safety.set_location(PARIJS)
    assert safety.point == PARIJS
    assert safety.needs_restore
    assert not safety.uncertain

    resultaat = await safety.restore(Reason.BUTTON)
    assert resultaat.ok and resultaat.confirmed
    assert safety.point is None
    assert not safety.needs_restore
    assert safety.clean_start_done
    assert toestel.history == [("set", PARIJS), ("clear", None)]


async def test_terugvragen_mag_altijd_ook_als_er_niets_actief_is():
    safety, toestel = bewaker()
    assert (await safety.restore(Reason.BUTTON)).ok
    assert (await safety.restore(Reason.BUTTON)).ok
    assert toestel.history == [("clear", None), ("clear", None)]


async def test_mislukt_zetten_vraagt_meteen_de_echte_locatie_terug():
    """Bij twijfel altijd opruimen: we weten niet of de opdracht is aangekomen."""
    safety, toestel = bewaker()
    toestel.fail_next = Problem.CONNECTION_LOST
    with pytest.raises(DeviceError):
        await safety.set_location(PARIJS)
    assert toestel.history == [("clear", None)]
    assert safety.point is None
    assert not safety.needs_restore
    assert safety.last_restore.reason is Reason.ERROR


async def test_mislukt_zetten_en_mislukt_terugvragen_blijft_onthouden():
    safety, toestel = bewaker(KabelEruitBijZetten())
    with pytest.raises(DeviceError):
        await safety.set_location(PARIJS)
    assert safety.uncertain
    assert safety.needs_restore
    assert toestel.location == PARIJS  # de nep-iPhone staat echt nog in Parijs
    assert paths.active_marker().exists()

    # Kabel er weer in: nu lukt het wel.
    toestel.plug_in()
    assert (await safety.restore(Reason.CONNECTION)).ok
    assert not safety.needs_restore
    assert toestel.location is None
    assert not paths.active_marker().exists()


async def test_mislukt_terugvragen_laat_de_nep_locatie_als_actief_staan():
    safety, toestel = bewaker()
    await safety.set_location(PARIJS)
    toestel.unplug()
    resultaat = await safety.restore(Reason.BUTTON)
    assert not resultaat.ok
    assert resultaat.problem is Problem.NO_DEVICE
    assert safety.point == PARIJS
    assert safety.uncertain
    assert safety.needs_restore


async def test_onverwachte_fout_bij_terugvragen_wordt_opgevangen():
    safety, toestel = bewaker()

    async def kapot():
        raise RuntimeError("iets vreemds")

    toestel.clear_location = kapot
    resultaat = await safety.restore(Reason.BUTTON)
    assert not resultaat.ok
    assert resultaat.problem is Problem.UNKNOWN
    assert safety.uncertain


async def test_niet_bevestigd_wordt_een_keer_opnieuw_geprobeerd_over_een_verse_verbinding():
    safety, toestel = bewaker()
    await safety.set_location(PARIJS)
    toestel.confirm_clear = False
    gesloten = []
    echte_close = toestel.close

    async def close():
        gesloten.append(True)
        await echte_close()

    toestel.close = close
    resultaat = await safety.restore(Reason.BUTTON)
    assert resultaat.ok
    assert not resultaat.confirmed
    assert toestel.history == [("set", PARIJS), ("clear", None), ("clear", None)]
    assert gesloten == [True]  # er is een verse verbinding gemaakt
    assert not safety.needs_restore  # verstuurd is verstuurd; de pagina zegt erbij dat het niet bevestigd is


async def test_niet_bevestigd_en_daarna_verbinding_weg_telt_als_mislukt():
    safety, toestel = bewaker()
    await safety.set_location(PARIJS)
    toestel.confirm_clear = False
    echte_close = toestel.close

    async def close():
        await echte_close()
        toestel.unplug()

    toestel.close = close
    resultaat = await safety.restore(Reason.BUTTON)
    assert not resultaat.ok
    assert safety.needs_restore


# --- merkbestand ---------------------------------------------------------


async def test_merkbestand_bestaat_zolang_er_iets_actief_is_en_bevat_geen_coordinaten():
    safety, _ = bewaker()
    merk = paths.active_marker()
    assert not merk.exists()
    await safety.set_location(LatLon(48.858412, 2.294512))
    assert merk.exists()
    inhoud = merk.read_text(encoding="utf-8")
    assert "48" not in inhoud and "2.29" not in inhoud
    await safety.restore(Reason.BUTTON)
    assert not merk.exists()


async def test_merkbestand_van_de_vorige_keer_betekent_onzeker():
    """Ghost is de vorige keer weggevallen terwijl er iets actief was."""
    merk = paths.active_marker()
    merk.parent.mkdir(parents=True, exist_ok=True)
    merk.write_text("vorige keer", encoding="utf-8")

    safety, toestel = bewaker()
    assert safety.uncertain
    assert safety.unclean_previous_exit
    assert safety.needs_restore

    await safety.restore(Reason.STARTUP)
    assert not safety.needs_restore
    assert not merk.exists()


async def test_merkbestand_dat_niet_geschreven_kan_worden_houdt_niets_tegen(tmp_path):
    geen_map = tmp_path / "bestand"
    geen_map.write_text("ik ben een bestand, geen map", encoding="utf-8")
    safety = SafetyManager(FakeDevice(), marker=geen_map / "merk.txt")
    await safety.set_location(PARIJS)
    assert safety.point == PARIJS
    assert (await safety.restore(Reason.BUTTON)).ok


# --- afsluiten -----------------------------------------------------------


async def test_afsluiten_met_actieve_nep_locatie_vraagt_de_echte_terug():
    safety, toestel = bewaker()
    await safety.set_location(PARIJS)
    resultaat = await safety.shutdown(Reason.CTRL_C)
    assert resultaat.ok
    assert resultaat.reason is Reason.CTRL_C
    assert toestel.location is None
    assert toestel.history[-1] == ("clear", None)


async def test_afsluiten_na_een_gelukte_clear_stuurt_niets_extra():
    safety, toestel = bewaker()
    await safety.set_location(PARIJS)
    await safety.restore(Reason.BUTTON)
    assert await safety.shutdown(Reason.CTRL_C) is None
    assert toestel.history == [("set", PARIJS), ("clear", None)]


async def test_afsluiten_zonder_eerdere_clear_stuurt_er_voor_de_zekerheid_een():
    """Er is verbinding, maar er is deze sessie nog nooit een clear gelukt."""
    safety, toestel = bewaker()
    await toestel.connect()
    resultaat = await safety.shutdown(Reason.QUIT)
    assert resultaat is not None and resultaat.ok
    assert toestel.history == [("clear", None)]


async def test_afsluiten_zonder_verbinding_en_zonder_actie_bouwt_geen_verbinding_op():
    safety, toestel = bewaker()
    assert await safety.shutdown(Reason.QUIT) is None
    assert toestel.history == []
    assert not toestel.connected


async def test_afsluiten_met_merkbestand_probeert_het_altijd_ook_zonder_verbinding():
    merk = paths.active_marker()
    merk.parent.mkdir(parents=True, exist_ok=True)
    merk.write_text("vorige keer", encoding="utf-8")
    safety, toestel = bewaker()
    resultaat = await safety.shutdown(Reason.QUIT)
    assert resultaat is not None and resultaat.ok
    assert toestel.history == [("clear", None)]


async def test_afsluiten_dat_mislukt_wordt_eerlijk_gemeld():
    safety, toestel = bewaker()
    await safety.set_location(PARIJS)
    toestel.unplug()
    resultaat = await safety.shutdown(Reason.CTRL_C)
    assert not resultaat.ok
    assert safety.needs_restore
    assert paths.active_marker().exists()  # de volgende start weet het nog


async def test_twee_keer_afsluiten_is_geen_probleem():
    safety, toestel = bewaker()
    await safety.set_location(PARIJS)
    eerste, tweede = await asyncio.gather(safety.shutdown(Reason.WINDOW_CLOSED), safety.shutdown(Reason.CTRL_C))
    assert eerste.ok
    assert tweede is None
    assert toestel.history == [("set", PARIJS), ("clear", None)]


async def test_na_afsluiten_kan_er_geen_nep_locatie_meer_gezet_worden():
    safety, toestel = bewaker()
    await safety.shutdown(Reason.QUIT)
    with pytest.raises(ClosingError):
        await safety.set_location(PARIJS)
    assert toestel.history == []


async def test_afsluiten_wacht_niet_eindeloos():
    safety, toestel = bewaker()
    await safety.set_location(PARIJS)

    async def hangt():
        await asyncio.sleep(60)

    toestel.clear_location = hangt
    resultaat = await safety.shutdown(Reason.CTRL_C, timeout=0.05)
    assert not resultaat.ok
    assert safety.needs_restore


async def test_verplaatsen_houdt_de_nieuwe_plek_bij():
    safety, toestel = bewaker()
    await safety.set_location(PARIJS)
    await safety.set_location(AMSTERDAM)
    assert safety.point == AMSTERDAM
    assert toestel.location == AMSTERDAM


async def test_verplaatsen_dat_mislukt_laat_de_vorige_nep_locatie_staan():
    """Bij een route of bij opnieuw verbinden: één mislukte stap is geen reden om op te ruimen."""
    toestel = FakeDevice()
    safety = SafetyManager(toestel)
    eerste, tweede = LatLon(52.0, 5.0), LatLon(52.001, 5.0)
    await safety.set_location(eerste)
    toestel.fail_next = Problem.CONNECTION_LOST
    with pytest.raises(DeviceError):
        await safety.set_location(tweede, restore_on_failure=False)
    assert safety.point == eerste
    assert safety.uncertain is False
    assert ("clear", None) not in toestel.history


async def test_eerste_keer_zetten_ruimt_bij_een_fout_altijd_op():
    """Is er nog niets actief, dan wordt er hoe dan ook opgeruimd."""
    toestel = FakeDevice()
    safety = SafetyManager(toestel)
    toestel.fail_next = Problem.CONNECTION_LOST
    with pytest.raises(DeviceError):
        await safety.set_location(LatLon(52.0, 5.0), restore_on_failure=False)
    assert safety.point is None
    assert toestel.history == [("clear", None)]
