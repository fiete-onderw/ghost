"""Tests voor de deurwachter: Host, Origin en sessietoken."""

import pytest

from ghost.web.security import SECURITY_HEADERS, LocalGuard, new_token

TOKEN = "geheim-token-voor-de-test"


def controleer(pad="/api/state", **headers):
    guard = LocalGuard(port=8765, token=TOKEN)
    compleet = {"host": "127.0.0.1:8765", "x-ghost-token": TOKEN}
    compleet.update({naam.replace("_", "-"): waarde for naam, waarde in headers.items()})
    return guard.check(pad, {naam: waarde for naam, waarde in compleet.items() if waarde is not None})


def test_eigen_pagina_mag_erin():
    assert controleer() is None
    assert controleer(host="localhost:8765") is None
    assert controleer(host="LOCALHOST:8765") is None
    assert controleer(origin="http://127.0.0.1:8765") is None
    assert controleer(origin="http://localhost:8765") is None


@pytest.mark.parametrize(
    "host",
    [
        "evil.example:8765",  # DNS-rebinding: een vreemde naam die naar 127.0.0.1 wijst
        "evil.example",
        "127.0.0.1",  # zonder poort
        "127.0.0.1:9999",  # verkeerde poort
        "127.0.0.1:8765.evil.example",
        "192.168.1.20:8765",
        "",
        None,
    ],
)
def test_verkeerde_host_wordt_geweigerd(host):
    weigering = controleer(host=host)
    assert weigering is not None and weigering.status == 403
    assert "Host" in weigering.message


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://evil.example:8765",
        "https://127.0.0.1:8765",  # https in plaats van http is een andere herkomst
        "http://127.0.0.1:9999",
        "http://127.0.0.1:8765.evil.example",
        "null",  # bv. een pagina in een afgeschermd kader
        "",
    ],
)
def test_verzoek_van_een_andere_website_wordt_geweigerd(origin):
    weigering = controleer(origin=origin)
    assert weigering is not None and weigering.status == 403
    assert "Origin" in weigering.message


@pytest.mark.parametrize("token", [None, "", "fout", TOKEN + "x", TOKEN[:-1], TOKEN.upper()])
def test_zonder_het_juiste_token_geen_toegang_tot_de_api(token):
    weigering = controleer(x_ghost_token=token)
    assert weigering is not None and weigering.status == 401


def test_token_met_vreemde_tekens_geeft_geen_crash():
    assert controleer(x_ghost_token="tökèn-€").status == 401


def test_de_pagina_zelf_en_hello_hebben_geen_token_nodig():
    assert controleer("/", x_ghost_token=None) is None
    assert controleer("/static/app.js", x_ghost_token=None) is None
    assert controleer("/api/hello", x_ghost_token=None) is None


def test_host_wordt_ook_gecontroleerd_voor_de_pagina_zelf():
    assert controleer("/", host="evil.example:8765").status == 403
    assert controleer("/api/hello", host="evil.example:8765").status == 403


def test_elk_token_is_anders_en_lang_genoeg():
    tokens = {new_token() for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(token) >= 43 for token in tokens)


def test_geen_cors_en_geen_strenge_referrer_policy_in_de_vaste_headers():
    namen = {naam.lower() for naam in SECURITY_HEADERS}
    assert not any(naam.startswith("access-control-") for naam in namen)
    # De kaartserver van OpenStreetMap moet kunnen zien dat de aanvraag van een pagina komt.
    assert SECURITY_HEADERS["Referrer-Policy"] != "no-referrer"
    assert "frame-ancestors 'none'" in SECURITY_HEADERS["Content-Security-Policy"]
    assert "'unsafe-inline'" not in SECURITY_HEADERS["Content-Security-Policy"]
