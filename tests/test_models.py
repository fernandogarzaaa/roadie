from app.qloo.models import (
    parse_entity,
    parse_heatmap,
    parse_insight_entities,
    parse_latlon,
    parse_search_results,
    parse_tags,
)


def test_latlon_shapes():
    assert parse_latlon({"lat": 1.5, "lon": 2.5}) == (1.5, 2.5)
    assert parse_latlon({"latitude": "1.5", "longitude": "2.5"}) == (1.5, 2.5)
    assert parse_latlon({"lat": 10, "lng": 20}) == (10.0, 20.0)
    assert parse_latlon({"lat": -122.3, "lon": 47.6}) == (47.6, -122.3)  # swapped pair
    assert parse_latlon({"lat": True, "lon": 1}) is None
    assert parse_latlon({"lat": 1}) is None
    assert parse_latlon("nope") is None
    assert parse_latlon({"lat": 200, "lon": 300}) is None


def test_parse_entity_full():
    e = parse_entity(
        {
            "entity_id": "X",
            "name": "Portland",
            "subtype": "urn:entity:destination",
            "popularity": 0.9,
            "location": {"lat": 45.5, "lon": -122.6},
            "properties": {
                "geocode": {"name": "Portland", "country_code": "US"},
                "address": "1 Main",
            },
            "query": {
                "affinity": 0.87,
                "explainability": {
                    "signal.interests.entities": [
                        {"entity_id": "A", "score": 0.7},
                        {"entity_id": "B"},
                    ]
                },
            },
            "tags": [{"name": "Indie"}, {"id": "noname"}],
        }
    )
    assert e is not None
    assert (e.affinity, e.lat, e.lon, e.city, e.country_code, e.address) == (
        0.87,
        45.5,
        -122.6,
        "Portland",
        "US",
        "1 Main",
    )
    assert e.explain == {"A": 0.7}
    assert e.tags == ["Indie"]
    assert e.to_dict()["name"] == "Portland"


def test_parse_entity_minimal_and_invalid():
    e = parse_entity({"id": "1", "name": "A", "types": ["urn:entity:artist"]})
    assert e is not None and e.subtype == "urn:entity:artist" and e.affinity is None
    assert parse_entity({"name": "no id"}) is None
    assert parse_entity(["x"]) is None


def test_result_containers():
    payload = {"results": {"entities": [{"entity_id": "1", "name": "A"}, {"bad": 1}]}}
    assert [e.name for e in parse_insight_entities(payload)] == ["A"]
    assert parse_insight_entities({"results": []}) == []
    assert [
        e.name for e in parse_search_results({"results": [{"entity_id": "1", "name": "A"}]})
    ] == ["A"]
    assert [e.name for e in parse_search_results(payload)] == ["A"]
    assert parse_search_results(None) == []


def test_heatmap_matches_docs_shape():
    payload = {
        "results": {
            "heatmap": [
                {
                    "location": {"latitude": 40.59, "longitude": -73.75, "geohash": "dr5wct"},
                    "query": {"affinity": 1, "affinity_rank": 0.99, "popularity": 0.97},
                },
                {"location": {}, "query": {}},
            ]
        }
    }
    pts = parse_heatmap(payload)
    assert len(pts) == 1
    assert (pts[0].lat, pts[0].affinity, pts[0].affinity_rank, pts[0].popularity) == (
        40.59,
        1.0,
        0.99,
        0.97,
    )


def test_tags():
    tags = parse_tags(
        {"results": {"tags": [{"id": "urn:tag:x", "name": "X", "type": "t"}, {"name": "n"}]}}
    )
    assert [(t.tag_id, t.name, t.tag_type) for t in tags] == [("urn:tag:x", "X", "t")]
    assert parse_tags({"results": [{"tag_id": "a", "name": "A"}]})[0].tag_id == "a"


# --- Boundary tests for coordinate parsing and field defaults.


def test_latlon_non_numeric_strings_are_rejected():
    assert parse_latlon({"lat": "north", "lon": "2.5"}) is None
    assert parse_latlon({"lat": "1.5", "lon": "2.5"}) == (1.5, 2.5)
    assert parse_latlon({"lat": True, "lon": 2.5}) is None


def test_latlon_inclusive_bounds_are_kept():
    assert parse_latlon({"lat": 90, "lon": 180}) == (90.0, 180.0)
    assert parse_latlon({"lat": -90, "lon": -180}) == (-90.0, -180.0)
    assert parse_latlon({"lat": 45, "lon": 90}) == (45.0, 90.0)


def test_latlon_swapped_pair_is_repaired_only_when_unambiguous():
    # lat out of range but lon would be a valid latitude -> swap.
    assert parse_latlon({"lat": 121.0, "lon": 14.6}) == (14.6, 121.0)
    assert parse_latlon({"lat": 180.0, "lon": 90.0}) == (90.0, 180.0)
    # Neither ordering is valid -> rejected, not swapped into garbage.
    assert parse_latlon({"lat": 121.0, "lon": 95.0}) is None
    assert parse_latlon({"lat": 45.0, "lon": 181.0}) is None


def test_entity_optional_fields_default_to_empty_strings():
    entity = parse_entity({"entity_id": "e1", "name": "Venue", "subtype": None})
    assert entity is not None
    assert entity.subtype == ""
    assert entity.disambiguation == ""
    entity = parse_entity(
        {
            "entity_id": "e2",
            "name": "Venue",
            "subtype": "urn:entity:place",
            "disambiguation": "Seattle",
        }
    )
    assert entity.subtype == "urn:entity:place"
    assert entity.disambiguation == "Seattle"


def test_heatmap_missing_affinity_defaults_to_zero_and_name_falls_back():
    points = parse_heatmap(
        {"results": {"heatmap": [{"location": {"lat": 1, "lon": 2, "name": "Loc"}, "query": {}}]}}
    )
    assert len(points) == 1
    assert points[0].affinity == 0.0
    assert points[0].affinity_rank == 0.0
    assert points[0].popularity == 0.0
    assert points[0].name == "Loc"
