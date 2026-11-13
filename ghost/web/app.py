"""De adressen waar de webpagina mee praat (bouwstap 5).

Elk adres doet één ding en geeft altijd de complete stand van zaken terug
(`state`), zodat de pagina zichzelf meteen kan bijwerken.

    GET  /                   de pagina zelf (met het sessietoken erin)
    GET  /api/hello          "hier draait Ghost" (geen token nodig)
    GET  /api/state          de stand van zaken
    POST /api/parse          tekst zoals "52.37, 4.90" omzetten naar coördinaten
    POST /api/location       een vaste nep-locatie zetten
    POST /api/stop           de echte locatie terugvragen
    POST /api/timer          de auto-stop-timer verlengen
    POST /api/device/check   de iPhone opnieuw controleren
    POST /api/device/{actie} koppelen (pair), Developer Mode tonen (reveal) of het image plaatsen (mount)
    POST /api/quit           Ghost afsluiten

Elk verzoek gaat eerst langs de deurwachter in security.py.
"""

from __future__ import annotations

import logging
import mimetypes
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ghost import __version__
from ghost.controller import Controller, ControllerError, problem_snapshot
from ghost.coords import CoordinateError, format_for_screen, parse_latlon, validate
from ghost.safety import Reason
from ghost.web.security import SECURITY_HEADERS, LocalGuard

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
TOKEN_PLACEHOLDER = "{{GHOST_TOKEN}}"

#: Welk soort bestand hoort bij welke extensie. Python vraagt dat op Windows
#: normaal aan het register, en daar staat op sommige laptops iets verkeerds
#: (bv. ".js is platte tekst"). De browser weigert zo'n script dan. Daarom
#: leggen we het hier zelf vast.
FILE_TYPES = {
    ".js": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".html": "text/html",
}


class LocationBody(BaseModel):
    lat: float
    lon: float
    minutes: int | None = None


class ParseBody(BaseModel):
    text: str = Field(max_length=200)


class TimerBody(BaseModel):
    add_minutes: int


def create_app(
    controller: Controller,
    *,
    token: str,
    port: int,
    on_quit: Callable[[], None] | None = None,
) -> FastAPI:
    for extension, file_type in FILE_TYPES.items():
        mimetypes.add_type(file_type, extension)

    # Geen automatische documentatiepagina's: die laden scripts van internet.
    app = FastAPI(title="Ghost", docs_url=None, redoc_url=None, openapi_url=None)
    guard = LocalGuard(port=port, token=token)

    # ------------------------------------------------------------------
    # De deurwachter: elk verzoek komt hier eerst langs.
    # Let op wat hier NIET staat: CORS. Andere websites krijgen nooit
    # toestemming om onze antwoorden te lezen.
    # ------------------------------------------------------------------

    @app.middleware("http")
    async def protect(request: Request, call_next: Callable[[Request], Any]) -> Response:
        refusal = guard.check(request.url.path, request.headers)
        if refusal is not None:
            log.warning("Verzoek geweigerd (%s %s): %s", request.method, request.url.path, refusal.message)
            response: Response = JSONResponse({"error": refusal.message}, status_code=refusal.status)
        else:
            response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response

    # ------------------------------------------------------------------
    # Foutafhandeling
    # ------------------------------------------------------------------

    def ok(**extra: Any) -> JSONResponse:
        return JSONResponse({"ok": True, **extra, "state": controller.snapshot()})

    def refused(error: ControllerError, status: int = 409) -> JSONResponse:
        problem = problem_snapshot(error.problem, error.detail) if error.problem else None
        return JSONResponse(
            {"ok": False, "error": error.message, "problem": problem, "state": controller.snapshot()},
            status_code=status,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {"ok": False, "error": "Ongeldig verzoek: er ontbreekt iets of er staat iets verkeerds in."},
            status_code=400,
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, error: Exception) -> JSONResponse:
        # Veiligheid eerst: weten we niet wat er misging, dan vragen we de
        # echte locatie terug als er (mogelijk) een nep-locatie actief is.
        log.error("Onverwachte fout bij %s %s", request.method, request.url.path, exc_info=error)
        message = "Er ging iets onverwachts mis. Technische details staan in het logbestand."
        if controller.safety.needs_restore:
            result = await controller.stop(Reason.ERROR)
            message += (
                " Voor de zekerheid is je echte locatie teruggevraagd."
                if result.ok
                else " Je echte locatie kon NIET worden teruggevraagd; Ghost blijft het proberen."
            )
        return JSONResponse({"ok": False, "error": message, "state": controller.snapshot()}, status_code=500)

    # ------------------------------------------------------------------
    # De pagina
    # ------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        # Het token wordt hier in de pagina gezet. Alleen wie de pagina mag
        # lezen (onze eigen pagina in jouw browser) kent het daarna.
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html.replace(TOKEN_PLACEHOLDER, token))

    @app.get("/api/hello")
    async def hello() -> dict[str, str]:
        return {"app": "ghost", "version": __version__}

    # ------------------------------------------------------------------
    # Stand van zaken en locatie
    # ------------------------------------------------------------------

    @app.get("/api/state")
    async def state() -> JSONResponse:
        return ok()

    @app.post("/api/parse")
    async def parse(body: ParseBody) -> JSONResponse:
        try:
            point = parse_latlon(body.text)
        except CoordinateError as error:
            return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
        return JSONResponse({"ok": True, "lat": point.lat, "lon": point.lon, "text": format_for_screen(point)})

    @app.post("/api/location")
    async def set_location(body: LocationBody) -> JSONResponse:
        try:
            point = validate(body.lat, body.lon)
        except CoordinateError as error:
            return refused(ControllerError(str(error)), status=400)
        try:
            await controller.set_fixed(point, body.minutes)
        except ControllerError as error:
            return refused(error)
        return ok()

    @app.post("/api/stop")
    async def stop() -> JSONResponse:
        result = await controller.stop(Reason.BUTTON)
        if not result.ok:
            problem = problem_snapshot(result.problem, result.detail) if result.problem else None
            return JSONResponse(
                {
                    "ok": False,
                    "error": "Je echte locatie kon NIET worden teruggevraagd. Ghost blijft het proberen.",
                    "problem": problem,
                    "state": controller.snapshot(),
                },
                status_code=409,
            )
        return ok(confirmed=result.confirmed)

    @app.post("/api/timer")
    async def timer(body: TimerBody) -> JSONResponse:
        try:
            controller.extend_timer(body.add_minutes)
        except ControllerError as error:
            return refused(error)
        return ok()

    # ------------------------------------------------------------------
    # Het statuspaneel
    # ------------------------------------------------------------------

    @app.post("/api/device/check")
    async def check() -> JSONResponse:
        await controller.refresh_status()
        return ok()

    @app.post("/api/device/{action}")
    async def device_action(action: str) -> JSONResponse:
        try:
            await controller.run_action(action)
        except ControllerError as error:
            return refused(error)
        return ok()

    # ------------------------------------------------------------------
    # Afsluiten
    # ------------------------------------------------------------------

    @app.post("/api/quit")
    async def quit_ghost() -> JSONResponse:
        # Eerst de echte locatie terug, dan pas het seintje om te stoppen.
        # Zo kan de pagina nog laten zien hoe het is afgelopen.
        result = await controller.safety.shutdown(Reason.QUIT)
        restored = None if result is None else result.ok
        confirmed = None if result is None else result.confirmed
        response = JSONResponse(
            {
                "ok": True,
                "restored": restored,
                "confirmed": confirmed,
                "needs_restore": controller.safety.needs_restore,
                "state": controller.snapshot(),
            }
        )
        if on_quit is not None:
            on_quit()
        return response

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
