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
from ghost.safety import SafetyManager
from ghost.web.app import STATIC_DIR, create_app

TOKEN = "test-token-0123456789"
BASIS = "http://127.0.0.1:8765"


class Opstelling:
    def __init__(self, toestel=None, **client_opties):
        self.toestel = toestel or FakeDevice()
        self.controller = Controller(SafetyManager(self.toestel), log_path=paths.log_file())
        self.gestopt = []
        self.app = create_app(self.controller, token=TOKEN, port=8765, on_quit=lambda: self.gestopt.append(True))
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
    for verwijzing in re.findall(r'(?:src|href)="([^"]+)"', html):
        assert verwijzing.startswith("/static/"), verwijzing
        assert (STATIC_DIR / verwijzing.removeprefix("/static/")).is_file(), verwijzing
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
