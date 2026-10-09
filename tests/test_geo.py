import pytest

from app.geo import haversine_km, resolve_region, road_km, wkt_point


@pytest.mark.parametrize(
    "text,key",
    [
        ("Pacific Northwest", "pnw"),
        ("seattle", "pnw"),
        ("PNW", "pnw"),
        ("Metro Manila", "luzon"),
        ("Philippines", "luzon"),
        ("uk", "uk"),
        ("I live near Austin, TX", "texas"),
    ],
)
def test_resolve_region_aliases(text, key):
    assert resolve_region(text).key == key


def test_resolve_latlon():
    r = resolve_region(" 13.62 , 123.19 ")
    assert (r.lat, r.lon, r.key, r.country_code) == (13.62, 123.19, "custom", "")


@pytest.mark.parametrize("bad", ["", "   ", "Atlantis", "95,10", "10,190"])
def test_resolve_region_rejects(bad):
    with pytest.raises(ValueError):
        resolve_region(bad)


def test_wkt_is_longitude_first():
    assert wkt_point(47.6, -122.3) == "POINT(-122.300000 47.600000)"


def test_haversine_seattle_portland():
    assert 230 < haversine_km(47.6062, -122.3321, 45.5152, -122.6784) < 236
    assert haversine_km(1, 1, 1, 1) == 0


def test_road_factor():
    assert road_km(0, 0, 0, 1) == pytest.approx(haversine_km(0, 0, 0, 1) * 1.25)
