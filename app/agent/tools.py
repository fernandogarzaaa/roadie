"""The agent's tools. Each one wraps documented Qloo calls and returns a
trimmed, LLM-sized summary (no raw payloads: small free tiers cap tokens/min).

The full results stay in ``ToolState`` so the server can assemble the final
itinerary from grounded data even if the LLM paraphrases.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app import planner
from app.geo import Region, haversine_km, wkt_point
from app.qloo import constants as C
from app.qloo.client import QlooClient, QlooError
from app.qloo.models import Entity, HeatPoint

MAX_TOOL_CHARS = 2400
# Minimum regional-heatmap affinity near a city for a suggested opener to count as local.
OPENER_MIN_HEAT = float(os.getenv("OPENER_MIN_HEAT", "0.6"))


@dataclass
class TourRequest:
    artists: list[str]
    home_region: str
    tour_days: int
    budget_usd: float
    band_size: int = 3
    start_date: date | None = None
    local_openers: list[str] = field(default_factory=list)


@dataclass
class CityCandidate:
    name: str
    entity_id: str
    lat: float
    lon: float
    affinity: float
    heat: float | None = None
    popularity: float | None = None
    drivers: dict[str, float] = field(default_factory=dict)

    @property
    def score(self) -> float:
        if self.heat is None:
            return self.affinity
        return 0.7 * self.affinity + 0.3 * self.heat


def pick_cities(
    cities: list[CityCandidate], tour_days: int, cap: int = 8, floor: float = 0.55
) -> list[str]:
    """Default stop choice: strong-affinity cities only, sized to the tour length."""
    if not cities:
        return []
    top = max(c.score for c in cities)
    strong = [c for c in cities if c.score >= floor * top]
    n = max(2, min(len(strong), tour_days - 2, cap))
    pool = strong if len(strong) >= 2 else cities
    return [c.name for c in pool[:n]]


@dataclass
class ToolState:
    request: TourRequest
    region: Region
    artists: list[Entity] = field(default_factory=list)
    cities: list[CityCandidate] = field(default_factory=list)
    venue_tags: list[str] = field(default_factory=list)
    venues: dict[str, list[Entity]] = field(default_factory=dict)
    openers: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    compare: list[dict[str, Any]] = field(default_factory=list)
    route: planner.Route | None = None
    hotspots: dict[str, list[HeatPoint]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def artist_ids(self) -> list[str]:
        return [a.entity_id for a in self.artists]

    def city(self, name: str) -> CityCandidate | None:
        key = name.strip().lower()
        for c in self.cities:
            if c.name.lower() == key:
                return c
        for c in self.cities:
            if key in c.name.lower() or c.name.lower() in key:
                return c
        return None


TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "resolve_artists",
            "description": "Resolve artist names to Qloo entity IDs with Qloo /search. Call first.",
            "parameters": {
                "type": "object",
                "properties": {"names": {"type": "array", "items": {"type": "string"}}},
                "required": ["names"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rank_cities",
            "description": (
                "Rank destinations in the home region by Qloo taste-audience affinity for the "
                "resolved artists (Insights, filter.type=destination, plus a regional heatmap)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 3, "maximum": 25}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_venues",
            "description": "Find music venues in given cities whose audiences match the artist.",
            "parameters": {
                "type": "object",
                "properties": {"cities": {"type": "array", "items": {"type": "string"}}},
                "required": ["cities"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_openers",
            "description": (
                "Suggest smaller artists with a similar taste profile whose audience concentrates "
                "near each city (Insights artist + heatmap), and score any user shortlist."
            ),
            "parameters": {
                "type": "object",
                "properties": {"cities": {"type": "array", "items": {"type": "string"}}},
                "required": ["cities"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_audiences",
            "description": "Compare two artists' audiences with Qloo /v2/analysis/compare.",
            "parameters": {
                "type": "object",
                "properties": {"artist_a": {"type": "string"}, "artist_b": {"type": "string"}},
                "required": ["artist_a", "artist_b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_route",
            "description": (
                "Order the chosen cities into a driving loop from home, insert travel/rest days, "
                "and price it against the budget. Drops lowest-affinity stops if it does not fit."
            ),
            "parameters": {
                "type": "object",
                "properties": {"cities": {"type": "array", "items": {"type": "string"}}},
                "required": ["cities"],
            },
        },
    },
]


def trim(obj: Any, limit: int = MAX_TOOL_CHARS) -> str:
    text = json.dumps(obj, separators=(",", ":"), default=str)
    if len(text) <= limit:
        return text
    return text[: limit - 20] + '..."(truncated)"'


def _r(x: float | None, nd: int = 3) -> float | None:
    return None if x is None else round(x, nd)


class Toolbox:
    def __init__(self, client: QlooClient, state: ToolState, venue_tags: list[str] | None = None):
        self.client = client
        self.state = state
        self._configured_tags = venue_tags or []

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            return {"error": f"unknown tool {name}"}
        try:
            result: dict[str, Any] = await handler(**args)
            return result
        except QlooError as exc:
            self.state.warnings.append(str(exc))
            return {"error": str(exc)}
        except (TypeError, ValueError) as exc:
            return {"error": f"bad arguments for {name}: {exc}"}

    # -- tools ----------------------------------------------------------------

    async def tool_resolve_artists(self, names: list[str] | None = None) -> dict[str, Any]:
        names = names or self.state.request.artists
        found: list[Entity] = []
        missing: list[str] = []
        for name in names[:5]:
            hits = await self.client.search(name, types=[C.TYPE_ARTIST], take=3)
            if hits:
                found.append(hits[0])
            else:
                missing.append(name)
        known = {a.entity_id for a in self.state.artists}
        self.state.artists.extend(a for a in found if a.entity_id not in known)
        if missing:
            self.state.warnings.append(f"Qloo /search found no artist for: {', '.join(missing)}")
        return {
            "artists": [
                {
                    "name": a.name,
                    "id": a.entity_id,
                    "note": a.disambiguation,
                    "popularity": _r(a.popularity),
                }
                for a in found
            ],
            "missing": missing,
        }

    async def _ensure_artists(self) -> None:
        if not self.state.artists:
            await self.tool_resolve_artists()
        if not self.state.artists:
            raise ValueError("no artist could be resolved in Qloo")

    async def tool_rank_cities(self, limit: int = 12) -> dict[str, Any]:
        await self._ensure_artists()
        region = self.state.region
        radius_m = int(min(800_000, region.radius_km * 1000))
        params: dict[str, Any] = {
            "signal.interests.entities": self.state.artist_ids,
            "filter.location": region.wkt_point(),
            "filter.location.radius": radius_m,
            "feature.explainability": True,
            "take": 40,
        }
        if region.country_code:
            params["filter.geocode.country_code"] = region.country_code
        dests = await self.client.insight_entities(C.TYPE_DESTINATION, **params)
        if not dests:
            self.state.warnings.append(
                "Qloo returned no destinations for this artist/region (data gap or param issue)."
            )
        heat: list[HeatPoint] = []
        try:
            heat = await self.client.heatmap(
                **{
                    "signal.interests.entities": self.state.artist_ids,
                    "filter.location": region.wkt_point(),
                    "filter.location.radius": radius_m,
                    "take": 50,
                }
            )
        except QlooError as exc:
            self.state.warnings.append(f"Regional heatmap unavailable: {exc.message}")
        cities: list[CityCandidate] = []
        for d in dests:
            if d.lat is None or d.lon is None:
                continue
            cities.append(
                CityCandidate(
                    name=d.city or d.name,
                    entity_id=d.entity_id,
                    lat=d.lat,
                    lon=d.lon,
                    affinity=d.affinity or 0.0,
                    popularity=d.popularity,
                    drivers=d.explain,
                    heat=_heat_near(heat, d.lat, d.lon, 40.0),
                )
            )
        _normalise(cities)
        cities.sort(key=lambda c: (-c.score, c.name))
        self.state.cities = cities
        names = {a.entity_id: a.name for a in self.state.artists}
        return {
            "region": region.name,
            "cities": [
                {
                    "city": c.name,
                    "affinity": _r(c.affinity),
                    "heat": _r(c.heat),
                    "score": _r(c.score),
                    "km_from_home": round(haversine_km(region.lat, region.lon, c.lat, c.lon)),
                    "driven_by": {names.get(k, k): _r(v, 2) for k, v in c.drivers.items()},
                }
                for c in cities[: max(3, min(25, limit))]
            ],
        }

    async def _venue_tags(self) -> list[str]:
        if self.state.venue_tags:
            return self.state.venue_tags
        tags = list(self._configured_tags)
        if not tags:
            for query in C.VENUE_TAG_QUERIES:
                for tag in await self.client.tags(query, take=10):
                    is_place = "place" in tag.tag_type or ":place:" in tag.tag_id
                    if is_place and tag.tag_id not in tags:
                        tags.append(tag.tag_id)
                if tags:
                    break
        if not tags:
            self.state.warnings.append("No music-venue tag found via /v2/tags; venues unfiltered.")
        self.state.venue_tags = tags
        return tags

    async def tool_find_venues(self, cities: list[str]) -> dict[str, Any]:
        await self._ensure_artists()
        if not self.state.cities:
            await self.tool_rank_cities()
        tags = await self._venue_tags()
        wanted = [c for c in (self.state.city(n) for n in cities[:12]) if c is not None]
        region = self.state.region
        # One diversified regional call first (top venues per city), then per-city
        # fallbacks only for cities it missed.
        params: dict[str, Any] = {
            "signal.interests.entities": self.state.artist_ids,
            "filter.tags": tags or None,
            "filter.location": region.wkt_point(),
            "filter.location.radius": int(min(800_000, region.radius_km * 1000)),
            "diversify.by": "properties.geocode.city",
            "diversify.take": 3,
            "take": 50,
        }
        if tags:
            params["operator.filter.tags"] = "union"
        regional = await self.client.insight_entities(C.TYPE_PLACE, **params)
        for c in wanted:
            near = [
                v
                for v in regional
                if v.lat is not None
                and v.lon is not None
                and haversine_km(c.lat, c.lon, v.lat, v.lon) <= 30
            ]
            if not near:
                near = await self.client.insight_entities(
                    C.TYPE_PLACE,
                    **{
                        "signal.interests.entities": self.state.artist_ids,
                        "filter.tags": tags or None,
                        "operator.filter.tags": "union" if tags else None,
                        "filter.location": wkt_point(c.lat, c.lon),
                        "filter.location.radius": 25_000,
                        "take": 5,
                    },
                )
            self.state.venues[c.name] = near[:3]
        return {
            "venues": {
                city: [
                    {
                        "name": v.name,
                        "affinity": _r(v.affinity),
                        "popularity": _r(v.popularity),
                        "address": v.address,
                    }
                    for v in vs
                ]
                for city, vs in self.state.venues.items()
                if city in {c.name for c in wanted}
            },
            "unknown_cities": [n for n in cities if self.state.city(n) is None],
        }

    async def tool_find_openers(self, cities: list[str]) -> dict[str, Any]:
        await self._ensure_artists()
        if not self.state.cities:
            await self.tool_rank_cities()
        wanted = [c for c in (self.state.city(n) for n in cities[:12]) if c is not None]
        headliner_pop = max((a.popularity or 0.0) for a in self.state.artists)
        pop_cap = round(max(0.15, min(0.8, headliner_pop * 0.6)), 2)
        candidates = await self.client.insight_entities(
            C.TYPE_ARTIST,
            **{
                "signal.interests.entities": self.state.artist_ids,
                "filter.exclude.entities": self.state.artist_ids,
                "filter.popularity.max": pop_cap,
                "take": 50,
            },
        )
        shortlist: list[Entity] = []
        if self.state.request.local_openers:
            ids: list[str] = []
            for name in self.state.request.local_openers[:8]:
                hits = await self.client.search(name, types=[C.TYPE_ARTIST], take=1)
                ids.extend(h.entity_id for h in hits)
            if ids:
                shortlist = await self.client.insight_entities(
                    C.TYPE_ARTIST,
                    **{
                        "signal.interests.entities": self.state.artist_ids,
                        "filter.results.entities": ids,
                        "take": len(ids),
                    },
                )
        # Artists carry no documented location filter, so local fit is measured
        # from each candidate's own heatmap across the region.
        region = self.state.region
        pool = (
            shortlist
            + [c for c in candidates if c.entity_id not in {s.entity_id for s in shortlist}]
        )[:16]
        local: dict[str, list[HeatPoint]] = {}
        for cand in pool:
            try:
                local[cand.entity_id] = await self.client.heatmap(
                    **{
                        "signal.interests.entities": [cand.entity_id],
                        "filter.location": region.wkt_point(),
                        "filter.location.radius": int(min(800_000, region.radius_km * 1000)),
                        "take": 50,
                    }
                )
            except QlooError as exc:
                self.state.warnings.append(f"Opener heatmap failed for {cand.name}: {exc.message}")
                local[cand.entity_id] = []
        shortlisted = {s.entity_id for s in shortlist}
        for c in wanted:
            ranked: list[dict[str, Any]] = []
            for cand in pool:
                heat = _heat_near(local.get(cand.entity_id, []), c.lat, c.lon, 40.0)
                taste = cand.affinity or 0.0
                ranked.append(
                    {
                        "name": cand.name,
                        "entity_id": cand.entity_id,
                        "taste_match": _r(taste),
                        "local_heat": _r(heat),
                        # Local heat outweighs taste: an opener's job is to bring their own crowd.
                        "fit": _r(0.4 * taste + 0.6 * (heat or 0.0)),
                        "from_shortlist": cand.entity_id in shortlisted,
                    }
                )
            ranked.sort(key=lambda r: -(r["fit"] or 0.0))
            # Shortlisted acts always show; suggestions need a real local audience.
            keep = [
                r
                for r in ranked
                if r["from_shortlist"] or (r["local_heat"] or 0.0) >= OPENER_MIN_HEAT
            ]
            self.state.openers[c.name] = keep[:2]
        return {
            "popularity_cap": pop_cap,
            "openers": {
                c.name: [
                    {k: o[k] for k in ("name", "taste_match", "local_heat", "fit")}
                    for o in self.state.openers[c.name]
                ]
                for c in wanted
            },
            "no_local_match": [c.name for c in wanted if not self.state.openers[c.name]],
            "note": "local_heat = where that act's own Qloo audience concentrates; "
            "hometowns are not in Qloo, so confirm before booking.",
        }

    async def tool_compare_audiences(self, artist_a: str, artist_b: str) -> dict[str, Any]:
        ids = []
        for name in (artist_a, artist_b):
            hits = await self.client.search(name, types=[C.TYPE_ARTIST], take=1)
            if not hits:
                return {"error": f"artist not found: {name}"}
            ids.append(hits[0].entity_id)
        payload = await self.client.compare([ids[0]], [ids[1]], take=8)
        results = payload.get("results")
        rows = results if isinstance(results, list) else []
        summary = [
            {"name": r.get("name"), "affinity": _r((r.get("query") or {}).get("affinity"))}
            for r in rows[:8]
            if isinstance(r, dict)
        ]
        self.state.compare.append({"a": artist_a, "b": artist_b, "shared": summary})
        return {"a": artist_a, "b": artist_b, "shared_signals": summary}

    async def tool_plan_route(self, cities: list[str]) -> dict[str, Any]:
        if not self.state.cities:
            await self.tool_rank_cities()
        req = self.state.request
        stops = []
        for name in cities:
            c = self.state.city(name)
            if c is None:
                continue
            stops.append(
                planner.Stop(
                    city=c.name,
                    lat=c.lat,
                    lon=c.lon,
                    score=c.score,
                    entity_id=c.entity_id,
                    venues=[v.to_dict() for v in self.state.venues.get(c.name, [])],
                    openers=self.state.openers.get(c.name, []),
                )
            )
        if not stops:
            return {"error": "none of those cities were in rank_cities output"}
        region = self.state.region
        route = planner.plan_route(
            (region.lat, region.lon),
            region.name,
            stops,
            req.tour_days,
            req.budget_usd,
            req.band_size,
            req.start_date,
        )
        if len(route.days) < 0.6 * req.tour_days:
            route.notes.append(
                f"Only {len(route.stops)} strong-affinity cities fit; the run uses "
                f"{len(route.days)} of {req.tour_days} days. Widen the region or add "
                "reference artists for more options."
            )
        self.state.route = route
        return {
            "order": [s.city for s in route.stops],
            "days_used": len(route.days),
            "tour_days": req.tour_days,
            "total_km": route.total_km,
            "cost_usd": route.cost.total,
            "budget_usd": route.budget,
            "within_budget": route.within_budget,
            "break_even_per_show_usd": route.break_even_per_show,
            "dropped": route.dropped,
            "notes": route.notes,
        }

    async def hotspots(self, city: CityCandidate) -> list[HeatPoint]:
        """Within-city heatmap for the map layer (not an LLM tool)."""
        try:
            points = await self.client.heatmap(
                **{
                    "signal.interests.entities": self.state.artist_ids,
                    "filter.location": wkt_point(city.lat, city.lon),
                    "filter.location.radius": 20_000,
                    "take": 50,
                }
            )
        except QlooError as exc:
            self.state.warnings.append(f"Hotspots unavailable for {city.name}: {exc.message}")
            return []
        self.state.hotspots[city.name] = points
        return points


def _heat_near(points: list[HeatPoint], lat: float, lon: float, km: float) -> float | None:
    vals = [p.affinity for p in points if haversine_km(lat, lon, p.lat, p.lon) <= km]
    return max(vals) if vals else None


def _normalise(cities: list[CityCandidate]) -> None:
    """Rescale heat to 0..1 across candidates so it blends fairly with affinity."""
    heats = [c.heat for c in cities if c.heat is not None]
    if not heats:
        return
    lo, hi = min(heats), max(heats)
    for c in cities:
        if c.heat is not None:
            c.heat = 1.0 if hi == lo else (c.heat - lo) / (hi - lo)
