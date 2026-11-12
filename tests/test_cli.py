"""Tests voor de terminal-commando's, tegen de nep-iPhone."""

import pytest

from ghost import paths
from ghost.cli import EXIT_OK, EXIT_PROBLEM, EXIT_USAGE, main
from ghost.device.base import Problem
from ghost.device.fake import FakeDevice
from ghost.geo import LatLon


def test_status_alles_in_orde(capsys):
    assert main(["status"], device=FakeDevice()) == EXIT_OK
    uitvoer = capsys.readouterr().out
    assert "[OK] iPhone verbonden: Demo-iPhone" in uitvoer
    assert "[OK] Locatiedienst bereikbaar" in uitvoer
    assert "Alles in orde" in uitvoer
    assert "[X]" not in uitvoer


@pytest.mark.parametrize(
    ("probleem", "verwachte_tekst"),
    [
        (Problem.APPLE_SERVICE_MISSING, "USB-service"),
        (Problem.NO_DEVICE, "Geen iPhone gevonden"),
        (Problem.DEVICE_LOCKED, "vergrendeld"),
        (Problem.NOT_TRUSTED, "vertrouwt deze laptop nog niet"),
        (Problem.DEVELOPER_MODE_OFF, "Developer Mode staat uit"),
        (Problem.TUNNEL_FAILED, "kwam niet tot stand"),
        (Problem.LOCATION_SERVICE_UNAVAILABLE, "biedt de locatiedienst niet aan"),
    ],
)
def test_status_legt_elk_probleem_uit(capsys, probleem, verwachte_tekst):
    assert main(["status"], device=FakeDevice(problem=probleem)) == EXIT_PROBLEM
    uitvoer = capsys.readouterr().out
    assert verwachte_tekst in uitvoer
    assert "Zo los je het op:" in uitvoer
    assert "Logbestand" in uitvoer


def test_te_oude_ios_geeft_de_afgesproken_melding(capsys):
    assert main(["status"], device=FakeDevice(ios_version="17.3.1")) == EXIT_PROBLEM
    assert "Update je iPhone naar iOS 17.4 of hoger." in capsys.readouterr().out


def test_developer_mode_uitleg_vraagt_nooit_om_de_toegangscode_te_verwijderen(capsys):
    main(["status"], device=FakeDevice(problem=Problem.DEVELOPER_MODE_OFF))
    assert "NIET te verwijderen" in capsys.readouterr().out


def test_set_zet_de_locatie_en_vraagt_daarna_de_echte_terug(capsys):
    toestel = FakeDevice()
    assert main(["set", "52.3676", "4.9041", "--seconds", "0"], device=toestel) == EXIT_OK
    assert toestel.history == [("set", LatLon(52.3676, 4.9041)), ("clear", None)]
    assert toestel.location is None
    uitvoer = capsys.readouterr().out
    assert "NEP-LOCATIE ACTIEF: 52.36760, 4.90410" in uitvoer
    assert "Echte locatie teruggevraagd" in uitvoer
    assert "Zet Developer Mode uit" in uitvoer


def test_set_begrijpt_coordinaten_met_een_komma_ertussen():
    toestel = FakeDevice()
    assert main(["set", "52.3676,", "4.9041", "--seconds", "0"], device=toestel) == EXIT_OK
    assert toestel.history[0] == ("set", LatLon(52.3676, 4.9041))


def test_set_begrijpt_negatieve_coordinaten():
    toestel = FakeDevice()
    assert main(["set", "-33.8688", "151.2093", "--seconds", "0"], device=toestel) == EXIT_OK
    assert toestel.history[0] == ("set", LatLon(-33.8688, 151.2093))


def test_set_weigert_ongeldige_coordinaten_zonder_het_toestel_aan_te_raken(capsys):
    toestel = FakeDevice()
    assert main(["set", "95", "4.9", "--seconds", "0"], device=toestel) == EXIT_USAGE
    assert toestel.history == []
    assert "breedtegraad" in capsys.readouterr().out


def test_set_doet_niets_als_het_toestel_niet_klaar_is(capsys):
    toestel = FakeDevice(problem=Problem.DEVELOPER_MODE_OFF)
    assert main(["set", "52.3676", "4.9041", "--seconds", "0"], device=toestel) == EXIT_PROBLEM
    assert toestel.history == []


def test_no_clear_laat_de_nep_locatie_staan_en_zegt_dat_duidelijk(capsys):
    toestel = FakeDevice()
    assert main(["set", "52.3676", "4.9041", "--seconds", "0", "--no-clear"], device=toestel) == EXIT_OK
    assert toestel.location == LatLon(52.3676, 4.9041)
    assert "NIET teruggevraagd" in capsys.readouterr().out


def test_als_zetten_mislukt_wordt_toch_geprobeerd_de_echte_locatie_terug_te_vragen(capsys):
    """Veiligheid eerst: bij twijfel altijd clear proberen."""
    toestel = FakeDevice()
    toestel.fail_next = Problem.CONNECTION_LOST
    assert main(["set", "52.3676", "4.9041", "--seconds", "0"], device=toestel) == EXIT_PROBLEM
    assert toestel.history == [("clear", None)]
    assert "verbinding met de iPhone is weggevallen" in capsys.readouterr().out


def test_clear_zonder_actieve_nep_locatie_werkt_gewoon(capsys):
    toestel = FakeDevice()
    assert main(["clear"], device=toestel) == EXIT_OK
    assert toestel.history == [("clear", None)]


def test_als_clear_mislukt_staat_er_een_duidelijke_waarschuwing(capsys):
    toestel = FakeDevice()
    toestel.fail_next = Problem.CONNECTION_LOST
    assert main(["clear"], device=toestel) == EXIT_PROBLEM
    uitvoer = capsys.readouterr().out
    assert "kon NIET worden teruggevraagd" in uitvoer
    assert "mogelijk nog op de nep-locatie" in uitvoer


def test_demo_vlag_meldt_dat_er_een_nep_iphone_wordt_gebruikt(capsys):
    assert main(["--demo", "status"]) == EXIT_OK
    assert "[DEMO]" in capsys.readouterr().out


def test_log_bevat_alleen_afgeronde_coordinaten():
    main(["set", "52.367612", "4.904139", "--seconds", "0"], device=FakeDevice())
    log = paths.log_file().read_text(encoding="utf-8")
    assert "52.37, 4.90" in log
    assert "52.3676" not in log
    assert "4.9041" not in log


def test_log_bevat_exacte_coordinaten_alleen_in_debugmodus():
    main(["--debug", "set", "52.367612", "4.904139", "--seconds", "0"], device=FakeDevice())
    log = paths.log_file().read_text(encoding="utf-8")
    assert "52.367612, 4.904139" in log
    assert "DEBUGMODUS" in log


def test_kapot_scherm_houdt_het_terugzetten_niet_tegen(monkeypatch):
    """Als er niets meer getoond kan worden (venster dicht), gaat het clear-verzoek toch de deur uit."""

    def kapotte_print(*args, **kwargs):
        raise BrokenPipeError("venster is dicht")

    monkeypatch.setattr("builtins.print", kapotte_print)
    toestel = FakeDevice()
    assert main(["set", "52.3676", "4.9041", "--seconds", "0"], device=toestel) == EXIT_OK
    assert toestel.history == [("set", LatLon(52.3676, 4.9041)), ("clear", None)]
