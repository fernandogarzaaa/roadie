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


# --- Boundary and reference-value tests (raise the mutation score of the
# --- scoped CI job above its 80% gate; each pins real behaviour).


def test_haversine_matches_reference_distances():
    # Seattle -> Portland is ~234 km great-circle; London -> Paris ~344 km.
    assert haversine_km(47.6062, -122.3321, 45.5152, -122.6784) == pytest.approx(233.7, abs=1.0)
    assert haversine_km(51.5074, -0.1278, 48.8566, 2.3522) == pytest.approx(343.6, abs=1.0)
    # Same point is zero; antipodes are half the circumference (clamped asin).
    assert haversine_km(10.0, 20.0, 10.0, 20.0) == 0.0
    assert haversine_km(0.0, 0.0, 0.0, 180.0) == pytest.approx(20015.1, abs=1.0)


def test_haversine_east_west_leg_uses_both_latitudes():
    # A purely east-west leg at 60N is half as long as the same span at the equator.
    equator = haversine_km(0.0, 0.0, 0.0, 10.0)
    sixty = haversine_km(60.0, 0.0, 60.0, 10.0)
    assert sixty == pytest.approx(equator / 2, rel=0.01)


@pytest.mark.parametrize("text", ["90,180", "-90,-180", "90,-180", "-90,180"])
def test_custom_coordinates_accept_inclusive_bounds(text):
    region = resolve_region(text)
    assert region.key == "custom"


@pytest.mark.parametrize("text", ["90.5,0", "-90.5,0", "0,180.5", "0,-180.5", "91,0", "0,181"])
def test_custom_coordinates_reject_out_of_range(text):
    with pytest.raises(ValueError, match="out of range"):
        resolve_region(text)


def test_short_aliases_only_match_exactly():
    # "ca"/"la"/"tx" are too short for substring matching ("cascadia" must not be California)...
    assert resolve_region("ca").key == "california"
    with pytest.raises(ValueError):
        resolve_region("cascadia")
    # ...but a 4+ char alias inside a longer phrase does match.
    assert resolve_region("gigs around austin please").key == "texas"


@pytest.mark.parametrize(
    "city,lat,lon",
    [
        ("seattle", 47.6062, -122.3321),
        ("los angeles", 34.0522, -118.2437),
        ("phoenix", 33.4484, -112.0740),
        ("austin", 30.2672, -97.7431),
    ],
)
def test_region_centres_cover_their_alias_cities(city, lat, lon):
    # A region alias should resolve to a centre whose radius covers that city.
    region = resolve_region(city)
    assert haversine_km(region.lat, region.lon, lat, lon) <= region.radius_km
    assert haversine_km(region.lat, region.lon, lat, lon) < 300
