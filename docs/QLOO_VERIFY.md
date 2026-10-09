# What to verify once the Qloo key arrives

Roadie was built before a hackathon key was issued. The client follows the
documented API exactly, but the docs disagree with themselves in a few places.
Every uncertain string is an env-overridable constant in `app/qloo/constants.py`,
so fixing one is a config change, not a code change.

Run the probes (they print status codes, counts and response *keys* only, and
write nothing to disk):

```bash
QLOO_API_KEY=... python scripts/probe_qloo.py "Phoebe Bridgers" Chicago
```

| # | Question | Roadie's current assumption | If wrong, set |
|---|----------|-----------------------------|---------------|
| 1 | Does `filter.type=urn:entity:destination` work on the hackathon host? The hackathon guide and Entity Type Guide list it (with `signal.interests.entities` required); the Insights OpenAPI enum lists only `urn:entity:locality`. | `urn:entity:destination` | `QLOO_TYPE_DESTINATION=urn:entity:locality` (note: locality only accepts locality entities as signals, so this may need a different city-ranking path) |
| 2 | Heatmap type string: `urn:heatmap` (use-case page, Entity Type Guide) vs `urn:entity:heatmap` (one param description). | `urn:heatmap` | `QLOO_TYPE_HEATMAP=urn:entity:heatmap` |
| 3 | `output.heatmap.boundary` values: docs say "default geohashes; other options city or neighborhood" and show `urn:entity:locality` as an example. | unset (geohash grid; Roadie aggregates points within 40 km of each city) | `QLOO_HEATMAP_BOUNDARY=<value that returns one point per city>` |
| 4 | Does a heatmap accept a 450–800 km radius around a WKT POINT (region-wide), or only a single locality? | region-wide works; failures are caught and the city score falls back to destination affinity alone | lower region radii in `app/geo.py`, or switch to per-city heatmaps |
| 5 | Music-venue tag IDs. No static list exists; Roadie searches `/v2/tags` for "music venue", "live music", "concert hall", "nightclub" and keeps tags whose type or ID contains `place`. | runtime discovery | `QLOO_VENUE_TAGS=urn:tag:...,urn:tag:...` (skips discovery) |
| 6 | Does `diversify.by=properties.geocode.city` work for `urn:entity:place` with a region-wide `filter.location`? | yes; per-city fallback calls fill any gaps | nothing; fallback covers it |
| 7 | Where do `lat`/`lon` live on destination and place entities (`location.lat/lon`, `properties.geocode`, `latitude/longitude`)? | parser accepts all observed shapes; entities without coordinates are skipped | extend `parse_latlon` in `app/qloo/models.py` |
| 8 | Shape of `query.explainability` per entity. | `{"signal.interests.entities": [{"entity_id", "score"}]}` | adjust `parse_entity` |
| 9 | Does `filter.popularity.max` on `urn:entity:artist` return genuinely smaller acts? Roadie caps at `0.6 × headliner popularity` (clamped 0.15–0.8). | yes | tune in `tool_find_openers` |
| 10 | Artists have no documented location filter. Roadie measures an opener's local fit from that artist's own heatmap near each city and only suggests acts with affinity ≥ `OPENER_MIN_HEAT` (0.6). The absolute scale of heatmap `affinity` is undocumented. | 0.6 | `OPENER_MIN_HEAT=<value>` |
| 11 | `/v2/analysis/compare` response shape (`results` as a list of tags with `query.affinity`?). | list of dicts with `name` + `query.affinity` | adjust `tool_compare_audiences` |
| 12 | `/search` for places with `filter.location=lat,lon` and `filter.radius` in **miles** (used by the LLM-only audit). | as documented | — |
| 13 | Rate limits: a full plan makes roughly 20–30 Qloo calls (artist search, 1 destination, 1–2 heatmaps, 1 tag search, 1 diversified venue call plus fallbacks, 1 artist call, up to 16 opener heatmaps, up to 8 city hotspot heatmaps), plus about 10 search calls for the audit. All are cached server-side. | — | lower the opener pool (16) or hotspot count (8) in `app/agent/tools.py` / `orchestrator.py` |

Data handling: responses are cached in memory, and on disk only if
`QLOO_CACHE_DIR` is set (point it outside the repo). `.gitignore` excludes
`.cache/`, `qloo_cache/` and `*.qloo.json`. Mock fixtures in
`app/qloo/mock_world.py` are hand-written and synthetic.
