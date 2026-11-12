"""Tests voor het lezen en controleren van coördinaten."""

import math

import pytest

from ghost.coords import CoordinateError, format_for_log, parse_latlon, validate
from ghost.geo import LatLon

AMSTERDAM = LatLon(52.3676, 4.9041)


@pytest.mark.parametrize(
    "tekst",
    [
        "52.3676, 4.9041",
        "52.3676,4.9041",
        "52.3676 4.9041",
        "  52.3676 ,  4.9041  ",
        "52.3676; 4.9041",
        "52,3676 4,9041",
        "52,3676; 4,9041",
        "52,3676, 4,9041",
        "52.3676°, 4.9041°",
        "+52.3676, +4.9041",
    ],
)
def test_gangbare_schrijfwijzen_worden_begrepen(tekst):
    assert parse_latlon(tekst) == pytest.approx(AMSTERDAM)


def test_negatieve_coordinaten():
    assert parse_latlon("-33.8688, 151.2093") == pytest.approx(LatLon(-33.8688, 151.2093))
    assert parse_latlon("40.6900 -74.0458") == pytest.approx(LatLon(40.69, -74.0458))
    assert parse_latlon("−33.8688, 151.2093").lat == pytest.approx(-33.8688)  # typografisch minteken


def test_hele_getallen():
    assert parse_latlon("52, 4") == LatLon(52.0, 4.0)


@pytest.mark.parametrize("tekst", ["", "hallo", "52.3676", "52.3676, 4.9041, 7", "52.3676, abc", "1e5, 2"])
def test_onzin_geeft_een_duidelijke_fout(tekst):
    with pytest.raises(CoordinateError) as fout:
        parse_latlon(tekst)
    assert "52.3676, 4.9041" in str(fout.value)  # de melding toont een voorbeeld


@pytest.mark.parametrize(("lat", "lon"), [(-90, -180), (90, 180), (0, 0)])
def test_grenswaarden_zijn_geldig(lat, lon):
    assert validate(lat, lon) == LatLon(lat, lon)


@pytest.mark.parametrize(("lat", "lon"), [(90.0001, 0), (-90.0001, 0), (0, 180.0001), (0, -180.0001)])
def test_net_buiten_het_bereik_is_ongeldig(lat, lon):
    with pytest.raises(CoordinateError):
        validate(lat, lon)


@pytest.mark.parametrize(("lat", "lon"), [(math.nan, 0), (0, math.inf), ("x", 0), (None, 0)])
def test_geen_gewone_getallen_is_ongeldig(lat, lon):
    with pytest.raises(CoordinateError):
        validate(lat, lon)


def test_omgedraaide_volgorde_geeft_een_tip():
    with pytest.raises(CoordinateError) as fout:
        parse_latlon("151.2093, -33.8688")
    assert "omgedraaid" in str(fout.value)


def test_log_rondt_af_op_twee_decimalen():
    assert format_for_log(LatLon(52.367612, 4.904139)) == "52.37, 4.90"


def test_log_is_alleen_exact_in_debugmodus():
    assert format_for_log(LatLon(52.367612, 4.904139), precise=True) == "52.367612, 4.904139"
