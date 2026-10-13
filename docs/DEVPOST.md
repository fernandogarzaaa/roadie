Project name: Roadie
Tagline: Tour routing for indie musicians, grounded in where your fans actually are.

Try it out:
- Live demo: https://roadie-zx1q.onrender.com
- Code (MIT): https://github.com/fernandogarzaaa/roadie

Built with: python, fastapi, httpx, qloo, groq, gpt-oss, leaflet, openstreetmap, docker, render, langsmith

## Inspiration
Independent bands book their own tours, and most of them guess. They play the cities they've heard of, in rooms they found on a forum, with an opener they met once. A bad routing decision costs a small band its whole margin: an empty Tuesday in a big market loses money that a full room 200 km away would have made. Ask an LLM and you get the same guesses, written more confidently: the biggest markets, a plausible "Music Hall," and "a popular local indie band."

## What it does
You enter an artist (or a few reference artists your fans also love), a home region, tour length, budget and band size. Roadie:
1. Resolves the artists to Qloo entities.
2. Ranks the cities where that taste audience over-indexes (destination affinity blended with a regional audience heatmap), and explains which reference artist drove each pick.
3. Finds the rooms in each city whose crowd matches: Qloo places tagged as music venues, ranked by affinity, three per city.
4. Finds openers: similar but smaller acts, scored by their own audience heat near each stop.
5. Routes it all into a drivable loop with travel and rest days, prices fuel, lodging and per diems, and shows the break-even per show.
6. Draws it on a map with within-city audience hotspots.

Next to the plan, it runs the same LLM with no tools, then audits that answer against Qloo: which of its cities the audience actually favors, and whether its venues and openers exist at all. Every run shows the gap between a confident guess and a grounded plan.

## How we built it
A FastAPI backend runs an OpenAI-compatible tool-calling agent (Groq's gpt-oss-120b on the free tier) over six tools, each a set of documented Qloo GET calls: /search, /v2/insights for destinations, places, artists and heatmaps, /v2/tags, and /v2/analysis/compare. A typical plan makes about 28 Qloo calls. Tool results are trimmed to keep the model's context small, and any step the model skips is completed deterministically, so the output is always grounded. A nearest-neighbour plus 2-opt planner builds the route; the frontend is vanilla JS with Leaflet and OpenStreetMap. Optional LangSmith tracing records one span per LLM and tool call. The whole thing is a single Docker image on Render's free plan, with 159 tests and a mock mode (synthetic Qloo fixtures plus a scripted LLM) so anyone can run it without keys.

## Challenges we ran into
- Free-tier LLM limits: Groq caps gpt-oss at 8,000 tokens a minute, so the client honours the provider's retry hint on 429s and keeps every tool result compact.
- Reasoning models sometimes wrapped their final JSON answer in a call to a tool that doesn't exist. Roadie recovers that answer instead of failing the run.
- Entity coverage: not every artist resolves (one OPM act we wanted for the Philippines preset isn't in the graph), so Roadie surfaces a clear warning and the presets use artists that resolve.
- Heatmaps want a geohash boundary, not a locality entity, for city-level hotspots.

## Accomplishments that we're proud of
- Remove Qloo and the product stops working: there are no cities, rooms or openers to route. The LLM orchestrates and explains; the taste data decides.
- The side-by-side audit makes that case on every run instead of in a slide.
- A Khruangbin tour out of Texas came back as a five-city, eight-day loop (Austin, New Braunfels, Galveston, Houston, Dallas) at about $2,040 in costs against a $5,000 budget, each stop explained with its affinity score and best-matched venue, in under 20 seconds.

## What we learned
Cultural affinity and market size are different maps. The cities an audience over-indexes in are often not the biggest ones, and that difference is exactly where a small band makes or loses money.

## What's next for Roadie
- Real calendar data: venue availability and competing shows on the same night.
- Ticket-price and capacity estimates per room, so break-even becomes a forecast.
- Export to a booking-agent email pack: one pitch per venue with the audience numbers attached.
- Merch and setlist suggestions per city from the same taste signals.
