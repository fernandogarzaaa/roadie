"""Small geographic helpers: a home-region gazetteer and great-circle distance.

The gazetteer only turns a free-text home region ("Pacific Northwest") into a
center point, radius and country code for Qloo location filters. It holds no
taste data; every ranking signal comes from Qloo.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class Region:
    key: str
    name: str
    lat: float
    lon: float
    radius_km: float
    country_code: str
    aliases: tuple[str, ...] = ()

    def wkt_point(self) -> str:
        return wkt_point(self.lat, self.lon)


REGIONS: tuple[Region, ...] = (
    Region(
        "pnw",
        "Pacific Northwest",
        46.2,
        -122.0,
        450,
        "US",
        ("pnw", "pacific northwest", "seattle", "portland", "oregon", "washington state"),
    ),
    Region(
        "california",
        "California",
        36.2,
        -119.5,
        600,
        "US",
        ("california", "ca", "los angeles", "la", "san francisco", "bay area", "socal"),
    ),
    Region(
        "southwest",
        "Southwest US",
        34.0,
        -111.5,
        600,
        "US",
        ("southwest", "arizona", "new mexico", "phoenix", "las vegas"),
    ),
    Region(
        "texas", "Texas", 31.0, -97.5, 550, "US", ("texas", "tx", "austin", "houston", "dallas")
    ),
    Region(
        "midwest",
        "Midwest US",
        41.8,
        -89.5,
        600,
        "US",
        ("midwest", "chicago", "illinois", "great lakes", "minneapolis", "ohio"),
    ),
    Region(
        "northeast",
        "Northeast US",
        41.5,
        -73.5,
        450,
        "US",
        ("northeast", "new york", "nyc", "boston", "new england", "philadelphia"),
    ),
    Region(
        "southeast",
        "Southeast US",
        34.5,
        -84.5,
        600,
        "US",
        ("southeast", "atlanta", "nashville", "tennessee", "carolinas", "georgia"),
    ),
    Region(
        "mountain",
        "Mountain West",
        40.5,
        -108.5,
        600,
        "US",
        ("mountain west", "colorado", "denver", "utah", "salt lake city"),
    ),
    Region(
        "uk",
        "United Kingdom",
        53.0,
        -2.0,
        500,
        "GB",
        ("uk", "united kingdom", "england", "london", "britain", "manchester"),
    ),
    Region(
        "luzon",
        "Luzon, Philippines",
        15.0,
        121.0,
        400,
        "PH",
        ("philippines", "luzon", "manila", "metro manila", "ph", "bicol"),
    ),
    Region(
        "australia_east",
        "East Coast Australia",
        -31.5,
        151.0,
        800,
        "AU",
        ("australia", "sydney", "melbourne", "brisbane", "east coast australia"),
    ),
)

_LATLON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


def resolve_region(text: str) -> Region:
    """Resolve a home region from free text, a known alias, or a "lat,lon" pair.

    Raises ValueError when the text matches nothing, so the API can report it.
    """
    query = text.strip().lower()
    if not query:
        raise ValueError("home region is empty")
    match = _LATLON.match(query)
    if match:
        lat, lon = float(match.group(1)), float(match.group(2))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(f"coordinates out of range: {text!r}")
        return Region("custom", f"{lat:.3f},{lon:.3f}", lat, lon, 500, "")
    for region in REGIONS:
        if query == region.key or query == region.name.lower() or query in region.aliases:
            return region
    for region in REGIONS:
        if any(alias in query for alias in region.aliases if len(alias) > 3):
            return region
    known = ", ".join(r.name for r in REGIONS)
    raise ValueError(f"unknown home region {text!r}; try one of: {known}, or 'lat,lon'")


def wkt_point(lat: float, lon: float) -> str:
    """WKT is X then Y, so longitude comes first (per Qloo's signal.location docs)."""
    return f"POINT({lon:.6f} {lat:.6f})"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


# Straight-line distance understates road distance; 1.25 is a common detour factor.
ROAD_FACTOR = 1.25


def road_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return haversine_km(lat1, lon1, lat2, lon2) * ROAD_FACTOR
