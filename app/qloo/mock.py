"""An ``httpx.MockTransport`` that imitates the documented Qloo hackathon API.

The real ``QlooClient`` runs unchanged on top of it, so mock mode exercises the
same URL building, headers, error handling and parsing as production. It
mirrors documented behaviour we rely on:

* 401 without ``X-Api-Key``; 404 for the bare base URL or unknown paths.
* 405-style failure for POST on ``/v2/insights`` (it is GET only).
* 403 for unsupported ``filter.type`` (e.g. ``urn:entity:music``).
* 400 "at least one valid signal or filter is required" when signals are missing.
* Unknown params are silently ignored, as the guide warns.

All values come from ``mock_world`` and are synthetic.
"""

from __future__ import annotations

import math
import re
from typing import Any

import httpx

from app.geo import haversine_km
from app.qloo import mock_world as w

SUPPORTED_TYPES = {
    "urn:entity:artist",
    "urn:entity:book",
    "urn:entity:brand",
    "urn:entity:destination",
    "urn:entity:movie",
    "urn:entity:person",
    "urn:entity:place",
    "urn:entity:podcast",
    "urn:entity:tv_show",
    "urn:entity:video_game",
    "urn:heatmap",
}
MOCK_KEY = "mock-key"
_POINT = re.compile(r"POINT\s*\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)", re.I)
_LATLON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


def _json(status: int, body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(status, json=body)


def _err(status: int, message: str) -> httpx.Response:
    return _json(
        status,
        {"code": status, "reason": httpx.codes.get_reason_phrase(status), "message": message},
    )


def _csv(params: httpx.QueryParams, key: str) -> list[str]:
    return [v.strip() for v in params.get(key, "").split(",") if v.strip()]


def _float(params: httpx.QueryParams, key: str, default: float | None = None) -> float | None:
    try:
        return float(params[key]) if key in params else default
    except ValueError:
        return default


def _location(params: httpx.QueryParams) -> tuple[float, float] | None:
    raw = params.get("filter.location") or params.get("signal.location") or ""
    m = _POINT.search(raw)
    if m:
        return float(m.group(2)), float(m.group(1))  # WKT is lon lat
    m = _LATLON.match(raw)
    if m:
        return float(m.group(1)), float(m.group(2))
    query = (params.get("filter.location.query") or "").lower()
    for city in w.CITIES:
        if city.name.lower() == query:
            return city.lat, city.lon
    return None


def _taste_for(ids: list[str]) -> tuple[float, ...] | None:
    known = {a.entity_id: a for a in (*w.ARTISTS, *w.ALL_LOCAL_ACTS)}
    vectors = [known[i].taste for i in ids if i in known]
    if not vectors:
        return None
    return tuple(sum(col) / len(vectors) for col in zip(*vectors, strict=True))


def _affinity(signal: tuple[float, ...], target: tuple[float, ...], pop: float) -> float:
    # Taste similarity dominates; a little popularity keeps big cities relevant.
    return round(0.85 * w.cosine(signal, target) + 0.15 * pop, 4)


def _search(params: httpx.QueryParams) -> httpx.Response:
    query = params.get("query", "").strip().lower()
    if not query:
        return _err(400, "query must be specified.")
    types = _csv(params, "types") or ["urn:entity:artist"]
    take = int(params.get("take", "5"))
    results: list[dict[str, Any]] = []
    if "urn:entity:artist" in types:
        pool = (*w.ARTISTS, *w.ALL_LOCAL_ACTS)
        exact = [a for a in pool if a.name.lower() == query]
        partial = [a for a in pool if query in a.name.lower() and a not in exact]
        hits = exact + partial
        generic = len(query.split()) > 4 or query.startswith(("a ", "an ", "the local", "local "))
        if not hits and not generic:
            # Unknown artists still resolve, with a stable pseudo-taste, so any
            # input demos end to end. Flagged in disambiguation.
            synthetic = w.MockArtist(
                params.get("query", "").strip(),
                w.hashed_vector(query),
                0.3,
                disambiguation="Synthetic mock artist",
            )
            hits = [synthetic]
            _SYNTHETIC[synthetic.entity_id] = synthetic
        for a in hits[:take]:
            results.append(
                {
                    "name": a.name,
                    "entity_id": a.entity_id,
                    "types": ["urn:entity:artist"],
                    "popularity": a.popularity,
                    "disambiguation": a.disambiguation,
                    "properties": {},
                    "tags": [],
                }
            )
    if "urn:entity:place" in types:
        loc = _location(params)
        radius_km = (_float(params, "filter.radius", 10.0) or 10.0) * 1.609
        for v in w.ALL_VENUES:
            if query in v.name.lower() or query in f"{v.name} {v.city}".lower():
                if loc and haversine_km(loc[0], loc[1], v.lat, v.lon) > radius_km:
                    continue
                results.append(_venue_json(v, None))
        results = results[:take]
    return _json(200, {"success": True, "results": results, "duration": 0.01})


_SYNTHETIC: dict[str, w.MockArtist] = {}


def _home_city(ids: list[str]) -> w.MockCity | None:
    for act in w.ALL_LOCAL_ACTS:
        if act.entity_id in ids:
            return w.CITY_BY_NAME.get(act.home_city)
    return None


def _venue_json(v: w.MockVenue, affinity: float | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": v.name,
        "entity_id": v.entity_id,
        "type": "urn:entity",
        "subtype": "urn:entity:place",
        "popularity": v.popularity,
        "location": {"lat": v.lat, "lon": v.lon},
        "properties": {
            "address": v.address,
            "geocode": {"name": v.city, "country_code": w.CITY_BY_NAME[v.city].country_code},
        },
        "tags": [{"id": w.VENUE_TAG_ID, "name": "Music Venue", "type": "urn:tag:category:place"}],
    }
    if affinity is not None:
        body["query"] = {"affinity": affinity}
    return body


def _signal(params: httpx.QueryParams) -> tuple[list[str], tuple[float, ...] | None]:
    ids = _csv(params, "signal.interests.entities")
    taste = _taste_for(ids)
    if taste is None:
        synth = [_SYNTHETIC[i].taste for i in ids if i in _SYNTHETIC]
        if synth:
            taste = tuple(sum(c) / len(synth) for c in zip(*synth, strict=True))
    return ids, taste


def _insights(request: httpx.Request) -> httpx.Response:
    if request.method != "GET":
        return _err(404, "No path matching request parameters")
    params = request.url.params
    ftype = params.get("filter.type", "")
    if not ftype:
        return _err(400, "filter.type is required")
    if ftype not in SUPPORTED_TYPES:
        return _err(403, f"You do not have permission to access {ftype}")
    ids, taste = _signal(params)
    take = min(50, int(params.get("take", "20")))
    pop_min = _float(params, "filter.popularity.min", 0.0) or 0.0
    pop_max = _float(params, "filter.popularity.max", 1.0) or 1.0
    exclude = set(_csv(params, "filter.exclude.entities"))
    loc = _location(params)
    radius_km = (_float(params, "filter.location.radius", 15000.0) or 15000.0) / 1000

    if ftype == "urn:heatmap":
        if loc is None:
            return _err(400, "filter.location or filter.location.query is required")
        if taste is None:
            return _err(400, "at least one valid signal or filter is required")
        home = _home_city(ids)
        cells: list[tuple[float, float, float, float]] = []
        for c in w.CITIES:
            if haversine_km(loc[0], loc[1], c.lat, c.lon) > max(radius_km, 30):
                continue
            base = _affinity(taste, c.taste, c.popularity)
            if home is not None:
                # A local act's audience is concentrated around its own scene.
                d_home = haversine_km(home.lat, home.lon, c.lat, c.lon)
                base = min(1.0, base * (0.45 + 0.55 * math.exp(-d_home / 250)))
            for k in range(7):  # a small geohash-like cluster per city
                s = w._seed(f"cell:{c.name}:{k}")
                dlat = ((s % 97) / 97 - 0.5) * 0.08
                dlon = (((s >> 8) % 89) / 89 - 0.5) * 0.08
                cell_aff = round(max(0.0, base - (k * 0.03) - ((s >> 16) % 10) / 200), 4)
                cells.append((cell_aff, c.lat + dlat, c.lon + dlon, c.popularity))
        cells.sort(key=lambda p: p[0], reverse=True)
        n = len(cells)
        heat = []
        for rank, (aff, lat, lon, pop) in enumerate(cells):
            heat.append(
                {
                    "location": {
                        "latitude": round(lat, 5),
                        "longitude": round(lon, 5),
                        "geohash": w.mock_id("gh", f"{lat:.3f}{lon:.3f}")[:6].lower(),
                    },
                    "query": {
                        "affinity": aff,
                        "affinity_rank": round(1 - rank / max(1, n), 4),
                        "popularity": pop,
                    },
                }
            )
        return _json(
            200,
            {
                "success": True,
                "results": {"heatmap": heat[:take] if take else heat},
                "duration": 12,
            },
        )

    no_signal = not ids or taste is None
    if no_signal and (ftype == "urn:entity:destination" or not params.get("filter.tags")):
        return _err(400, "at least one valid signal or filter is required")

    entities: list[tuple[float, dict[str, Any]]] = []
    if ftype == "urn:entity:destination":
        country = params.get("filter.geocode.country_code")
        for c in w.CITIES:
            if country and c.country_code != country:
                continue
            if loc and haversine_km(loc[0], loc[1], c.lat, c.lon) > radius_km:
                continue
            if not pop_min <= c.popularity <= pop_max or c.entity_id in exclude:
                continue
            aff = _affinity(taste or (), c.taste, c.popularity)
            entities.append(
                (
                    aff,
                    {
                        "name": c.name,
                        "entity_id": c.entity_id,
                        "type": "urn:entity",
                        "subtype": "urn:entity:destination",
                        "popularity": c.popularity,
                        "location": {"lat": c.lat, "lon": c.lon},
                        "properties": {
                            "geocode": {
                                "name": c.name,
                                "country_code": c.country_code,
                                "admin1_region": c.admin1,
                            }
                        },
                        "query": {"affinity": aff},
                    },
                )
            )
    elif ftype == "urn:entity:place":
        tags = set(_csv(params, "filter.tags"))
        venue_tags = {t["id"] for t in w.MOCK_TAGS if "place" in t["id"]}
        for v in w.ALL_VENUES:
            if tags and not tags & venue_tags:
                continue
            if loc and haversine_km(loc[0], loc[1], v.lat, v.lon) > radius_km:
                continue
            if not pop_min <= v.popularity <= pop_max or v.entity_id in exclude:
                continue
            aff = _affinity(taste, v.taste, v.popularity) if taste else round(v.popularity, 4)
            entities.append((aff, _venue_json(v, aff)))
    elif ftype == "urn:entity:artist":
        for a in (*w.ARTISTS, *w.ALL_LOCAL_ACTS):
            if a.entity_id in exclude or a.entity_id in ids:
                continue
            if not pop_min <= a.popularity <= pop_max:
                continue
            aff = _affinity(taste or (), a.taste, a.popularity)
            entities.append(
                (
                    aff,
                    {
                        "name": a.name,
                        "entity_id": a.entity_id,
                        "type": "urn:entity",
                        "subtype": "urn:entity:artist",
                        "popularity": a.popularity,
                        "properties": {},
                        "query": {"affinity": aff},
                        "disambiguation": a.disambiguation,
                    },
                )
            )
    # Other supported types return an empty list, like a data gap would.
    only = set(_csv(params, "filter.results.entities"))
    if only:
        entities = [e for e in entities if e[1]["entity_id"] in only]
    entities.sort(key=lambda e: e[0], reverse=True)
    if params.get("diversify.by") == "properties.geocode.city":
        per_city = int(params.get("diversify.take", "5"))
        seen: dict[str, int] = {}
        kept = []
        for aff, ent in entities:
            city = ent.get("properties", {}).get("geocode", {}).get("name", "")
            if seen.get(city, 0) < per_city:
                seen[city] = seen.get(city, 0) + 1
                kept.append((aff, ent))
        entities = kept
    if params.get("feature.explainability") == "true" and ids:
        for _aff, ent in entities:
            # Split the score across input entities by their own similarity.
            parts: list[dict[str, Any]] = []
            for i in ids:
                t = _taste_for([i]) or (_SYNTHETIC[i].taste if i in _SYNTHETIC else None)
                if t is not None:
                    score = round(max(0.0, w.cosine(t, _target(ent))), 3)
                    parts.append({"entity_id": i, "score": score})
            total = sum(p["score"] for p in parts) or 1.0
            for p in parts:
                p["score"] = round(p["score"] / total, 3)
            ent.setdefault("query", {})["explainability"] = {"signal.interests.entities": parts}
    page = max(1, int(params.get("page", "1")))
    chunk = [e for _, e in entities][(page - 1) * take : page * take]
    return _json(200, {"success": True, "results": {"entities": chunk}, "duration": 9})


_TARGETS: dict[str, tuple[float, ...]] = {
    **{c.entity_id: c.taste for c in w.CITIES},
    **{v.entity_id: v.taste for v in w.ALL_VENUES},
    **{a.entity_id: a.taste for a in (*w.ARTISTS, *w.ALL_LOCAL_ACTS)},
}


def _target(ent: dict[str, Any]) -> tuple[float, ...]:
    return _TARGETS.get(ent["entity_id"], tuple(0.0 for _ in w.AXES))


def _compare(params: httpx.QueryParams) -> httpx.Response:
    a_ids = _csv(params, "a.signal.interests.entities")
    b_ids = _csv(params, "b.signal.interests.entities")
    a, b = _taste_for(a_ids), _taste_for(b_ids)
    if a is None or b is None:
        return _err(400, "a.signal.interests.entities and b.signal.interests.entities are required")
    take = min(50, int(params.get("take", "10")))
    shared = sorted(
        (
            {
                "tag_id": f"urn:tag:genre:music:{axis}",
                "name": axis.title(),
                "query": {
                    "a": {"affinity": round(x, 3)},
                    "b": {"affinity": round(y, 3)},
                    "affinity": round(min(x, y), 3),
                },
            }
            for axis, x, y in zip(w.AXES, a, b, strict=True)
            if x > 0 and y > 0
        ),
        key=lambda t: float(t["query"]["affinity"]),  # type: ignore[index]
        reverse=True,
    )
    return _json(
        200,
        {
            "success": True,
            "results": shared[:take],
            "duration": 7,
            "_mock_overlap": round(w.cosine(a, b), 3),
        },
    )


def _tags(params: httpx.QueryParams) -> httpx.Response:
    query = params.get("filter.query", "").lower()
    words = [t for t in re.split(r"\W+", query) if t]
    hits = [t for t in w.MOCK_TAGS if all(word in t["name"].lower() for word in words)]
    return _json(200, {"success": True, "results": {"tags": hits[: int(params.get("take", "20"))]}})


def handler(request: httpx.Request) -> httpx.Response:
    if not request.headers.get("x-api-key"):
        return _err(401, "No API key found in request")
    path = request.url.path.rstrip("/")
    if path == "":
        return _err(404, "Not found")
    if path in {"/recs", "/recommendations"}:
        return _err(404, "Legacy endpoint not available")
    if path == "/search":
        return _search(request.url.params)
    if path == "/v2/insights":
        return _insights(request)
    if path == "/v2/tags":
        return _tags(request.url.params)
    if path == "/v2/analysis/compare":
        return _compare(request.url.params)
    return _err(404, "No path matching request parameters")


def mock_transport() -> httpx.MockTransport:
    return httpx.MockTransport(handler)
