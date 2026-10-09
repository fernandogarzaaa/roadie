"""A deterministic stand-in LLM for LLM_MOCK=1 / no LLM key.

It drives the *same* orchestrator loop and tool-calling protocol as a real
model (tool call -> tool result -> next call -> final JSON), so tests and the
demo exercise the real code path. Its prose is templated and the UI labels it.
"""

from __future__ import annotations

import json
from typing import Any

from app.agent.llm import LLMReply, ToolCall
from app.geo import haversine_km, resolve_region
from app.qloo import mock_world


def _tool_results(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    names: dict[str, str] = {}
    for m in messages:
        for tc in m.get("tool_calls") or []:
            names[tc["id"]] = tc["function"]["name"]
    out: dict[str, dict[str, Any]] = {}
    for m in messages:
        if m.get("role") == "tool":
            try:
                out[names.get(m.get("tool_call_id", ""), "?")] = json.loads(m["content"])
            except (ValueError, KeyError):
                out[names.get(m.get("tool_call_id", ""), "?")] = {}
    return out


def _request(messages: list[dict[str, Any]]) -> dict[str, Any]:
    for m in messages:
        if m.get("role") == "user":
            try:
                return dict(json.loads(m["content"]).get("request", {}))
            except (ValueError, AttributeError):
                return {}
    return {}


class ScriptedAgentLLM:
    name = "scripted-mock-llm"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 900,
    ) -> LLMReply:
        req = _request(messages)
        done = _tool_results(messages)
        step = len(done)

        def call(name: str, args: dict[str, Any]) -> LLMReply:
            return LLMReply(tool_calls=[ToolCall(f"call_{step}_{name}", name, args)])

        if "resolve_artists" not in done:
            return call("resolve_artists", {"names": req.get("artists", [])})
        if "rank_cities" not in done:
            return call("rank_cities", {"limit": 12})
        ranked = done["rank_cities"].get("cities", [])
        top = max((c.get("score") or 0.0 for c in ranked), default=0.0)
        strong = [c["city"] for c in ranked if (c.get("score") or 0.0) >= 0.55 * top]
        n = max(2, min(len(strong), int(req.get("tour_days", 7)) - 2, 8))
        picks = strong[:n] if len(strong) >= 2 else [c["city"] for c in ranked[:n]]
        if "find_venues" not in done:
            return call("find_venues", {"cities": picks})
        if "find_openers" not in done:
            return call("find_openers", {"cities": picks})
        if "plan_route" not in done:
            return call("plan_route", {"cities": picks})
        route = done["plan_route"]
        by_city = {c["city"]: c for c in done["rank_cities"].get("cities", [])}
        venues = done["find_venues"].get("venues", {})
        openers = done["find_openers"].get("openers", {})
        stops = []
        for city in route.get("order", []):
            c = by_city.get(city, {})
            v = (venues.get(city) or [{}])[0]
            o = (openers.get(city) or [{}])[0]
            why = (
                f"Qloo affinity {c.get('affinity')} (score {c.get('score')}) for this audience"
                + (
                    f"; best-matched room {v['name']} ({v.get('affinity')})"
                    if v.get("name")
                    else ""
                )
                + (f"; opener {o['name']} fits {o.get('fit')}" if o.get("name") else "")
                + "."
            )
            stops.append({"city": city, "why": why})
        summary = (
            f"{len(stops)} shows over {route.get('days_used')} days, "
            f"{route.get('total_km')} km, est. ${route.get('cost_usd')} "
            f"vs ${route.get('budget_usd')} budget. Cities chosen by Qloo audience "
            "affinity, ordered as a driving loop."
        )
        risks = list(route.get("notes", []))
        if not route.get("within_budget"):
            risks.append("Over budget at minimum viable routing.")
        return LLMReply(content=json.dumps({"summary": summary, "stops": stops, "risks": risks}))


class ScriptedBaselineLLM:
    """Imitates a typical ungrounded answer: famous big cities, generic rooms."""

    name = "scripted-mock-llm"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 900,
    ) -> LLMReply:
        req = _request(messages)
        try:
            region = resolve_region(str(req.get("home_region", "")))
        except ValueError:
            return LLMReply(content=json.dumps({"summary": "Unknown region.", "stops": []}))
        near = [
            c
            for c in mock_world.CITIES
            if haversine_km(region.lat, region.lon, c.lat, c.lon) <= region.radius_km
        ]
        near.sort(key=lambda c: (-c.popularity, c.name))
        n = max(2, min(len(near), int(req.get("tour_days", 7)) - 2, 8))
        stops = [
            {
                "city": c.name,
                "lat": c.lat,
                "lon": c.lon,
                "venue": f"{c.name} Music Hall",
                "opener": "a popular local indie band",
                "why": f"{c.name} is a major market with a big live-music scene.",
            }
            for c in near[:n]
        ]
        return LLMReply(
            content=json.dumps(
                {
                    "summary": "Hit the biggest markets in the region for maximum exposure.",
                    "stops": stops,
                }
            )
        )
