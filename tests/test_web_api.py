"""Tests voor de adressen waar de webpagina mee praat.

De test doet alsof hij een browser is en praat rechtstreeks met de app,
zonder dat er echt een server op een poort draait.
"""

import re

import httpx
import pytest

from ghost import paths
from ghost.controller import Controller
from ghost.device.base import Problem
from ghost.device.fake import FakeDevice
from ghost.geo import LatLon
from ghost.osm import OsmError, Place, RoadRoute
from ghost.safety import SafetyManager
from ghost.store import Favorites, Settings
from ghost.web.app import STATIC_DIR, create_app

TOKEN = "test-token-0123456789"
BASIS = "http://127.0.0.1:8765"


class NepRouteserver:
    """Doet alsof hij de routeserver is, zodat de tests niets naar internet sturen."""

    def __init__(self):
        self.vragen = []

    async def route(self, waypoints, profile):
        self.vragen.append(profile)
        return RoadRoute(list(waypoints), 0.0)


class NepZoekdienst:
    def __init__(self):
        self.vragen = []
        self.fout = None

    async def search(self, query):
        self.vragen.append(query)
        if self.fout:
            raise OsmError(self.fout)
        return [Place("Dam, Amsterdam, Nederland", 52.3731, 4.8926)]


class Opstelling:
    def __init__(self, toestel=None, **client_opties):
        self.toestel = toestel or FakeDevice()
        self.routeserver = NepRouteserver()
        self.zoekdienst = NepZoekdienst()
        self.controller = Controller(
            SafetyManager(self.toestel), log_path=paths.log_file(), routing=self.routeserver
        )
        self.gestopt = []
        self.favorieten = Favorites(paths.favorites_file())
        self.instellingen = Settings(paths.settings_file())
        self.app = create_app(
            self.controller,
            token=TOKEN,
            port=8765,
            on_quit=lambda: self.gestopt.append(True),
            favorites=self.favorieten,
            settings=self.instellingen,
            search=self.zoekdienst,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, **client_opties),
            base_url=BASIS,
            headers={"X-Ghost-Token": TOKEN},
        )


@pytest.fixture
async def ghost():
    opstelling = Opstelling()
    await opstelling.controller.refresh_status()
    yield opstelling
    await opstelling.client.aclose()


# --- de deurwachter in het echt -------------------------------------------


async def test_de_pagina_bevat_het_token_en_strenge_headers(ghost):
    antwoord = await ghost.client.get("/", headers={"X-Ghost-Token": ""})
    assert antwoord.status_code == 200
    assert f'<meta name="ghost-token" content="{TOKEN}">' in antwoord.text
    assert "{{GHOST_TOKEN}}" not in antwoord.text
    assert "script-src 'self'" in antwoord.headers["content-security-policy"]
    assert antwoord.headers["x-frame-options"] == "DENY"
    assert antwoord.headers["cache-control"] == "no-store"
    assert antwoord.headers["cross-origin-resource-policy"] == "same-origin"


async def test_geen_cors_ook_niet_als_een_andere_website_erom_vraagt(ghost):
    """Er komt nooit een 'Access-Control-Allow-...' header terug. Dat is wat 'geen CORS' betekent."""
    voorvraag = await ghost.client.options(
        "/api/location",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-ghost-token,content-type",
        },
    )
    assert voorvraag.status_code == 403
    eigen = await ghost.client.get("/api/state", headers={"Origin": BASIS})
    assert eigen.status_code == 200
    for antwoord in (voorvraag, eigen):
        assert not [naam for naam in antwoord.headers if naam.lower().startswith("access-control-")]


async def test_andere_website_kan_de_locatie_niet_zetten(ghost):
    antwoord = await ghost.client.post(
        "/api/location", json={"lat": 48.8584, "lon": 2.2945}, headers={"Origin": "https://evil.example"}
    )
    assert antwoord.status_code == 403
    assert ghost.toestel.location is None


async def test_dns_rebinding_wordt_geweigerd(ghost):
    for pad in ("/", "/api/state", "/static/app.js"):
        antwoord = await ghost.client.get(pad, headers={"Host": "evil.example:8765"})
        assert antwoord.status_code == 403
        assert TOKEN not in antwoord.text  # de pagina met het token wordt niet verstuurd


@pytest.mark.parametrize("token", ["", "fout-token"])
async def test_zonder_juist_token_gebeurt_er_niets(ghost, token):
    kop = {"X-Ghost-Token": token}
    assert (await ghost.client.get("/api/state", headers=kop)).status_code == 401
    antwoord = await ghost.client.post("/api/location", json={"lat": 48.8584, "lon": 2.2945}, headers=kop)
    assert antwoord.status_code == 401
    assert (await ghost.client.post("/api/stop", headers=kop)).status_code == 401
    assert (await ghost.client.post("/api/quit", headers=kop)).status_code == 401
    assert ghost.toestel.history == [("clear", None)]  # alleen de schone start
    assert ghost.gestopt == []


async def test_token_in_het_webadres_werkt_niet(ghost):
    antwoord = await ghost.client.get(f"/api/state?token={TOKEN}", headers={"X-Ghost-Token": ""})
    assert antwoord.status_code == 401


async def test_hello_werkt_zonder_token_en_verklapt_niets(ghost):
    antwoord = await ghost.client.get("/api/hello", headers={"X-Ghost-Token": ""})
    assert antwoord.json() == {"app": "ghost", "version": antwoord.json()["version"]}


async def test_automatische_documentatie_staat_uit(ghost):
    for pad in ("/docs", "/redoc", "/openapi.json"):
        assert (await ghost.client.get(pad)).status_code == 404


# --- de pagina en zijn bestanden ----------------------------------------


@pytest.mark.parametrize(
    "pad",
    [
        "/static/app.js",
        "/static/style.css",
        "/static/favicon.svg",
        "/static/vendor/leaflet/leaflet.js",
        "/static/vendor/leaflet/leaflet.css",
    ],
)
async def test_bestanden_van_de_pagina_worden_geleverd(ghost, pad):
    antwoord = await ghost.client.get(pad, headers={"X-Ghost-Token": ""})
    assert antwoord.status_code == 200
    assert antwoord.headers["x-content-type-options"] == "nosniff"


async def test_bestandstypen_kloppen_ook_als_windows_er_iets_anders_van_vindt(monkeypatch):
    """Op sommige Windows-laptops zegt het register dat .js platte tekst is. De browser weigert het script dan."""
    import mimetypes

    mimetypes.add_type("text/plain", ".js")  # boots zo'n laptop na
    opstelling = Opstelling()  # bij het maken van de app wordt het rechtgezet
    try:
        verwacht = {
            "/static/app.js": "text/javascript",
            "/static/vendor/leaflet/leaflet.js": "text/javascript",
            "/static/style.css": "text/css",
            "/static/favicon.svg": "image/svg+xml",
            "/static/vendor/leaflet/images/marker-icon.png": "image/png",
        }
        for pad, soort in verwacht.items():
            antwoord = await opstelling.client.get(pad)
            assert antwoord.headers["content-type"].split(";")[0] == soort, pad
    finally:
        await opstelling.client.aclose()


def test_de_pagina_laadt_niets_van_andere_websites_en_heeft_geen_inline_code():
    """Past bij de strenge regels (CSP): alleen eigen bestanden, geen code of stijl in de HTML zelf."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    # Alles wat de browser vanzelf inlaadt (scripts, stijlen, plaatjes) moet van Ghost zelf komen.
    geladen = re.findall(r'<(?:script|img|iframe)\b[^>]*\bsrc="([^"]+)"', html)
    geladen += re.findall(r'<link\b[^>]*\bhref="([^"]+)"', html)
    assert len(geladen) >= 4
    for verwijzing in geladen:
        assert verwijzing.startswith("/static/"), verwijzing
        assert (STATIC_DIR / verwijzing.removeprefix("/static/")).is_file(), verwijzing
    # Gewone links naar buiten mogen, maar alleen als je er zelf op klikt, in een
    # nieuw tabblad, en zonder dat de andere website iets over deze pagina te weten komt.
    for link in re.findall(r"<a\b[^>]*>", html):
        assert 'href="https://' in link, link
        assert 'target="_blank"' in link and 'rel="noopener noreferrer"' in link, link
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert "<style" not in html
    assert not re.search(r'\sstyle="', html)
    assert not re.search(r"\son[a-z]+=", html)


def test_het_script_toont_tekst_nooit_als_html():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in script
    assert "outerHTML" not in script
    assert "document.write" not in script
    assert "eval(" not in script
    assert "token=" not in script  # het token gaat nooit in een webadres


def test_leaflet_is_de_onveranderde_versie_met_licentie():
    map_ = STATIC_DIR / "vendor" / "leaflet"
    assert "Leaflet 1.9.4" in (map_ / "leaflet.js").read_text(encoding="utf-8")[:200]
    assert "BSD 2-Clause" in (map_ / "LICENSE").read_text(encoding="utf-8")


# --- locatie zetten en stoppen ------------------------------------------


async def test_stand_van_zaken(ghost):
    beeld = (await ghost.client.get("/api/state")).json()["state"]
    assert beeld["device"]["ready"] is True
    assert beeld["device"]["name"] == "Demo-iPhone"
    assert beeld["simulation"]["active"] is False
    assert beeld["simulation"]["clean_start_done"] is True
    assert beeld["timer"]["default"] == 30


async def test_locatie_zetten_en_stoppen(ghost):
    antwoord = await ghost.client.post("/api/location", json={"lat": 48.8584, "lon": 2.2945, "minutes": 15})
    assert antwoord.status_code == 200
    beeld = antwoord.json()["state"]["simulation"]
    assert beeld["active"] is True
    assert beeld["text"] == "48.85840, 2.29450"
    assert beeld["remaining_s"] == 900
    assert ghost.toestel.location == LatLon(48.8584, 2.2945)

    antwoord = await ghost.client.post("/api/stop")
    assert antwoord.status_code == 200
    assert antwoord.json()["confirmed"] is True
    assert antwoord.json()["state"]["simulation"]["active"] is False
    assert ghost.toestel.location is None


async def test_stoppen_mag_altijd_ook_als_er_niets_actief_is(ghost):
    assert (await ghost.client.post("/api/stop")).status_code == 200
    assert ghost.toestel.history == [("clear", None), ("clear", None)]


@pytest.mark.parametrize(
    "inhoud",
    [
        {"lat": 95, "lon": 4.9},
        {"lat": 52.3, "lon": 181},
        {"lat": "abc", "lon": 4.9},
        {"lat": 52.3},
        {"lat": 52.3, "lon": 4.9, "minutes": 0},
        {"lat": 52.3, "lon": 4.9, "minutes": 100000},
        {},
    ],
)
async def test_ongeldige_invoer_raakt_het_toestel_niet(ghost, inhoud):
    antwoord = await ghost.client.post("/api/location", json=inhoud)
    assert antwoord.status_code in (400, 409)
    assert antwoord.json()["error"]
    assert ghost.toestel.location is None


async def test_onzin_in_plaats_van_json_geeft_een_nette_fout(ghost):
    antwoord = await ghost.client.post(
        "/api/location", content=b"dit is geen json", headers={"Content-Type": "application/json"}
    )
    assert antwoord.status_code == 400
    assert ghost.toestel.location is None


async def test_iphone_niet_klaar_geeft_uitleg_met_de_juiste_knop():
    opstelling = Opstelling(FakeDevice(problem=Problem.DEVELOPER_MODE_OFF))
    await opstelling.controller.refresh_status()
    antwoord = await opstelling.client.post("/api/location", json={"lat": 48.8584, "lon": 2.2945})
    assert antwoord.status_code == 409
    assert antwoord.json()["problem"]["action"]["id"] == "reveal"
    assert opstelling.toestel.history == []
    await opstelling.client.aclose()


async def test_mislukt_stoppen_wordt_eerlijk_gemeld(ghost):
    await ghost.client.post("/api/location", json={"lat": 48.8584, "lon": 2.2945})
    ghost.toestel.unplug()
    antwoord = await ghost.client.post("/api/stop")
    assert antwoord.status_code == 409
    assert "NIET" in antwoord.json()["error"]
    assert antwoord.json()["state"]["simulation"]["uncertain"] is True


async def test_coordinaten_uit_tekst(ghost):
    goed = await ghost.client.post("/api/parse", json={"text": "52,3676; 4,9041"})
    assert goed.json() == {"ok": True, "lat": 52.3676, "lon": 4.9041, "text": "52.36760, 4.90410"}
    fout = await ghost.client.post("/api/parse", json={"text": "ergens in Parijs"})
    assert fout.status_code == 400
    assert "begrijp" in fout.json()["error"]


async def test_timer_verlengen(ghost):
    await ghost.client.post("/api/location", json={"lat": 48.8584, "lon": 2.2945, "minutes": 5})
    antwoord = await ghost.client.post("/api/timer", json={"add_minutes": 15})
    assert antwoord.json()["state"]["simulation"]["remaining_s"] == 20 * 60
    await ghost.client.post("/api/stop")
    assert (await ghost.client.post("/api/timer", json={"add_minutes": 15})).status_code == 409


# --- statuspaneel -------------------------------------------------------


async def test_opnieuw_controleren_en_koppelen():
    opstelling = Opstelling(FakeDevice(problem=Problem.NOT_TRUSTED))
    antwoord = await opstelling.client.post("/api/device/check")
    assert antwoord.json()["state"]["device"]["problem"]["action"]["id"] == "pair"
    antwoord = await opstelling.client.post("/api/device/pair")
    assert antwoord.status_code == 200
    assert antwoord.json()["state"]["device"]["ready"] is True
    assert (await opstelling.client.post("/api/device/formatteren")).status_code == 409
    await opstelling.client.aclose()


# --- afsluiten en onverwachte fouten ------------------------------------


async def test_afsluiten_vraagt_eerst_de_echte_locatie_terug(ghost):
    await ghost.client.post("/api/location", json={"lat": 48.8584, "lon": 2.2945})
    antwoord = await ghost.client.post("/api/quit")
    assert antwoord.json()["restored"] is True
    assert antwoord.json()["needs_restore"] is False
    assert ghost.toestel.location is None
    assert ghost.gestopt == [True]
    # Daarna kan er niets meer gezet worden.
    assert (await ghost.client.post("/api/location", json={"lat": 1, "lon": 2})).status_code == 409


async def test_onverwachte_fout_vraagt_voor_de_zekerheid_de_echte_locatie_terug():
    opstelling = Opstelling(raise_app_exceptions=False)
    await opstelling.controller.refresh_status()
    await opstelling.client.post("/api/location", json={"lat": 48.8584, "lon": 2.2945})

    def kapot(add_minutes):
        raise RuntimeError("een programmeerfout")

    opstelling.controller.extend_timer = kapot
    antwoord = await opstelling.client.post("/api/timer", json={"add_minutes": 15})
    assert antwoord.status_code == 500
    assert "teruggevraagd" in antwoord.json()["error"]
    assert "programmeerfout" not in antwoord.text  # technische details alleen in het log
    assert opstelling.toestel.location is None
    await opstelling.client.aclose()


async def test_log_bevat_alleen_afgeronde_coordinaten(ghost):
    from ghost.logging_setup import setup_logging

    setup_logging()
    await ghost.client.post("/api/location", json={"lat": 48.858412, "lon": 2.294512})
    await ghost.client.post("/api/stop")
    log = paths.log_file().read_text(encoding="utf-8")
    assert "48.86, 2.29" in log
    assert "48.8584" not in log and "2.2945" not in log


# --- routes ---------------------------------------------------------------

LIJN = {"waypoints": [[52.0, 5.0], [52.002, 5.0]], "mode": "line"}


async def test_route_plannen_starten_pauzeren_en_stoppen(ghost):
    antwoord = await ghost.client.post("/api/route/plan", json=LIJN)
    assert antwoord.status_code == 200
    plan = antwoord.json()["plan"]
    assert plan["path"] == [[52.0, 5.0], [52.002, 5.0]]
    assert 200 < plan["length_m"] < 250
    assert ghost.toestel.location is None  # plannen zet nog niets

    antwoord = await ghost.client.post("/api/route/start", json={"plan_id": plan["id"], "speed_kmh": 5, "minutes": 15})
    assert antwoord.status_code == 200
    beeld = antwoord.json()["state"]["simulation"]
    assert beeld["active"] is True and beeld["route"]["speed_kmh"] == 5
    assert ghost.toestel.location == LatLon(52.0, 5.0)

    assert (await ghost.client.get("/api/route/path")).json()["path"] == plan["path"]
    assert (await ghost.client.post("/api/route/pause")).json()["state"]["simulation"]["route"]["paused"] is True
    assert (await ghost.client.post("/api/route/resume")).json()["state"]["simulation"]["route"]["paused"] is False
    snel = await ghost.client.post("/api/route/speed", json={"speed_kmh": 15})
    assert snel.json()["state"]["simulation"]["route"]["speed_kmh"] == 15

    gestopt = await ghost.client.post("/api/stop")
    assert gestopt.json()["state"]["simulation"]["route"] is None
    assert ghost.toestel.location is None
    assert (await ghost.client.post("/api/route/pause")).status_code == 409
    assert (await ghost.client.get("/api/route/path")).status_code == 409


async def test_route_over_de_weg_gebruikt_de_routeserver_met_het_gekozen_vervoer(ghost):
    antwoord = await ghost.client.post("/api/route/plan", json={**LIJN, "mode": "bike", "round_trip": True})
    assert antwoord.status_code == 200
    assert ghost.routeserver.vragen == ["bike"]
    assert len(antwoord.json()["plan"]["path"]) == 3  # heen en weer terug


@pytest.mark.parametrize(
    "inhoud",
    [
        {"waypoints": [[52.0, 5.0]], "mode": "line"},
        {"waypoints": [[95.0, 5.0], [52.0, 5.0]], "mode": "line"},
        {"waypoints": [[52.0, 5.0], [52.002, 5.0]], "mode": "vliegen"},
        {"waypoints": [[52.0, 5.0 + i / 1000] for i in range(26)], "mode": "line"},
        {"waypoints": "geen lijst", "mode": "line"},
        {"mode": "line"},
    ],
)
async def test_onmogelijke_route_wordt_netjes_geweigerd(ghost, inhoud):
    antwoord = await ghost.client.post("/api/route/plan", json=inhoud)
    assert antwoord.status_code in (400, 409)
    assert antwoord.json()["error"]


@pytest.mark.parametrize("inhoud", [{"plan_id": 12345, "speed_kmh": 5}, {"plan_id": 1, "speed_kmh": 500}, {"speed_kmh": 5}])
async def test_route_start_niet_met_onzin(ghost, inhoud):
    await ghost.client.post("/api/route/plan", json=LIJN)
    antwoord = await ghost.client.post("/api/route/start", json=inhoud)
    assert antwoord.status_code in (400, 409)
    assert ghost.toestel.location is None


async def test_verschuiven_via_de_api(ghost):
    await ghost.client.post("/api/location", json={"lat": 52.0, "lon": 5.0})
    antwoord = await ghost.client.post("/api/nudge", json={"direction": "n", "meters": 100})
    assert antwoord.status_code == 200
    assert antwoord.json()["state"]["simulation"]["lat"] > 52.0
    assert (await ghost.client.post("/api/nudge", json={"direction": "x", "meters": 100})).status_code == 409
    assert (await ghost.client.post("/api/nudge", json={"direction": "n", "meters": 99999})).status_code == 409


# --- favorieten -----------------------------------------------------------


async def test_favorieten_bewaren_ophalen_en_verwijderen(ghost):
    assert (await ghost.client.get("/api/favorites")).json()["favorites"] == []
    antwoord = await ghost.client.post("/api/favorites", json={"name": "Eiffeltoren", "lat": 48.8584, "lon": 2.2945})
    assert antwoord.status_code == 200
    lijst = antwoord.json()["favorites"]
    assert [(f["name"], f["lat"], f["lon"]) for f in lijst] == [("Eiffeltoren", 48.8584, 2.2945)]
    assert antwoord.json()["state"]["favorites_version"] == 1
    assert Favorites(paths.favorites_file()).list() == lijst  # het staat echt op schijf

    weg = await ghost.client.delete(f"/api/favorites/{lijst[0]['id']}")
    assert weg.json()["favorites"] == []
    assert weg.json()["state"]["favorites_version"] == 2


@pytest.mark.parametrize(
    "inhoud", [{"name": "", "lat": 1, "lon": 2}, {"name": "X", "lat": 95, "lon": 2}, {"name": "X"}, {"name": "x" * 500, "lat": 1, "lon": 2}]
)
async def test_onbruikbare_favoriet_wordt_geweigerd(ghost, inhoud):
    antwoord = await ghost.client.post("/api/favorites", json=inhoud)
    assert antwoord.status_code == 400
    assert ghost.favorieten.list() == []


async def test_favorieten_hebben_ook_het_token_nodig(ghost):
    kop = {"X-Ghost-Token": "fout"}
    assert (await ghost.client.get("/api/favorites", headers=kop)).status_code == 401
    assert (await ghost.client.post("/api/favorites", json={"name": "X", "lat": 1, "lon": 2}, headers=kop)).status_code == 401


# --- adres zoeken ---------------------------------------------------------


async def test_adres_zoeken_staat_standaard_uit_en_stuurt_dan_niets_naar_internet(ghost):
    assert (await ghost.client.get("/api/state")).json()["state"]["settings"] == {"address_search": False}
    antwoord = await ghost.client.post("/api/search", json={"q": "Dam Amsterdam"})
    assert antwoord.status_code == 409
    assert "staat uit" in antwoord.json()["error"]
    assert ghost.zoekdienst.vragen == []


async def test_adres_zoeken_werkt_pas_na_bewust_aanzetten(ghost):
    aan = await ghost.client.post("/api/settings", json={"address_search": True})
    assert aan.json()["state"]["settings"]["address_search"] is True
    assert Settings(paths.settings_file()).address_search is True  # de keuze wordt onthouden

    antwoord = await ghost.client.post("/api/search", json={"q": "Dam Amsterdam"})
    assert antwoord.json()["places"] == [{"name": "Dam, Amsterdam, Nederland", "lat": 52.3731, "lon": 4.8926}]
    assert ghost.zoekdienst.vragen == ["Dam Amsterdam"]

    await ghost.client.post("/api/settings", json={"address_search": False})
    assert (await ghost.client.post("/api/search", json={"q": "Dam"})).status_code == 409
    assert ghost.zoekdienst.vragen == ["Dam Amsterdam"]


async def test_fout_van_de_zoekdienst_wordt_gewone_taal(ghost):
    await ghost.client.post("/api/settings", json={"address_search": True})
    ghost.zoekdienst.fout = "De zoekdienst is niet bereikbaar. Controleer je internet."
    antwoord = await ghost.client.post("/api/search", json={"q": "Dam"})
    assert antwoord.status_code == 502
    assert "niet bereikbaar" in antwoord.json()["error"]


async def test_onderbroken_route_hervatten_en_vergeten_via_de_pagina(ghost):
    from ghost.controller import InterruptedRoute

    assert (await ghost.client.post("/api/route/continue")).status_code == 409  # er is niets onderbroken

    plan = (await ghost.client.post("/api/route/plan", json=LIJN)).json()["plan"]
    echt_plan = ghost.controller._plans[plan["id"]]
    ghost.controller.interrupted = InterruptedRoute(echt_plan, 100.0, 5.0, 15)
    beeld = (await ghost.client.get("/api/state")).json()["state"]["simulation"]
    assert beeld["interrupted"]["travelled_m"] == 100.0

    antwoord = await ghost.client.post("/api/route/continue")
    assert antwoord.status_code == 200
    beeld = antwoord.json()["state"]["simulation"]
    assert beeld["active"] is True
    assert beeld["interrupted"] is None
    assert beeld["route"]["travelled_m"] == 100.0
    assert beeld["minutes"] == 15

    await ghost.client.post("/api/stop")
    ghost.controller.interrupted = InterruptedRoute(echt_plan, 50.0, 5.0, 15)
    antwoord = await ghost.client.post("/api/route/forget")
    assert antwoord.json()["state"]["simulation"]["interrupted"] is None
