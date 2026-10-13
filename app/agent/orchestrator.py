"""The Roadie agent loop plus the LLM-only baseline and its Qloo audit."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from typing import Any

from app import planner
from app.agent.llm import ChatModel, LLMError, extract_json
from app.agent.tools import TOOL_SPECS, Toolbox, ToolState, TourRequest, pick_cities, trim
from app.geo import Region
from app.qloo import constants as C
from app.qloo.client import QlooClient, QlooError
from app.tracing import NoopSpan, Span

SYSTEM_PROMPT = """You are Roadie, a tour-routing agent for independent musicians.
You MUST ground every routing decision in the tools, which return Qloo taste data:
1. resolve_artists  2. rank_cities  3. find_venues  4. find_openers  5. plan_route
(compare_audiences is optional, for multiple reference artists).
Pick cities from rank_cities only. Prefer high affinity, but let plan_route enforce
days and budget. Never invent venues, openers or numbers; quote tool values.
When finished, reply with ONLY a JSON object:
{"summary": str, "stops": [{"city": str, "why": str}], "risks": [str]}
Each "why" must cite the Qloo numbers (affinity, venue match, opener fit)."""

BASELINE_PROMPT = """You are a tour-routing assistant for independent musicians.
Without any tools, plan a tour for the request. Reply with ONLY a JSON object:
{"summary": str, "stops": [{"city": str, "lat": float, "lon": float,
"venue": str, "opener": str, "why": str}]}"""


def _request_payload(req: TourRequest, region: Region) -> str:
    return json.dumps(
        {
            "request": {
                "artists": req.artists,
                "home_region": req.home_region,
                "resolved_region": region.name,
                "tour_days": req.tour_days,
                "budget_usd": req.budget_usd,
                "band_size": req.band_size,
                "local_openers": req.local_openers,
            }
        }
    )


async def run_agent(
    llm: ChatModel, toolbox: Toolbox, max_steps: int = 12, span: Span | None = None
) -> dict[str, Any]:
    span = span or NoopSpan()
    state = toolbox.state
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _request_payload(state.request, state.region)},
    ]
    steps: list[dict[str, Any]] = []
    final: dict[str, Any] | None = None
    mode = "llm"
    for _ in range(max_steps):
        llm_span = span.child("llm", "llm", {"messages": len(messages)})
        try:
            reply = await llm.chat(messages, TOOL_SPECS)
        except LLMError as exc:
            llm_span.end(error=str(exc))
            state.warnings.append(f"LLM failed, finished with the scripted pipeline: {exc}")
            mode = "fallback"
            break
        llm_span.end(
            {
                "content": reply.content[:500],
                "tool_calls": len(reply.tool_calls),
                "usage": reply.usage,
            }
        )
        messages.append(reply.as_message())
        if not reply.tool_calls:
            final = extract_json(reply.content)
            if final is None:
                state.warnings.append("LLM's final answer was not JSON; using tool data only.")
            break
        for tc in reply.tool_calls:
            t0 = time.perf_counter()
            calls_before = len(toolbox.client.calls)
            tool_span = span.child(tc.name, "tool", tc.arguments)
            result = await toolbox.call(tc.name, tc.arguments)
            new_calls = [c.to_dict() for c in toolbox.client.calls[calls_before:]]
            tool_span.end(
                {"result": result, "qloo_calls": new_calls},
                error=result.get("error") if isinstance(result, dict) else None,
            )
            steps.append(
                {
                    "tool": tc.name,
                    "args": tc.arguments,
                    "ms": int((time.perf_counter() - t0) * 1000),
                    "qloo_calls": new_calls,
                    "error": result.get("error"),
                }
            )
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": trim(result)})
    else:
        state.warnings.append("Agent hit its step limit; completed with the scripted pipeline.")

    await _complete(toolbox, steps)
    return {"final": final, "steps": steps, "mode": mode}


async def _complete(toolbox: Toolbox, steps: list[dict[str, Any]]) -> None:
    """Fill any step the LLM skipped so the itinerary is always fully grounded."""
    state = toolbox.state
    plan: list[tuple[str, dict[str, Any]]] = []
    if not state.artists:
        plan.append(("resolve_artists", {"names": state.request.artists}))
    if not state.cities:
        plan.append(("rank_cities", {"limit": 12}))
    for name, args in plan:
        result = await toolbox.call(name, args)
        steps.append({"tool": name, "args": args, "auto": True, "error": result.get("error")})
    if state.route is None and state.cities:
        picks = pick_cities(state.cities, state.request.tour_days)
        for name in ("find_venues", "find_openers", "plan_route"):
            result = await toolbox.call(name, {"cities": picks})
            steps.append(
                {
                    "tool": name,
                    "args": {"cities": picks},
                    "auto": True,
                    "error": result.get("error"),
                }
            )
    if state.route is None:
        return
    missing = [s.city for s in state.route.stops if s.city not in state.venues]
    if missing:
        await toolbox.call("find_venues", {"cities": missing})
    missing = [s.city for s in state.route.stops if s.city not in state.openers]
    if missing:
        await toolbox.call("find_openers", {"cities": missing})
    for stop in state.route.stops:
        stop.venues = [v.to_dict() for v in state.venues.get(stop.city, [])]
        stop.openers = state.openers.get(stop.city, [])
    for stop in state.route.stops[:8]:
        city = state.city(stop.city)
        if city is not None and city.name not in state.hotspots:
            await toolbox.hotspots(city)


async def run_baseline(
    llm: ChatModel, req: TourRequest, region: Region, span: Span | None = None
) -> dict[str, Any]:
    span = span or NoopSpan()
    child = span.child("baseline_llm_only", "llm", {"request": req.artists})
    try:
        reply = await llm.chat(
            [
                {"role": "system", "content": BASELINE_PROMPT},
                {"role": "user", "content": _request_payload(req, region)},
            ],
            None,
            max_tokens=1400,
        )
    except LLMError as exc:
        child.end(error=str(exc))
        return {"error": str(exc), "stops": [], "summary": ""}
    child.end({"content": reply.content[:800]})
    parsed = extract_json(reply.content) or {}
    stops = [s for s in parsed.get("stops", []) if isinstance(s, dict) and s.get("city")]
    return {"summary": str(parsed.get("summary", "")), "stops": stops}


async def audit_baseline(
    baseline: dict[str, Any], state: ToolState, client: QlooClient
) -> dict[str, Any]:
    """Check the LLM-only plan against Qloo: are its cities, rooms and openers real
    and do they match this audience?"""
    rank = {c.name.lower(): (i + 1, c) for i, c in enumerate(state.cities)}
    audited = []
    venues_found = openers_found = 0
    affinities: list[float] = []
    opener_ids: list[str] = []
    for s in baseline.get("stops", [])[:10]:
        city = str(s.get("city", ""))
        hit = rank.get(city.lower())
        row: dict[str, Any] = {
            "city": city,
            "venue": s.get("venue"),
            "opener": s.get("opener"),
            "why": s.get("why"),
            "qloo_rank": hit[0] if hit else None,
            "qloo_affinity": round(hit[1].affinity, 3) if hit else None,
            "venue_in_qloo": False,
            "opener_in_qloo": False,
        }
        if hit:
            affinities.append(hit[1].affinity)
        lat, lon = s.get("lat"), s.get("lon")
        if s.get("venue"):
            try:
                extra: dict[str, Any] = {}
                if isinstance(lat, int | float) and isinstance(lon, int | float):
                    extra = {"filter.location": f"{lat},{lon}", "filter.radius": 15}
                found = await client.search(str(s["venue"]), types=[C.TYPE_PLACE], take=1, **extra)
                row["venue_in_qloo"] = bool(found)
                venues_found += bool(found)
            except QlooError:
                pass
        if s.get("opener"):
            try:
                found = await client.search(str(s["opener"]), types=[C.TYPE_ARTIST], take=1)
                exact = [f for f in found if f.name.lower() == str(s["opener"]).lower()]
                row["opener_in_qloo"] = bool(exact)
                if exact:
                    openers_found += 1
                    opener_ids.append(exact[0].entity_id)
            except QlooError:
                pass
        audited.append(row)
    route = None
    pts = [
        s
        for s in baseline.get("stops", [])
        if isinstance(s.get("lat"), int | float) and isinstance(s.get("lon"), int | float)
    ]
    if pts:
        stops = [
            planner.Stop(city=str(s["city"]), lat=float(s["lat"]), lon=float(s["lon"])) for s in pts
        ]
        region = state.region
        days = planner.schedule(
            (region.lat, region.lon),
            region.name,
            stops,
            state.request.start_date or planner.date.today(),
        )
        cost = planner.cost(days, state.request.band_size)
        route = {
            "days_used": len(days),
            "total_km": round(sum(d.drive_km for d in days), 1),
            "cost": cost.to_dict(),
            "within_budget": cost.total <= state.request.budget_usd,
            "fits_days": len(days) <= state.request.tour_days,
        }
    n = len(audited)
    return {
        "stops": audited,
        "route": route,
        "metrics": {
            "stops": n,
            "cities_in_qloo_top": sum(1 for r in audited if r["qloo_rank"]),
            "mean_affinity": round(sum(affinities) / len(affinities), 3) if affinities else None,
            "venues_verified": venues_found,
            "openers_verified": openers_found,
        },
    }


def grounded_metrics(state: ToolState) -> dict[str, Any]:
    route = state.route
    if route is None:
        return {}
    affs = [c.affinity for c in (state.city(s.city) for s in route.stops) if c is not None]
    return {
        "stops": len(route.stops),
        "cities_in_qloo_top": len(route.stops),
        "mean_affinity": round(sum(affs) / len(affs), 3) if affs else None,
        "venues_verified": sum(1 for s in route.stops if s.venues),
        "openers_verified": sum(len(s.openers) > 0 for s in route.stops),
    }


async def plan_tour(
    req: TourRequest,
    region: Region,
    agent_llm: ChatModel,
    baseline_llm: ChatModel,
    client: QlooClient,
    audit_client: QlooClient,
    venue_tags: list[str],
    max_steps: int,
    span: Span,
    include_baseline: bool = True,
) -> dict[str, Any]:
    state = ToolState(request=req, region=region)
    toolbox = Toolbox(client, state, venue_tags)
    agent_task = run_agent(agent_llm, toolbox, max_steps, span)
    if include_baseline:
        agent_out, baseline = await asyncio.gather(
            agent_task, run_baseline(baseline_llm, req, region, span)
        )
    else:
        agent_out, baseline = await agent_task, {"stops": [], "summary": ""}
    audit = await audit_baseline(baseline, state, audit_client) if baseline.get("stops") else None

    final = agent_out["final"] or {}
    whys = {
        str(s.get("city", "")).lower(): str(s.get("why", ""))
        for s in final.get("stops", [])
        if isinstance(s, dict)
    }
    route_dict = planner.route_to_dict(state.route) if state.route else None
    if route_dict:
        for stop in route_dict["stops"]:
            stop["why"] = whys.get(stop["city"].lower(), "")
            stop["hotspots"] = [asdict(p) for p in state.hotspots.get(stop["city"], [])[:25]]
    return {
        "artists": [a.to_dict() for a in state.artists],
        "region": {
            "name": region.name,
            "lat": region.lat,
            "lon": region.lon,
            "radius_km": region.radius_km,
            "country_code": region.country_code,
        },
        "grounded": {
            "summary": final.get("summary", ""),
            "risks": final.get("risks", []),
            "route": route_dict,
            "metrics": grounded_metrics(state),
            "candidates": [
                {
                    "city": c.name,
                    "affinity": round(c.affinity, 3),
                    "heat": None if c.heat is None else round(c.heat, 3),
                    "score": round(c.score, 3),
                    "lat": c.lat,
                    "lon": c.lon,
                }
                for c in state.cities[:20]
            ],
            "steps": agent_out["steps"],
            "mode": agent_out["mode"],
        },
        "baseline": {**baseline, "audit": audit},
        "qloo_calls": [c.to_dict() for c in client.calls],
        "audit_calls": [c.to_dict() for c in audit_client.calls],
        "warnings": state.warnings,
    }
