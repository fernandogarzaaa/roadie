"""The mock transport must reproduce the documented failure modes."""

import httpx
import pytest

from app.qloo.client import QlooClient, QlooError
from app.qloo.mock import handler, mock_transport
from app.qloo.mock_world import ARTISTS, CITIES, cosine, hashed_vector, mock_id


def raw(method: str, url: str, headers=None) -> httpx.Response:
    hdrs = {"x-api-key": "k"} if headers is None else headers
    return handler(httpx.Request(method, url, headers=hdrs))


def test_auth_and_paths():
    assert raw("GET", "https://h/v2/insights", headers={}).status_code == 401
    assert raw("GET", "https://h/").status_code == 404
    assert raw("GET", "https://h/recs").status_code == 404
    assert raw("GET", "https://h/v2/insights/search").status_code == 404
    assert raw("POST", "https://h/v2/insights?filter.type=urn:entity:artist").status_code == 404


def test_type_and_signal_errors():
    assert raw("GET", "https://h/v2/insights").status_code == 400
    assert raw("GET", "https://h/v2/insights?filter.type=urn:entity:music").status_code == 403
    r = raw("GET", "https://h/v2/insights?filter.type=urn:entity:destination")
    assert r.status_code == 400 and "valid signal" in r.json()["message"]
    assert raw("GET", "https://h/v2/insights?filter.type=urn:heatmap").status_code == 400


async def test_destination_ranking_is_taste_coherent():
    client = QlooClient("k", transport=mock_transport())
    a = (await client.search("Phoebe Bridgers"))[0]
    out = await client.insight_entities(
        "urn:entity:destination",
        **{
            "signal.interests.entities": [a.entity_id],
            "filter.geocode.country_code": "US",
            "filter.location": "POINT(-122.0 46.2)",
            "filter.location.radius": 450000,
            "take": 50,
        },
    )
    names = [e.name for e in out]
    assert "Vancouver" not in names  # country filter
    assert names.index("Portland") < names.index("Spokane")
    assert all(e.country_code == "US" for e in out)
    affs = [e.affinity for e in out]
    assert affs == sorted(affs, reverse=True)


async def test_unknown_artist_is_synthetic_but_generic_phrase_is_not():
    client = QlooClient("k", transport=mock_transport())
    assert (await client.search("Some Unknown Band"))[0].disambiguation == "Synthetic mock artist"
    assert await client.search("a popular local indie band") == []


async def test_results_filter_and_diversify():
    client = QlooClient("k", transport=mock_transport())
    a = (await client.search("Khruangbin"))[0]
    pool = await client.insight_entities(
        "urn:entity:artist", **{"signal.interests.entities": [a.entity_id], "take": 5}
    )
    ids = [p.entity_id for p in pool[:2]]
    only = await client.insight_entities(
        "urn:entity:artist",
        **{"signal.interests.entities": [a.entity_id], "filter.results.entities": ids},
    )
    assert sorted(e.entity_id for e in only) == sorted(ids)
    venues = await client.insight_entities(
        "urn:entity:place",
        **{
            "signal.interests.entities": [a.entity_id],
            "diversify.by": "properties.geocode.city",
            "diversify.take": 1,
            "filter.location": "POINT(-97.5 31.0)",
            "filter.location.radius": 550000,
            "take": 50,
        },
    )
    cities = [v.city for v in venues]
    assert len(cities) == len(set(cities)) > 1


async def test_compare_needs_both_sides():
    client = QlooClient("k", transport=mock_transport(), max_retries=0)
    with pytest.raises(QlooError):
        await client.compare([], ["x"])
    ids = [a.entity_id for a in ARTISTS[:2]]
    out = await client.compare([ids[0]], [ids[1]])
    assert out["results"] and "_mock_overlap" in out


def test_world_helpers():
    assert mock_id("a", "x") == mock_id("a", "x") != mock_id("b", "x")
    assert hashed_vector("abc") == hashed_vector("ABC")
    assert cosine((1.0, 0.0), (0.0, 0.0)) == 0.0
    assert len({c.name for c in CITIES}) == len(CITIES)
