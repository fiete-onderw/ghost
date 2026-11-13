"""Tests voor favorieten en instellingen."""

import json

import pytest

from ghost import paths
from ghost.geo import LatLon
from ghost.store import MAX_FAVORITES, Favorites, Settings, StoreError

DAM = LatLon(52.3731, 4.8926)


def test_favoriet_bewaren_en_terugvinden_na_een_herstart():
    favorieten = Favorites(paths.favorites_file())
    bewaard = favorieten.add("  De   Dam ", DAM)
    assert bewaard["name"] == "De Dam"
    assert favorieten.version == 1

    opnieuw = Favorites(paths.favorites_file())
    assert opnieuw.list() == [{"id": bewaard["id"], "name": "De Dam", "lat": 52.3731, "lon": 4.8926}]


def test_favoriet_verwijderen():
    favorieten = Favorites(paths.favorites_file())
    een = favorieten.add("Een", DAM)
    twee = favorieten.add("Twee", DAM)
    assert favorieten.remove(een["id"]) is True
    assert favorieten.remove("bestaat-niet") is False
    assert [item["id"] for item in Favorites(paths.favorites_file()).list()] == [twee["id"]]


def test_favoriet_zonder_naam_wordt_geweigerd_en_lange_naam_ingekort():
    favorieten = Favorites(None)
    with pytest.raises(StoreError):
        favorieten.add("   ", DAM)
    assert len(favorieten.add("x" * 500, DAM)["name"]) == 60


def test_er_is_een_maximum_aantal_favorieten():
    favorieten = Favorites(None)
    for nummer in range(MAX_FAVORITES):
        favorieten.add(f"Plek {nummer}", DAM)
    with pytest.raises(StoreError):
        favorieten.add("Een te veel", DAM)


def test_zonder_pad_wordt_er_niets_op_schijf_gezet():
    """Zo werkt de demomodus: je echte gegevens worden nooit aangeraakt."""
    Favorites(None).add("Demo", DAM)
    Settings(None).set_address_search(True)
    assert not paths.favorites_file().exists()
    assert not paths.settings_file().exists()


def test_kapot_bestand_wordt_opzij_gezet_en_niet_weggegooid():
    bestand = paths.favorites_file()
    bestand.parent.mkdir(parents=True, exist_ok=True)
    bestand.write_text("{dit is geen json", encoding="utf-8")
    assert Favorites(bestand).list() == []
    assert bestand.with_suffix(".json.kapot").read_text(encoding="utf-8") == "{dit is geen json"


def test_een_kapotte_regel_houdt_de_rest_niet_tegen():
    bestand = paths.favorites_file()
    bestand.parent.mkdir(parents=True, exist_ok=True)
    bestand.write_text(
        json.dumps(
            [
                {"id": "a", "name": "Goed", "lat": 52.0, "lon": 5.0},
                {"id": "b", "name": "Buiten de aarde", "lat": 999, "lon": 5.0},
                {"naam": "verkeerde velden"},
                "tekst",
            ]
        ),
        encoding="utf-8",
    )
    assert [item["name"] for item in Favorites(bestand).list()] == ["Goed"]


def test_adres_zoeken_staat_standaard_uit_en_de_keuze_wordt_onthouden():
    assert Settings(paths.settings_file()).address_search is False
    Settings(paths.settings_file()).set_address_search(True)
    assert Settings(paths.settings_file()).address_search is True
    assert Settings(paths.settings_file()).snapshot() == {"address_search": True}


def test_instellingenbestand_met_onzin_valt_terug_op_veilig():
    bestand = paths.settings_file()
    bestand.parent.mkdir(parents=True, exist_ok=True)
    bestand.write_text('["een lijst in plaats van instellingen"]', encoding="utf-8")
    assert Settings(bestand).address_search is False
