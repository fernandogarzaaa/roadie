# Roadie

**A tour-routing agent for independent musicians, grounded in Qloo taste data.**

You give Roadie an artist (or a few reference artists your fans also love), a
home region, a tour length and a budget. It finds the cities where that taste
audience over-indexes, the rooms in each city whose crowd matches, and smaller
acts with a similar sound whose own audience is concentrated nearby. Then it
routes the shows into a drivable loop, prices it, and explains every choice
with the Qloo numbers behind it.

Next to that plan it shows what the same LLM produces with no tools, and audits
it against Qloo: which of its cities the audience actually favours, and whether
its venues and openers exist at all.

Built for the [Qloo Agentic Hackathon](https://qloo.devpost.com/) (deadline Oct 30, 2026, 11:45pm EDT). MIT licensed.

> **Live demo:** https://roadie-zx1q.onrender.com (free Render instance: the first request after idle takes about a minute to wake).
> Runs on the live Qloo hackathon API with Groq `openai/gpt-oss-120b`. Without keys it falls back to
> synthetic fixtures (`QLOO_MOCK=1`) and a scripted stand-in LLM. See
> [docs/QLOO_VERIFY.md](docs/QLOO_VERIFY.md).

---

## Why Qloo matters

An LLM asked to route a tour for a dream-pop act from Portland names the biggest
markets it has read about: Seattle, San Francisco, Los Angeles. It invents a
plausible "Music Hall" in each and suggests "a popular local indie band". None
of that is wrong-sounding, and none of it is about *this* audience.

Roadie's decisions come from Qloo's cross-domain taste graph instead:

| Decision | Without Qloo | With Qloo |
|---|---|---|
| Which cities | Market size and fame | `urn:entity:destination` affinity for the artist's audience, blended with a regional `urn:heatmap` |
| Which rooms | Guessed names | `urn:entity:place` with a music-venue tag, ranked by affinity to the artist, top 3 per city (`diversify.by=properties.geocode.city`) |
| Which openers | "A local band" | `urn:entity:artist` similar but smaller acts (`filter.popularity.max`), scored by **their own** heatmap near each city; your shortlist scored with `filter.results.entities` |
| Why | Vibes | `feature.explainability` shows which reference artist drove each city |
| Map | Pins | Within-city audience hotspots from `urn:heatmap` |

Remove Qloo and the product stops working: there are no cities, rooms or
openers to route. The LLM's job is orchestration and explanation; the cultural
data does the deciding. The comparison panel makes that difference visible on
every run.

## How it works

```
Browser (vanilla JS + Leaflet/OSM)
   │  POST /api/plan
   ▼
FastAPI ─┬─ Agent loop (OpenAI-compatible tool calling, max 12 steps)
         │     tools: resolve_artists → rank_cities → find_venues
         │            → find_openers → plan_route  (+ compare_audiences)
         │     each tool = documented Qloo GET calls, trimmed to ~2.4 KB for the LLM
         │     anything the LLM skips is auto-completed, so output is always grounded
         ├─ Baseline: same LLM, no tools  ──►  audit against Qloo (/search, ranks)
         ├─ Planner: nearest-neighbour + 2-opt loop, travel/rest days, cost model
         ├─ QlooClient (httpx, X-Api-Key, GET only, retries, private cache)
         │     └─ QLOO_MOCK=1 swaps in an httpx.MockTransport over synthetic fixtures
         └─ Optional LangSmith trace: one root run, a child per LLM call and tool call
```

The **Qloo calls** per plan, all `GET https://hackathon.api.qloo.com/...`:

| Step | Endpoint and key params |
|---|---|
| Resolve artists | `/search?query=…&types=urn:entity:artist` |
| Rank cities | `/v2/insights?filter.type=urn:entity:destination&signal.interests.entities=…&filter.location=POINT(lon lat)&filter.location.radius=…&filter.geocode.country_code=…&feature.explainability=true&take=40` |
| Regional heat | `/v2/insights?filter.type=urn:heatmap&signal.interests.entities=…&filter.location=…&filter.location.radius=…` |
| Venue tags | `/v2/tags?filter.query=music venue` (then "live music", "concert hall", "nightclub") |
| Venues | `/v2/insights?filter.type=urn:entity:place&filter.tags=…&signal.interests.entities=…&diversify.by=properties.geocode.city&diversify.take=3&take=50` (+ per-city fallback) |
| Openers | `/v2/insights?filter.type=urn:entity:artist&signal.interests.entities=…&filter.exclude.entities=…&filter.popularity.max=…&take=50`, then one `urn:heatmap` per candidate |
| Your shortlist | `/v2/insights?filter.type=urn:entity:artist&filter.results.entities=…` |
| Fan-base overlap | `/v2/analysis/compare?a.signal.interests.entities=…&b.signal.interests.entities=…` |
| Map hotspots | `/v2/insights?filter.type=urn:heatmap&filter.location=<city>&filter.location.radius=20000` |

Legacy `/recs` and `/recommendations` are refused by the client before any
network call. Every request (path and params, never the key) is shown in the
UI's "How the agent got here" panel.

**Scoring.** City score = 0.7 × destination affinity + 0.3 × normalised regional
heat (affinity alone if the heatmap is unavailable). The default stop set keeps
cities scoring at least 55% of the best one. Opener fit = 0.4 × taste match +
0.6 × local heat, and a suggested opener must have local heat ≥ 0.6 near the
city (shortlisted acts always show). Qloo exposes no artist hometown, so the
UI says to confirm locality before booking.

**Routing and money.** One van; nearest-neighbour then 2-opt for a closed loop
from home; at most 600 km of driving a day (longer legs get travel days); a rest
day after every 5 shows. Cost = fuel $0.22/km × road km (1.25 × great-circle) +
$110 per room-night (2 people per room) + $35 per person per day. The lowest-
scoring stops are dropped until the run fits both the day count and the budget.
Roadie shows break-even per show; it never invents ticket revenue.

## Run it

Requires Python 3.11+.

```bash
git clone <your fork> roadie && cd roadie
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

make mock            # QLOO_MOCK=1 LLM_MOCK=1 on http://localhost:8000
make check           # ruff + mypy + pytest, fully offline
```

Try the presets: indie folk in the Pacific Northwest, psych-soul in Texas,
OPM folk-pop across Luzon, hardcore in the Northeast. In mock mode the header
badges say so, and every number is synthetic.

### Go live

```bash
cp .env.example .env    # then fill in the keys
export $(grep -v '^#' .env | xargs)
python scripts/probe_qloo.py "Phoebe Bridgers" Chicago   # day-one API checks
make run
```

| Variable | Default | Notes |
|---|---|---|
| `QLOO_API_KEY` | — | Hackathon key. Empty means mock mode. |
| `QLOO_BASE_URL` | `https://hackathon.api.qloo.com` | Hackathon keys fail on staging/production hosts. |
| `QLOO_MOCK` | off | Force synthetic fixtures even with a key. |
| `QLOO_VENUE_TAGS` | — | Comma-separated tag IDs; skips `/v2/tags` discovery. |
| `QLOO_CACHE_DIR` | — | Optional private disk cache. Keep it outside the repo. |
| `QLOO_TYPE_DESTINATION`, `QLOO_TYPE_HEATMAP`, `QLOO_HEATMAP_BOUNDARY`, `QLOO_PATH_*` | documented values | Overrides for strings still to verify live. |
| `OPENER_MIN_HEAT` | `0.6` | Local-heat floor for suggested openers. |
| `LLM_BASE_URL` | `https://openrouter.ai/api/v1` | Any OpenAI-compatible `/chat/completions` with tool calling. |
| `LLM_API_KEY` | — | Empty means the scripted mock model. |
| `LLM_MODEL` | `openai/gpt-4o-mini` | Must support tool calling. |
| `LLM_MOCK` | off | Force the scripted model. |
| `LANGSMITH_API_KEY` | — | Turns tracing on. |
| `LANGSMITH_PROJECT` | `roadie` | |
| `LANGSMITH_ENDPOINT` | SDK default | e.g. the EU endpoint. |
| `RATE_LIMIT_PER_10MIN` | `12` | Per-IP plan limit, protects your keys on a public demo. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Addresses whose `X-Forwarded-For` uvicorn trusts (the rate limiter keys on the client IP). Behind Railway/Render, where the container is only reachable through the platform proxy, set `*` (render.yaml already does); never set `*` when the port is directly reachable. |
| `PREWARM` | `1` | Warm the cache with the demo artists at startup. |

**LLM providers.** OpenRouter is the default. Nebius Token Factory is the
documented alternative:

```bash
LLM_BASE_URL=https://api.tokenfactory.nebius.com/v1
LLM_API_KEY=<Token Factory key>
LLM_MODEL=<a tool-calling model id from the Token Factory console>
```

OpenAI, Groq, or a local vLLM/llama.cpp server work the same way. Tool results
are trimmed before they reach the model, so small free tiers (Groq's 8k
tokens/minute, for example) are usable.

**Tracing.** With `LANGSMITH_API_KEY` set, each plan becomes one LangSmith run
(`roadie.plan_tour`) with a child run per LLM call, per tool call (including the
exact Qloo requests it made) and for the LLM-only baseline. The UI shows a
"View agent trace in LangSmith" link. Tracing failures never break a plan.

## Deploy

**Railway (primary).** The repo has a `Dockerfile` and `railway.json`
(Dockerfile builder, `/health` healthcheck, restart on failure). New project →
Deploy from GitHub repo → add the env vars above → generate a domain. Railway
injects `PORT`, which the container honours. Railway has marked
`railway.json` (Config as Code) as deprecated in favour of Infrastructure as
Code, with existing files supported until 2026-12-01; the Dockerfile alone is
enough for Railway to build, so nothing breaks if you drop the JSON.
On Railway also set `FORWARDED_ALLOW_IPS=*` (the container is only reachable
through Railway's proxy); without it every visitor shares the proxy's IP and
the per-IP plan limit becomes a global one.

**Render (fallback).** `render.yaml` defines a free Docker web service with the
same healthcheck; secrets are marked `sync: false` so you enter them in the
dashboard.

`/health` is a constant-time JSON response, suitable for a keep-warm ping on
free tiers that sleep.

## Tests and CI

- 121 pytest tests, all offline: the Qloo client is tested against
  `respx`-mocked HTTP (base URL, `X-Api-Key` header, GET-only, comma-joined
  lists, legacy-path refusal, 401/403/400 handling, 429/5xx retry, caching,
  `/v2/analysis/compare` path), the mock transport against the documented
  failure modes, the planner against brute-force and 2-opt optimality checks,
  the agent loop against a real OpenAI-style tool-call exchange, and the API
  end to end.
- GitHub Actions: ruff + format + mypy; pytest on Python 3.11–3.13; a Docker
  build that boots the container and runs a real `/api/plan`; and a
  non-blocking [testteeth](https://github.com/fernandogarzaaa/testteeth)
  mutation job over the planner, geo and parser modules.

## Data handling

- Qloo responses are cached privately on the server (memory, plus disk only if
  `QLOO_CACHE_DIR` is set). No Qloo response data is committed; `.gitignore`
  blocks the usual cache paths.
- `app/qloo/mock_world.py` is hand-written: real city coordinates, invented
  affinities, fictional venues and fictional local acts. It is not Qloo data.
- No personal data is sent to Qloo, only artist names and places.
- The API key stays server-side and is never logged or shown in the UI.

## Limitations

- Qloo has no documented location filter for artists, so "local" openers are
  inferred from where each act's own audience concentrates. Confirm before booking.
- Venue capacity and fees are not in Qloo; venue popularity is a proxy for size.
- Costs are a simple model with visible assumptions, not a quote.
- The UI was checked in code and tests; it has not yet been reviewed in a real
  browser against the live API.

## Project layout

```
app/
  main.py            FastAPI app, /api/plan, /api/config, /health, rate limit, plan cache
  config.py          env settings (OpenRouter default)
  geo.py             home-region gazetteer, haversine, WKT (lon lat)
  planner.py         routing, schedule, cost model
  tracing.py         optional LangSmith spans
  agent/
    tools.py         the six tools (Qloo-backed), trimming, opener and city scoring
    orchestrator.py  agent loop, auto-completion, LLM-only baseline and audit
    llm.py           OpenAI-compatible client, JSON extraction
    scripted.py      deterministic mock LLM (same tool-calling protocol)
  qloo/
    client.py        documented-API client
    constants.py     endpoint/type strings (env-overridable)
    models.py        defensive response parsers
    cache.py         private TTL cache
    mock.py          httpx.MockTransport imitating the API
    mock_world.py    synthetic fixtures
  static/            index.html, app.js, styles.css
docs/QLOO_VERIFY.md  open API questions and how to settle them
scripts/probe_qloo.py day-one live probes (prints shapes only)
```

## License

MIT, see [LICENSE](LICENSE). Taste data by [Qloo](https://www.qloo.com). Map
tiles © OpenStreetMap contributors.
