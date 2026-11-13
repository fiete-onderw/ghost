"""Tests die de webserver ECHT starten (op een vrije poort) en weer stoppen.

Hier draait het om de volgorde bij het afsluiten: wat er ook gebeurt, eerst
gaat het clear-verzoek naar het toestel.
"""

import asyncio
import re
import socket

import httpx
import pytest
import uvicorn

from ghost import paths
from ghost.cli import EXIT_OK, EXIT_PROBLEM, build_parser
from ghost.device.fake import FakeDevice
from ghost.geo import LatLon
from ghost.safety import Reason
from ghost.web import server as server_module
from ghost.web.server import GhostServer, StopRequest, bind_local, ghost_is_running, serve

PARIJS = {"lat": 48.8584, "lon": 2.2945, "minutes": 5}


@pytest.fixture(autouse=True)
def zonder_startpauze(monkeypatch):
    """In het echt wacht Ghost een seconde met de eerste controle; in de tests hoeft dat niet."""
    monkeypatch.setattr(server_module, "STARTUP_DELAY_S", 0.0)


def vrije_poort():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Draaiend:
    """Een Ghost die echt draait, met een 'browser' (client) die het token al uit de pagina heeft gehaald."""

    def __init__(self, toestel=None):
        self.toestel = toestel or FakeDevice()
        self.poort = vrije_poort()
        self.stop = StopRequest()
        self.taak = None
        self.client = None

    async def __aenter__(self):
        self.taak = asyncio.create_task(
            serve(
                device=self.toestel,
                demo=True,
                debug=False,
                port=self.poort,
                browser=False,
                log_path=paths.log_file(),
                stop=self.stop,
            )
        )
        basis = f"http://127.0.0.1:{self.poort}"
        self.client = httpx.AsyncClient(base_url=basis, trust_env=False, timeout=5)
        for _ in range(200):
            try:
                pagina = await self.client.get("/")
                break
            except httpx.TransportError:
                await asyncio.sleep(0.02)
        else:
            raise AssertionError("de server kwam niet op gang")
        token = re.search(r'name="ghost-token" content="([^"]+)"', pagina.text).group(1)
        self.client.headers["X-Ghost-Token"] = token
        return self

    async def __aexit__(self, *fout):
        await self.client.aclose()
        if not self.taak.done():
            self.stop.request(Reason.QUIT)
            await asyncio.wait({self.taak}, timeout=10)

    async def wacht_op_klaar(self):
        for _ in range(200):
            beeld = (await self.client.get("/api/state")).json()["state"]
            if beeld["device"]["ready"] and beeld["simulation"]["clean_start_done"]:
                return beeld
            await asyncio.sleep(0.02)
        raise AssertionError("de iPhone werd niet klaar")


def test_de_poort_wordt_alleen_op_de_eigen_laptop_geopend():
    poort = vrije_poort()
    sock = bind_local(poort)
    try:
        assert sock.getsockname() == ("127.0.0.1", poort)
        with pytest.raises(OSError):
            bind_local(poort)  # een tweede Ghost krijgt dezelfde poort niet
    finally:
        sock.close()


def test_server_luistert_nooit_op_alle_netwerkadressen():
    assert server_module.HOST == "127.0.0.1"


def test_uvicorn_heeft_nog_de_functie_die_wij_vervangen():
    """Ghost schakelt uvicorn's eigen Ctrl+C-afhandeling uit. Verandert uvicorn, dan moet deze test dat melden."""
    assert "capture_signals" in vars(uvicorn.Server)
    assert "capture_signals" in vars(GhostServer)


def test_web_commando_en_poortcontrole():
    assert build_parser().parse_args(["web"]).port == 8765
    assert build_parser().parse_args(["web", "--port", "9000", "--no-browser"]).no_browser is True
    for fout in ("80", "70000", "abc"):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["web", "--port", fout])


async def test_ctrl_c_met_actieve_nep_locatie_vraagt_eerst_de_echte_terug(capsys):
    async with Draaiend() as ghost:
        await ghost.wacht_op_klaar()
        assert (await ghost.client.post("/api/location", json=PARIJS)).status_code == 200
        assert ghost.toestel.location == LatLon(48.8584, 2.2945)

        ghost.stop.request(Reason.CTRL_C)
        code = await asyncio.wait_for(ghost.taak, 10)

    assert code == EXIT_OK
    assert ghost.toestel.location is None
    assert ghost.toestel.history[-1] == ("clear", None)
    assert not ghost.toestel.connected  # verbinding netjes gesloten, NA het terugvragen
    uitvoer = capsys.readouterr().out
    assert "Ghost stopt (Ctrl+C)" in uitvoer
    assert "Echte locatie teruggevraagd" in uitvoer
    assert ghost_is_running(ghost.poort) is False


async def test_onverwacht_afbreken_vraagt_ook_de_echte_locatie_terug():
    """Wat er ook misgaat in de app zelf: het finally-blok draait."""
    async with Draaiend() as ghost:
        await ghost.wacht_op_klaar()
        await ghost.client.post("/api/location", json=PARIJS)
        ghost.taak.cancel()
        await asyncio.wait({ghost.taak}, timeout=10)
    assert ghost.toestel.location is None
    assert ghost.toestel.history[-1] == ("clear", None)


async def test_knop_ghost_afsluiten_stopt_de_server(capsys):
    async with Draaiend() as ghost:
        await ghost.wacht_op_klaar()
        await ghost.client.post("/api/location", json=PARIJS)
        antwoord = await ghost.client.post("/api/quit")
        assert antwoord.json()["restored"] is True
        code = await asyncio.wait_for(ghost.taak, 10)
    assert code == EXIT_OK
    assert ghost.toestel.history.count(("clear", None)) == 2  # schone start + afsluiten, niet dubbel
    assert "Echte locatie teruggevraagd" in capsys.readouterr().out


async def test_afsluiten_zonder_dat_er_iets_actief_was(capsys):
    async with Draaiend() as ghost:
        await ghost.wacht_op_klaar()
        ghost.stop.request(Reason.CTRL_C)
        code = await asyncio.wait_for(ghost.taak, 10)
    assert code == EXIT_OK
    assert ghost.toestel.history == [("clear", None)]  # alleen de schone start
    assert "Er was geen nep-locatie actief" in capsys.readouterr().out


async def test_afsluiten_terwijl_terugvragen_niet_lukt_waarschuwt_luid(capsys):
    async with Draaiend() as ghost:
        await ghost.wacht_op_klaar()
        await ghost.client.post("/api/location", json=PARIJS)
        ghost.toestel.unplug()
        ghost.stop.request(Reason.CTRL_C)
        code = await asyncio.wait_for(ghost.taak, 10)
    assert code == EXIT_PROBLEM
    uitvoer = capsys.readouterr().out
    assert "kon NIET worden teruggevraagd" in uitvoer
    assert "Start Ghost opnieuw" in uitvoer


async def test_echte_server_weigert_verkeerde_host_en_ontbrekend_token():
    async with Draaiend() as ghost:
        assert (await ghost.client.get("/api/state", headers={"X-Ghost-Token": "fout"})).status_code == 401
        assert (await ghost.client.get("/", headers={"Host": "evil.example"})).status_code == 403
        antwoord = await ghost.client.get("/api/hello")
        assert "server" not in antwoord.headers  # geen reclame voor de gebruikte software
        assert await asyncio.to_thread(ghost_is_running, ghost.poort) is True


async def test_tweede_ghost_start_niet_maar_verwijst_naar_de_eerste(capsys):
    async with Draaiend() as ghost:
        code = await serve(
            device=FakeDevice(), demo=True, debug=False, port=ghost.poort, browser=False, log_path=paths.log_file()
        )
        assert code == EXIT_OK
        assert "Ghost draait al" in capsys.readouterr().out


async def test_poort_bezet_door_een_ander_programma_geeft_duidelijke_uitleg(capsys):
    with socket.socket() as bezet:
        bezet.bind(("127.0.0.1", 0))
        bezet.listen()
        poort = bezet.getsockname()[1]
        toestel = FakeDevice()
        code = await serve(device=toestel, demo=True, debug=False, port=poort, browser=False, log_path=paths.log_file())
    assert code == EXIT_PROBLEM
    assert "al in gebruik" in capsys.readouterr().out
    assert toestel.history == []
