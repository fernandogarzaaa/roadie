import json

from app.agent.tools import (
    TOOL_SPECS,
    CityCandidate,
    Toolbox,
    _normalise,
    pick_cities,
    trim,
)


def test_tool_specs_are_openai_shaped():
    names = {t["function"]["name"] for t in TOOL_SPECS}
    assert names == {
        "resolve_artists",
        "rank_cities",
        "find_venues",
        "find_openers",
        "compare_audiences",
        "plan_route",
    }
    for t in TOOL_SPECS:
        assert t["type"] == "function" and t["function"]["parameters"]["type"] == "object"


def test_trim_caps_size():
    small = trim({"a": 1})
    assert json.loads(small) == {"a": 1}
    big = trim({"x": "y" * 10_000}, limit=500)
    assert len(big) <= 500 and big.endswith('"(truncated)"')


def test_score_blend_and_normalise():
    a = CityCandidate("A", "1", 0, 0, affinity=0.8)
    assert a.score == 0.8
    b = CityCandidate("B", "2", 0, 0, affinity=0.5, heat=10)
    c = CityCandidate("C", "3", 0, 0, affinity=0.5, heat=20)
    _normalise([a, b, c])
    assert (b.heat, c.heat) == (0.0, 1.0)
    assert c.score == 0.7 * 0.5 + 0.3
    d = CityCandidate("D", "4", 0, 0, affinity=0.1, heat=5)
    _normalise([d])
    assert d.heat == 1.0


def test_pick_cities_threshold():
    cs = [
        CityCandidate(n, n, 0, 0, affinity=x)
        for n, x in [("A", 0.9), ("B", 0.8), ("C", 0.6), ("D", 0.3)]
    ]
    assert pick_cities(cs, tour_days=10) == ["A", "B", "C"]
    assert pick_cities(cs, tour_days=3) == ["A", "B"]
    assert pick_cities([], 5) == []


async def test_full_tool_chain(toolbox: Toolbox):
    r = await toolbox.call("resolve_artists", {"names": ["Phoebe Bridgers", "Big Thief"]})
    assert [a["name"] for a in r["artists"]] == ["Phoebe Bridgers", "Big Thief"]
    ranked = await toolbox.call("rank_cities", {"limit": 5})
    assert len(ranked["cities"]) == 5
    dest_call = next(
        c for c in toolbox.client.calls if c.params.get("filter.type") == "urn:entity:destination"
    )
    p = dest_call.params
    assert p["feature.explainability"] == "true" and p["filter.geocode.country_code"] == "US"
    assert p["filter.location"].startswith("POINT(-122") and p["take"] == "40"
    top = [c["city"] for c in ranked["cities"][:3]]
    venues = await toolbox.call("find_venues", {"cities": top})
    assert set(venues["venues"]) == set(top)
    place_calls = [
        c for c in toolbox.client.calls if c.params.get("filter.type") == "urn:entity:place"
    ]
    assert place_calls[0].params["diversify.by"] == "properties.geocode.city"
    assert "music_venue" in place_calls[0].params["filter.tags"]
    openers = await toolbox.call("find_openers", {"cities": top})
    assert set(openers["openers"]) == set(top)
    artist_call = next(
        c for c in toolbox.client.calls if c.params.get("filter.type") == "urn:entity:artist"
    )
    assert (
        artist_call.params["filter.exclude.entities"]
        == artist_call.params["signal.interests.entities"]
    )
    for city in top:
        for o in toolbox.state.openers[city]:
            assert o["local_heat"] >= 0.6 or o["from_shortlist"]
    route = await toolbox.call("plan_route", {"cities": [*top, "Atlantis"]})
    assert set(route["order"]) <= set(top) and route["within_budget"]
    assert toolbox.state.route is not None


async def test_errors_are_returned_not_raised(toolbox: Toolbox):
    assert "unknown tool" in (await toolbox.call("nope", {}))["error"]
    assert "bad arguments" in (await toolbox.call("plan_route", {"wrong": 1}))["error"]
    await toolbox.call("rank_cities", {})
    assert "error" in await toolbox.call("plan_route", {"cities": ["Atlantis"]})


async def test_shortlist_is_scored_with_filter_results(toolbox: Toolbox):
    toolbox.state.request.local_openers = ["Low Arcades (Bellingham)"]
    await toolbox.call("find_openers", {"cities": ["Bellingham"]})
    shortlist_calls = [c for c in toolbox.client.calls if "filter.results.entities" in c.params]
    assert len(shortlist_calls) == 1
    names = [o["name"] for o in toolbox.state.openers["Bellingham"]]
    assert "Low Arcades (Bellingham)" in names


async def test_compare_and_hotspots(toolbox: Toolbox):
    out = await toolbox.call("compare_audiences", {"artist_a": "Big Thief", "artist_b": "Bon Iver"})
    assert out["shared_signals"]
    await toolbox.call("rank_cities", {})
    pts = await toolbox.hotspots(toolbox.state.cities[0])
    assert pts and toolbox.state.hotspots[toolbox.state.cities[0].name] == pts


async def test_unresolvable_artist_reports():
    from app.agent.tools import ToolState, TourRequest
    from app.geo import resolve_region
    from app.qloo.client import QlooClient
    from app.qloo.mock import mock_transport

    req = TourRequest(["a popular local indie band"], "Texas", 5, 1000)
    tb = Toolbox(
        QlooClient("k", transport=mock_transport()), ToolState(req, resolve_region("Texas"))
    )
    out = await tb.call("rank_cities", {})
    assert "no artist" in out["error"]
    assert tb.state.warnings


async def test_short_run_gets_a_widening_hint(toolbox: Toolbox):
    toolbox.state.request.tour_days = 30
    await toolbox.call("rank_cities", {})
    out = await toolbox.call("plan_route", {"cities": [toolbox.state.cities[0].name]})
    assert any("Widen the region" in n for n in out["notes"])
