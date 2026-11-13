"""Tests voor routes en adres zoeken via OpenStreetMap.

Er gaat hier NIETS naar internet: de functie die normaal ophaalt, is vervangen
door een nagemaakte. Zo testen we onze eigen logica en de spelregels.
"""

import asyncio
import time

import pytest

from ghost.geo import LatLon
from ghost.net import NetError, Throttle, fetch_json
from ghost.osm import MAX_ROAD_ROUTE_KM, OsmError, RoutingClient, SearchClient

A = LatLon(52.3676, 4.9041)
B = LatLon(52.3702, 4.8952)


class NepInternet:
    def __init__(self, antwoord):
        self.antwoord = antwoord
        self.adressen = []

    async def __call__(self, url):
        self.adressen.append(url)
        if isinstance(self.antwoord, Exception):
            raise self.antwoord
        return self.antwoord


def route_antwoord(punten=((4.9041, 52.3676), (4.9, 52.369), (4.8952, 52.3702)), afstand=821.6):
    return {"code": "Ok", "routes": [{"distance": afstand, "geometry": {"coordinates": [list(p) for p in punten]}}]}


# --- routes --------------------------------------------------------------


async def test_route_over_de_weg():
    internet = NepInternet(route_antwoord())
    route = await RoutingClient(internet, min_interval=0).route([A, B], "foot")
    assert route.distance_m == 821.6
    assert route.points[0] == LatLon(52.3676, 4.9041)  # de server geeft lengtegraad eerst; wij draaien het om
    assert len(route.points) == 3
    adres = internet.adressen[0]
    assert adres.startswith("https://routing.openstreetmap.de/routed-foot/route/v1/driving/")
    assert "4.904100,52.367600;4.895200,52.370200" in adres  # lengtegraad,breedtegraad
    assert "overview=full" in adres and "geometries=geojson" in adres


@pytest.mark.parametrize(("vervoer", "deel"), [("foot", "routed-foot"), ("bike", "routed-bike"), ("car", "routed-car")])
async def test_elk_vervoer_heeft_zijn_eigen_server(vervoer, deel):
    internet = NepInternet(route_antwoord())
    await RoutingClient(internet, min_interval=0).route([A, B], vervoer)
    assert f"/{deel}/" in internet.adressen[0]


async def test_onbekend_vervoer_en_te_weinig_punten_gaan_niet_naar_internet():
    internet = NepInternet(route_antwoord())
    client = RoutingClient(internet, min_interval=0)
    with pytest.raises(OsmError):
        await client.route([A, B], "vliegtuig")
    with pytest.raises(OsmError):
        await client.route([A], "foot")
    with pytest.raises(OsmError):
        await client.route([A] * 26, "foot")
    assert internet.adressen == []


async def test_te_lange_route_wordt_niet_aan_de_gratis_server_gevraagd():
    internet = NepInternet(route_antwoord())
    new_york = LatLon(40.7128, -74.0060)
    with pytest.raises(OsmError) as fout:
        await RoutingClient(internet, min_interval=0).route([A, new_york], "car")
    assert str(MAX_ROAD_ROUTE_KM) in fout.value.message
    assert internet.adressen == []


@pytest.mark.parametrize(
    ("antwoord", "tekst"),
    [
        ({"code": "NoRoute"}, "geen route"),
        (NetError("HTTP 400", 400, {"code": "NoRoute", "message": "Impossible route"}), "geen route"),
        ({"code": "NoSegment"}, "te ver van een weg"),
        ({"code": "InvalidQuery", "message": "x"}, "onbruikbaar"),
        ({"code": "Ok", "routes": []}, "onbruikbaar"),
        ({"code": "Ok", "routes": [{"distance": 5, "geometry": {"coordinates": [[4.9, 52.3]]}}]}, "onbruikbaar"),
        ({"code": "Ok", "routes": [{"distance": 5, "geometry": {"coordinates": [[4.9, 95], [4.9, 52]]}}]}, "onbruikbaar"),
        ("dit is geen woordenboek", "onbruikbaar"),
        (NetError("geen verbinding"), "niet bereikbaar"),
        (NetError("HTTP 503", 503, None), "niet bereikbaar"),
    ],
)
async def test_fouten_van_de_routeserver_worden_gewone_taal(antwoord, tekst):
    with pytest.raises(OsmError) as fout:
        await RoutingClient(NepInternet(antwoord), min_interval=0).route([A, B], "foot")
    assert tekst in fout.value.message
    assert "Rechte lijn" in fout.value.message  # er is altijd een uitweg


# --- adres zoeken ---------------------------------------------------------

DAM = [{"display_name": "Dam, Amsterdam, Nederland", "lat": "52.3731", "lon": "4.8926"}]


async def test_adres_zoeken():
    internet = NepInternet(DAM)
    plekken = await SearchClient(internet, min_interval=0).search("  Dam   Amsterdam ")
    assert [(p.name, p.lat, p.lon) for p in plekken] == [("Dam, Amsterdam, Nederland", 52.3731, 4.8926)]
    adres = internet.adressen[0]
    assert adres.startswith("https://nominatim.openstreetmap.org/search?")
    assert "q=Dam+Amsterdam" in adres and "format=jsonv2" in adres and "limit=5" in adres


async def test_dezelfde_zoekvraag_gaat_maar_een_keer_naar_internet():
    """Het beleid van de zoekdienst vraagt om resultaten te bewaren."""
    internet = NepInternet(DAM)
    client = SearchClient(internet, min_interval=0)
    await client.search("Dam Amsterdam")
    await client.search("dam  amsterdam")
    assert len(internet.adressen) == 1


async def test_lege_of_te_lange_zoektekst_gaat_niet_naar_internet():
    internet = NepInternet(DAM)
    client = SearchClient(internet, min_interval=0)
    for tekst in ("", "   ", "x" * 201):
        with pytest.raises(OsmError):
            await client.search(tekst)
    assert internet.adressen == []


async def test_onbruikbare_resultaten_worden_overgeslagen():
    rommel = [{"display_name": "Zonder plek"}, {"display_name": "Fout", "lat": "abc", "lon": "1"}, *DAM, "tekst"]
    plekken = await SearchClient(NepInternet(rommel), min_interval=0).search("iets")
    assert [p.name for p in plekken] == ["Dam, Amsterdam, Nederland"]
    assert await SearchClient(NepInternet({"geen": "lijst"}), min_interval=0).search("iets") == []


@pytest.mark.parametrize(
    ("fout", "tekst"),
    [(NetError("HTTP 429", 429), "weigert"), (NetError("HTTP 403", 403), "weigert"), (NetError("weg"), "niet bereikbaar")],
)
async def test_fouten_van_de_zoekdienst_worden_gewone_taal(fout, tekst):
    with pytest.raises(OsmError) as gevangen:
        await SearchClient(NepInternet(fout), min_interval=0).search("Dam")
    assert tekst in gevangen.value.message


# --- spelregels -----------------------------------------------------------


async def test_hooguit_een_verzoek_per_seconde_ook_als_er_tegelijk_geklikt_wordt():
    momenten = []

    async def ophalen(url):
        momenten.append(time.monotonic())
        return route_antwoord()

    client = RoutingClient(ophalen, min_interval=0.15)
    await asyncio.gather(*(client.route([A, B], "foot") for _ in range(3)))
    assert momenten[1] - momenten[0] >= 0.14
    assert momenten[2] - momenten[1] >= 0.14


async def test_rem_werkt_ook_na_een_fout():
    rem = Throttle(0.1)
    with pytest.raises(RuntimeError):
        async with rem:
            raise RuntimeError("mislukt")
    start = time.monotonic()
    async with rem:
        pass
    assert time.monotonic() - start >= 0.09


async def test_alleen_https_wordt_opgehaald():
    with pytest.raises(NetError):
        await fetch_json("http://example.org/onveilig")
    with pytest.raises(NetError):
        await fetch_json("file:///etc/passwd")


def test_ghost_maakt_zich_bekend_zonder_gegevens_van_de_gebruiker():
    from ghost import net

    assert net.USER_AGENT.startswith("Ghost-locatiesimulator/")
    assert "@" not in net.USER_AGENT and "github" not in net.USER_AGENT.lower()
