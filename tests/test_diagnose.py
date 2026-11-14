"""Tests voor het diagnoserapport."""

from ghost import paths
from ghost.cli import EXIT_OK, main
from ghost.device.base import Problem
from ghost.device.fake import FakeDevice
from ghost.diagnose import build_report, redact, report_file


async def test_rapport_bij_een_gezonde_verbinding(tmp_path):
    log = tmp_path / "ghost.log"
    log.write_text("regel 1\nregel 2\n", encoding="utf-8")
    toestel = FakeDevice()
    rapport = await build_report(toestel, log, rounds=3, pause=0)
    assert "Verzoeken gelukt: 3 van 3" in rapport
    assert "bleef stabiel" in rapport
    assert "Locatiedienst bereikbaar: ja" in rapport
    assert "regel 2" in rapport
    assert toestel.location is None


async def test_rapport_bevat_geen_naam_of_serienummer(tmp_path):
    log = tmp_path / "ghost.log"
    log.write_text("fout bij 00008140-000A1B2C3D4E5F60 en a1b2c3d4e5f60718293a4b5c6d7e8f9012345678\n", encoding="utf-8")
    rapport = await build_report(FakeDevice(name="iPhone van Geheim"), log, rounds=1, pause=0)
    assert "Geheim" not in rapport
    assert "00008140-000A1B2C3D4E5F60" not in rapport
    assert "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678" not in rapport
    assert rapport.count("[serienummer verwijderd]") == 2


async def test_rapport_meldt_een_wankele_verbinding(tmp_path):
    toestel = FakeDevice()
    echte_clear = toestel.clear_location
    teller = {"n": 0}

    async def soms_kapot():
        teller["n"] += 1
        if teller["n"] == 2:
            toestel.fail_next = Problem.CONNECTION_LOST
        return await echte_clear()

    toestel.clear_location = soms_kapot
    rapport = await build_report(toestel, tmp_path / "bestaat-niet.log", rounds=3, pause=0)
    assert "Verzoeken gelukt: 2 van 3" in rapport
    assert "NIET stabiel" in rapport
    assert "ronde 2: connection_lost" in rapport


async def test_rapport_slaat_de_stabiliteitstest_over_als_de_iphone_niet_klaar_is(tmp_path):
    toestel = FakeDevice(problem=Problem.NOT_LISTED)
    rapport = await build_report(toestel, tmp_path / "x.log", rounds=3, pause=0)
    assert "Probleem: not_listed" in rapport
    assert "Overgeslagen" in rapport
    assert toestel.history == []


def test_serienummers_worden_weggehaald():
    assert redact("udid 00008140-000A1B2C3D4E5F60 klaar") == "udid [serienummer verwijderd] klaar"
    assert redact("gewone tekst 52.37, 4.90") == "gewone tekst 52.37, 4.90"


def test_commando_diagnose_schrijft_het_bestand(capsys, monkeypatch):
    monkeypatch.setattr("ghost.diagnose.STABILITY_ROUNDS", 2)

    async def geen_pauze(seconden):
        return None

    monkeypatch.setattr("ghost.diagnose.asyncio.sleep", geen_pauze)
    assert main(["diagnose"], device=FakeDevice()) == EXIT_OK
    assert report_file().exists()
    assert report_file().parent == paths.log_dir()
    uitvoer = capsys.readouterr().out
    assert "Verzoeken gelukt: 2 van 2" in uitvoer
    assert "ghost-diagnose.txt" in uitvoer
