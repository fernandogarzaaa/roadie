"""Day-one probes for the live Qloo hackathon API.

Run once the key arrives:

    QLOO_API_KEY=... python scripts/probe_qloo.py "Phoebe Bridgers" Chicago

It prints status codes, result counts and the *keys* of the first result for
each open question in docs/QLOO_VERIFY.md. It never writes responses to disk,
so nothing from Qloo can end up in the repo.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from app.config import load_settings
from app.qloo import constants as C
from app.qloo.client import QlooClient, QlooError


def shape(payload: dict[str, Any]) -> str:
    results = payload.get("results")
    if isinstance(results, dict):
        for key in ("entities", "heatmap", "tags"):
            items = results.get(key)
            if isinstance(items, list):
                first = items[0] if items else {}
                keys = sorted(first) if isinstance(first, dict) else []
                return f"results.{key}: {len(items)} items, first keys={keys}"
        return f"results keys={sorted(results)}"
    if isinstance(results, list):
        first = results[0] if results else {}
        keys = sorted(first) if isinstance(first, dict) else []
        return f"results: {len(results)} items, first keys={keys}"
    return f"top-level keys={sorted(payload)}"


async def probe(client: QlooClient, label: str, path: str, params: dict[str, Any]) -> Any:
    try:
        payload = await client.get(path, params)
        print(f"[ok ] {label}: {shape(payload)}")
        return payload
    except QlooError as exc:
        print(f"[{exc.status}] {label}: {exc.message}")
        return None


async def main(artist: str, city: str) -> None:
    s = load_settings()
    if not s.qloo_api_key:
        sys.exit("Set QLOO_API_KEY first.")
    client = QlooClient(s.qloo_api_key, s.qloo_base_url, max_retries=1)
    found = await probe(
        client,
        "1 search artist",
        C.PATH_SEARCH,
        {"query": artist, "types": C.TYPE_ARTIST, "take": 3},
    )
    results = (found or {}).get("results") or []
    if not results:
        sys.exit("Artist not found; try another name.")
    aid = results[0].get("entity_id")
    print(f"     artist id = {aid}, top-level fields = {sorted(results[0])}")
    sig = {"signal.interests.entities": aid}
    await probe(
        client,
        "2 artist insights",
        C.PATH_INSIGHTS,
        {"filter.type": C.TYPE_ARTIST, **sig, "take": 5},
    )
    for loc in (city, "Atlanta"):
        await probe(
            client,
            f"3 artist + signal.location.query={loc}",
            C.PATH_INSIGHTS,
            {"filter.type": C.TYPE_ARTIST, **sig, "signal.location.query": loc, "take": 5},
        )
    for t in (C.TYPE_DESTINATION, "urn:entity:locality"):
        await probe(
            client,
            f"4 cities via {t}",
            C.PATH_INSIGHTS,
            {
                "filter.type": t,
                **sig,
                "filter.geocode.country_code": "US",
                "feature.explainability": "true",
                "take": 10,
            },
        )
    await probe(
        client,
        "5a heatmap (default boundary)",
        C.PATH_INSIGHTS,
        {"filter.type": C.TYPE_HEATMAP, **sig, "filter.location.query": city, "take": 20},
    )
    for boundary in ("urn:entity:locality", "city", "neighborhood", "urn:geohash"):
        await probe(
            client,
            f"5b heatmap boundary={boundary}",
            C.PATH_INSIGHTS,
            {
                "filter.type": C.TYPE_HEATMAP,
                **sig,
                "filter.location.query": city,
                "output.heatmap.boundary": boundary,
                "take": 20,
            },
        )
    await probe(
        client,
        "5c heatmap type urn:entity:heatmap",
        C.PATH_INSIGHTS,
        {"filter.type": "urn:entity:heatmap", **sig, "filter.location.query": city},
    )
    await probe(
        client,
        "6 heatmap over a 500 km radius",
        C.PATH_INSIGHTS,
        {
            "filter.type": C.TYPE_HEATMAP,
            **sig,
            "filter.location": "POINT(-87.6298 41.8781)",
            "filter.location.radius": 500000,
            "take": 50,
        },
    )
    for q in C.VENUE_TAG_QUERIES:
        tags = await probe(client, f"7 tags '{q}'", C.PATH_TAGS, {"filter.query": q, "take": 10})
        for tag in ((tags or {}).get("results") or {}).get("tags", [])[:5]:
            print(f"       {tag.get('id')}  {tag.get('name')}  type={tag.get('type')}")
    await probe(
        client,
        "8 venues diversified by city",
        C.PATH_INSIGHTS,
        {
            "filter.type": C.TYPE_PLACE,
            **sig,
            "filter.geocode.country_code": "US",
            "diversify.by": "properties.geocode.city",
            "diversify.take": 2,
            "take": 30,
        },
    )
    await probe(
        client,
        "9 compare",
        C.PATH_COMPARE,
        {"a.signal.interests.entities": aid, "b.signal.interests.entities": aid, "take": 5},
    )
    await client.aclose()


if __name__ == "__main__":
    args = sys.argv[1:]
    artist = args[0] if args else "Phoebe Bridgers"
    city = args[1] if len(args) > 1 else "Chicago"
    asyncio.run(main(artist, city))
