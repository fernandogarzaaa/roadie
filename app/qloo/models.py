"""Defensive parsers for Qloo responses.

Qloo's docs do not publish a full entity schema, and examples differ in small
ways (``lat``/``lon`` vs ``latitude``/``longitude``; one heatmap example even
labels a longitude as ``lat``). These helpers normalise what we need and keep
the raw dict out of anything we persist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def parse_latlon(obj: Any) -> tuple[float, float] | None:
    """Pull a (lat, lon) pair out of the location shapes Qloo docs show."""
    if not isinstance(obj, dict):
        return None
    lat = _num(obj.get("lat", obj.get("latitude")))
    lon = _num(obj.get("lon", obj.get("lng", obj.get("longitude"))))
    if lat is None or lon is None:
        return None
    if not -90 <= lat <= 90 and -90 <= lon <= 90:
        # Swapped pair (seen in the heatmap docs' locality example).
        lat, lon = lon, lat
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


@dataclass
class Entity:
    entity_id: str
    name: str
    subtype: str = ""
    affinity: float | None = None
    popularity: float | None = None
    lat: float | None = None
    lon: float | None = None
    city: str = ""
    country_code: str = ""
    address: str = ""
    tags: list[str] = field(default_factory=list)
    disambiguation: str = ""
    explain: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "subtype": self.subtype,
            "affinity": self.affinity,
            "popularity": self.popularity,
            "lat": self.lat,
            "lon": self.lon,
            "city": self.city,
            "country_code": self.country_code,
            "address": self.address,
            "tags": self.tags[:6],
            "disambiguation": self.disambiguation,
        }


def parse_entity(raw: Any) -> Entity | None:
    if not isinstance(raw, dict):
        return None
    entity_id = raw.get("entity_id") or raw.get("id")
    name = raw.get("name")
    if not entity_id or not name:
        return None
    props = _obj(raw.get("properties"))
    geocode = _obj(props.get("geocode"))
    query = _obj(raw.get("query"))

    loc = parse_latlon(raw.get("location")) or parse_latlon(props.get("location"))
    subtype = raw.get("subtype") or ""
    if not subtype and isinstance(raw.get("types"), list) and raw["types"]:
        subtype = str(raw["types"][0])

    tags: list[str] = []
    for tag in raw.get("tags") or []:
        if isinstance(tag, dict) and tag.get("name"):
            tags.append(str(tag["name"]))

    explain: dict[str, float] = {}
    exp = query.get("explainability")
    if isinstance(exp, dict):
        for key, items in exp.items():
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and item.get("entity_id"):
                        score = _num(item.get("score"))
                        if score is not None:
                            explain[str(item["entity_id"])] = score
            elif isinstance(items, int | float):
                explain[key] = float(items)

    return Entity(
        entity_id=str(entity_id),
        name=str(name),
        subtype=str(subtype),
        affinity=_num(query.get("affinity")),
        popularity=_num(raw.get("popularity")),
        lat=loc[0] if loc else None,
        lon=loc[1] if loc else None,
        city=str(geocode.get("name") or geocode.get("city") or ""),
        country_code=str(geocode.get("country_code") or ""),
        address=str(props.get("address") or ""),
        tags=tags,
        disambiguation=str(raw.get("disambiguation") or ""),
        explain=explain,
    )


def parse_insight_entities(payload: Any) -> list[Entity]:
    results = payload.get("results") if isinstance(payload, dict) else None
    raw_list = results.get("entities") if isinstance(results, dict) else None
    return [e for e in (parse_entity(r) for r in raw_list or []) if e is not None]


def parse_search_results(payload: Any) -> list[Entity]:
    results = payload.get("results") if isinstance(payload, dict) else None
    if isinstance(results, dict):  # tolerate {"results": {"entities": [...]}} too
        results = results.get("entities")
    return [e for e in (parse_entity(r) for r in results or []) if e is not None]


@dataclass(frozen=True)
class HeatPoint:
    lat: float
    lon: float
    affinity: float
    affinity_rank: float
    popularity: float
    name: str = ""


def parse_heatmap(payload: Any) -> list[HeatPoint]:
    results = payload.get("results") if isinstance(payload, dict) else None
    raw_list = results.get("heatmap") if isinstance(results, dict) else None
    points: list[HeatPoint] = []
    for raw in raw_list or []:
        if not isinstance(raw, dict):
            continue
        loc = parse_latlon(raw.get("location"))
        q = _obj(raw.get("query"))
        if loc is None:
            continue
        points.append(
            HeatPoint(
                lat=loc[0],
                lon=loc[1],
                affinity=_num(q.get("affinity")) or 0.0,
                affinity_rank=_num(q.get("affinity_rank")) or 0.0,
                popularity=_num(q.get("popularity")) or 0.0,
                name=str(raw.get("name") or (raw.get("location") or {}).get("name") or ""),
            )
        )
    return points


@dataclass(frozen=True)
class Tag:
    tag_id: str
    name: str
    tag_type: str = ""


def parse_tags(payload: Any) -> list[Tag]:
    results = payload.get("results") if isinstance(payload, dict) else None
    raw_list = results.get("tags") if isinstance(results, dict) else results
    tags: list[Tag] = []
    for raw in raw_list or []:
        if isinstance(raw, dict) and (raw.get("id") or raw.get("tag_id")) and raw.get("name"):
            tags.append(
                Tag(
                    tag_id=str(raw.get("id") or raw.get("tag_id")),
                    name=str(raw["name"]),
                    tag_type=str(raw.get("type") or raw.get("subtype") or ""),
                )
            )
    return tags
