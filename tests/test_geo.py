"""Tests voor de routewiskunde. Geen iPhone of internet nodig."""

import math

import pytest

from ghost.geo import (
    EARTH_RADIUS_M,
    LatLon,
    Route,
    clamp_speed_kmh,
    distance_after,
    eta_seconds,
    haversine_m,
    intermediate_point,
)

ONE_DEGREE_M = math.pi / 180 * EARTH_RADIUS_M  # ongeveer 111,2 km

DAM = LatLon(52.3731, 4.8932)
CENTRAAL = LatLon(52.3791, 4.9003)


# --- afstand ---------------------------------------------------------------


def test_afstand_tot_jezelf_is_nul():
    assert haversine_m(DAM, DAM) == 0


def test_een_graad_breedte_is_ongeveer_111_km():
    assert haversine_m(LatLon(0, 0), LatLon(1, 0)) == pytest.approx(ONE_DEGREE_M, rel=1e-9)


def test_een_graad_lengte_op_de_evenaar_is_ongeveer_111_km():
    assert haversine_m(LatLon(0, 0), LatLon(0, 1)) == pytest.approx(ONE_DEGREE_M, rel=1e-9)


def test_een_graad_lengte_is_korter_bij_amsterdam():
    afstand = haversine_m(LatLon(52, 4), LatLon(52, 5))
    assert afstand == pytest.approx(ONE_DEGREE_M * math.cos(math.radians(52)), rel=1e-3)


def test_afstand_is_in_beide_richtingen_gelijk():
    assert haversine_m(DAM, CENTRAAL) == pytest.approx(haversine_m(CENTRAAL, DAM))


def test_dam_naar_centraal_station_is_ongeveer_800_meter():
    assert 750 < haversine_m(DAM, CENTRAAL) < 850


def test_afstand_over_de_datumgrens_is_kort():
    assert haversine_m(LatLon(0, 179.5), LatLon(0, -179.5)) == pytest.approx(ONE_DEGREE_M, rel=1e-9)


# --- tussenpunt ------------------------------------------------------------


def test_tussenpunt_begin_en_eind():
    assert intermediate_point(DAM, CENTRAAL, 0) == pytest.approx(DAM)
    assert intermediate_point(DAM, CENTRAAL, 1) == pytest.approx(CENTRAAL)


def test_tussenpunt_halverwege_ligt_even_ver_van_beide_punten():
    midden = intermediate_point(DAM, CENTRAAL, 0.5)
    assert haversine_m(DAM, midden) == pytest.approx(haversine_m(midden, CENTRAAL), rel=1e-6)
    assert haversine_m(DAM, midden) == pytest.approx(haversine_m(DAM, CENTRAAL) / 2, rel=1e-6)


def test_tussenpunt_steekt_de_datumgrens_over_en_niet_de_hele_wereld():
    midden = intermediate_point(LatLon(0, 179), LatLon(0, -179), 0.5)
    assert abs(midden.lon) == pytest.approx(180)
    assert midden.lat == pytest.approx(0, abs=1e-9)


def test_tussenpunt_van_samenvallende_punten():
    assert intermediate_point(DAM, DAM, 0.3) == DAM


def test_tussenpunt_begrenst_de_fractie():
    assert intermediate_point(DAM, CENTRAAL, -5) == pytest.approx(DAM)
    assert intermediate_point(DAM, CENTRAAL, 5) == pytest.approx(CENTRAAL)


# --- snelheid en tijd ------------------------------------------------------


@pytest.mark.parametrize(("invoer", "verwacht"), [(0.2, 1), (1, 1), (5, 5), (120, 120), (500, 120), (-3, 1)])
def test_snelheid_wordt_begrensd_op_1_tot_120(invoer, verwacht):
    assert clamp_speed_kmh(invoer) == verwacht


@pytest.mark.parametrize("invoer", [math.nan, math.inf])
def test_snelheid_moet_een_gewoon_getal_zijn(invoer):
    with pytest.raises(ValueError):
        clamp_speed_kmh(invoer)


def test_lopen_met_5_kmu_is_1000_meter_in_12_minuten():
    assert distance_after(5, 12 * 60) == pytest.approx(1000)


def test_resterende_tijd():
    assert eta_seconds(1000, 5) == pytest.approx(720)
    assert eta_seconds(0, 5) == 0
    assert eta_seconds(-10, 5) == 0
    assert eta_seconds(100, 0) == math.inf


# --- route -----------------------------------------------------------------

# Drie punten op de evenaar: A -> B is 1 graad, B -> C is 2 graden.
A, B, C = LatLon(0, 0), LatLon(0, 1), LatLon(0, 3)


def test_route_heeft_minstens_twee_punten_nodig():
    with pytest.raises(ValueError):
        Route([A])


def test_lengte_is_de_som_van_de_stukken():
    assert Route([A, B, C]).length_m == pytest.approx(3 * ONE_DEGREE_M)


def test_begin_van_de_route():
    positie = Route([A, B, C]).position_at(0)
    assert positie.point == pytest.approx(A)
    assert positie.travelled_m == 0
    assert positie.remaining_m == pytest.approx(3 * ONE_DEGREE_M)
    assert not positie.finished


def test_positie_onderweg_ligt_op_het_juiste_stuk():
    route = Route([A, B, C])
    positie = route.position_at(2 * ONE_DEGREE_M)  # 1 graad voorbij B
    assert positie.point == pytest.approx(LatLon(0, 2), abs=1e-9)
    assert positie.remaining_m == pytest.approx(ONE_DEGREE_M)


def test_precies_op_een_tussenpunt():
    assert Route([A, B, C]).position_at(ONE_DEGREE_M).point == pytest.approx(B, abs=1e-9)


def test_einde_van_de_route():
    route = Route([A, B, C])
    for afstand in (route.length_m, route.length_m + 5000):
        positie = route.position_at(afstand)
        assert positie.point == C
        assert positie.remaining_m == 0
        assert positie.finished


def test_negatieve_afstand_telt_als_nul():
    assert Route([A, B]).position_at(-50).point == pytest.approx(A)


def test_dubbele_punten_geven_geen_fout():
    route = Route([A, A, B, B])
    assert route.length_m == pytest.approx(ONE_DEGREE_M)
    assert route.position_at(ONE_DEGREE_M / 2).point == pytest.approx(LatLon(0, 0.5), abs=1e-9)


def test_route_zonder_lengte_is_meteen_klaar():
    positie = Route([A, A]).position_at(10)
    assert positie.point == A
    assert positie.finished


def test_heen_en_terug_is_twee_keer_zo_lang_en_eindigt_bij_het_begin():
    route = Route([A, B, C], round_trip=True)
    assert route.length_m == pytest.approx(6 * ONE_DEGREE_M)
    assert route.position_at(3 * ONE_DEGREE_M).point == pytest.approx(C, abs=1e-9)  # keerpunt
    assert route.position_at(4 * ONE_DEGREE_M).point == pytest.approx(LatLon(0, 2), abs=1e-9)  # op de terugweg
    einde = route.position_at(6 * ONE_DEGREE_M)
    assert einde.point == pytest.approx(A)
    assert einde.finished


def test_herhalen_maakt_een_gesloten_lus():
    route = Route([A, B, C], repeat=True)
    assert route.length_m == pytest.approx(6 * ONE_DEGREE_M)  # 3 graden heen + 3 graden sluitstuk
    assert route.path[-1] == A
    positie = route.position_at(7 * ONE_DEGREE_M)  # 1 graad in de tweede ronde
    assert positie.lap == 1
    assert positie.point == pytest.approx(B, abs=1e-9)
    assert not positie.finished


def test_herhalen_sluit_niet_dubbel_als_begin_en_eind_al_gelijk_zijn():
    route = Route([A, B, A], repeat=True)
    assert route.length_m == pytest.approx(2 * ONE_DEGREE_M)


def test_herhalen_met_heen_en_terug_pendelt_eindeloos():
    route = Route([A, B], round_trip=True, repeat=True)
    assert route.position_at(1.5 * ONE_DEGREE_M).point == pytest.approx(LatLon(0, 0.5), abs=1e-9)
    derde_ronde = route.position_at(4.25 * ONE_DEGREE_M)
    assert derde_ronde.lap == 2
    assert derde_ronde.point == pytest.approx(LatLon(0, 0.25), abs=1e-9)
    assert not derde_ronde.finished


def test_herhalen_verspringt_niet_op_de_rondegrens():
    route = Route([A, B, LatLon(1, 1)], repeat=True)  # een driehoek
    net_voor = route.position_at(route.length_m - 1).point
    net_na = route.position_at(route.length_m + 1).point
    # Beide posities liggen 1 meter van het beginpunt: er zit geen sprong in.
    assert haversine_m(net_voor, A) == pytest.approx(1, abs=0.01)
    assert haversine_m(net_na, A) == pytest.approx(1, abs=0.01)


def test_snelheid_over_een_kilometer_klopt():
    """Wie 12 minuten met 5 km/u loopt, staat 1 km verder op de route."""
    route = Route([DAM, LatLon(52.40, 4.8932)])  # recht naar het noorden, ongeveer 3 km
    afgelegd = distance_after(5, 12 * 60)
    positie = route.position_at(afgelegd)
    assert haversine_m(DAM, positie.point) == pytest.approx(1000, abs=0.01)
    assert eta_seconds(positie.remaining_m, 5) == pytest.approx((route.length_m - 1000) / (5 / 3.6))


# --- een stukje verschuiven ------------------------------------------------


def test_verschuiven_naar_het_noorden_verandert_alleen_de_breedtegraad():
    from ghost.geo import destination_point

    start = LatLon(52.0, 5.0)
    nieuw = destination_point(start, 0, 1000)
    assert nieuw.lat > start.lat
    assert nieuw.lon == pytest.approx(start.lon, abs=1e-9)
    assert haversine_m(start, nieuw) == pytest.approx(1000, abs=0.01)


@pytest.mark.parametrize("richting", [0, 45, 90, 135, 180, 225, 270, 315])
@pytest.mark.parametrize("afstand", [1, 10, 250, 1000])
def test_verschuiven_legt_precies_de_gevraagde_afstand_af(richting, afstand):
    from ghost.geo import destination_point

    start = LatLon(48.8584, 2.2945)
    assert haversine_m(start, destination_point(start, richting, afstand)) == pytest.approx(afstand, abs=0.01)


def test_verschuiven_over_de_datumgrens_blijft_een_geldige_lengtegraad():
    from ghost.geo import destination_point

    nieuw = destination_point(LatLon(0.0, 179.9999), 90, 1000)
    assert -180 <= nieuw.lon <= 180
    assert nieuw.lon < 0  # aan de andere kant van de datumgrens


def test_lengte_van_een_lijn():
    from ghost.geo import path_length_m

    a, b, c = LatLon(52.0, 5.0), LatLon(52.01, 5.0), LatLon(52.01, 5.01)
    assert path_length_m([a]) == 0
    assert path_length_m([a, b, c]) == pytest.approx(haversine_m(a, b) + haversine_m(b, c))
