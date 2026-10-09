"""Qloo endpoint paths and type URNs in one place.

Several of these were still unverified against the live hackathon host when
Roadie was written (see docs/QLOO_VERIFY.md). Each one can be overridden with
an env var so a live probe can be acted on without a code change.
"""

from __future__ import annotations

import os


def _env(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


# Endpoints (all GET, query-string params)
PATH_SEARCH = _env("QLOO_PATH_SEARCH", "/search")
PATH_INSIGHTS = _env("QLOO_PATH_INSIGHTS", "/v2/insights")
PATH_TAGS = _env("QLOO_PATH_TAGS", "/v2/tags")
PATH_COMPARE = _env("QLOO_PATH_COMPARE", "/v2/analysis/compare")

# Entity / output types
TYPE_ARTIST = _env("QLOO_TYPE_ARTIST", "urn:entity:artist")
# Hackathon guide + Entity Type Guide say destination; the OpenAPI enum lists only
# urn:entity:locality. Destination is tried first; flip this if it 403s.
TYPE_DESTINATION = _env("QLOO_TYPE_DESTINATION", "urn:entity:destination")
TYPE_PLACE = _env("QLOO_TYPE_PLACE", "urn:entity:place")
# Use-case page says urn:heatmap; one parameter description says urn:entity:heatmap.
TYPE_HEATMAP = _env("QLOO_TYPE_HEATMAP", "urn:heatmap")
# Empty = Qloo default (geohash grid). Docs mention city/neighborhood options and
# show urn:entity:locality as an example value, without listing them.
HEATMAP_BOUNDARY = os.getenv("QLOO_HEATMAP_BOUNDARY", "").strip()

# Tag searches tried, in order, to discover music-venue tag IDs at runtime.
VENUE_TAG_QUERIES = tuple(
    q.strip()
    for q in _env("QLOO_VENUE_TAG_QUERIES", "music venue,live music,concert hall,nightclub").split(
        ","
    )
    if q.strip()
)

# Legacy endpoints that the hackathon forbids.
FORBIDDEN_PATHS = ("/recs", "/recommendations", "/v1/recommendations")

TAKE_MAX = 50
