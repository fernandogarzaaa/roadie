import json

import httpx
import respx

from app.agent.llm import LLMError, LLMReply, OpenAICompatibleLLM, ToolCall
from app.agent.orchestrator import audit_baseline, grounded_metrics, plan_tour, run_agent
from app.agent.scripted import ScriptedAgentLLM, ScriptedBaselineLLM
from app.agent.tools import Toolbox, ToolState
from app.geo import resolve_region
from app.qloo.client import QlooClient
from app.qloo.mock import mock_transport
from app.tracing import NoopSpan


def client() -> QlooClient:
    return QlooClient("k", transport=mock_transport())


async def test_scripted_agent_end_to_end(toolbox: Toolbox):
    out = await run_agent(ScriptedAgentLLM(), toolbox)
    assert out["mode"] == "llm" and out["final"]["stops"]
    assert [s["tool"] for s in out["steps"]] == [
        "resolve_artists",
        "rank_cities",
        "find_venues",
        "find_openers",
        "plan_route",
    ]
    assert all(s["qloo_calls"] for s in out["steps"][:4])
    route = toolbox.state.route
    assert route is not None and route.within_budget
    assert all(stop.venues for stop in route.stops)
    assert toolbox.state.hotspots


class FailingLLM:
    name = "fail"

    async def chat(self, *a, **k):
        raise LLMError("boom")


class ProseLLM:
    name = "prose"

    async def chat(self, *a, **k):
        return LLMReply("I think you should go to Portland.")


class LoopingLLM:
    name = "loop"

    async def chat(self, *a, **k):
        return LLMReply(tool_calls=[ToolCall("x", "resolve_artists", {})])


async def test_llm_failure_falls_back_to_grounded_pipeline(toolbox: Toolbox):
    out = await run_agent(FailingLLM(), toolbox)
    assert out["mode"] == "fallback"
    assert toolbox.state.route is not None
    assert any("LLM failed" in w for w in toolbox.state.warnings)
    assert all(s.get("auto") for s in out["steps"])


async def test_non_json_final_is_flagged(toolbox: Toolbox):
    out = await run_agent(ProseLLM(), toolbox)
    assert out["final"] is None and toolbox.state.route is not None
    assert any("not JSON" in w for w in toolbox.state.warnings)


async def test_step_limit(toolbox: Toolbox):
    await run_agent(LoopingLLM(), toolbox, max_steps=2)
    assert any("step limit" in w for w in toolbox.state.warnings)
    assert toolbox.state.route is not None


@respx.mock
async def test_real_client_tool_protocol(toolbox: Toolbox):
    """A real OpenAI-compatible model: tool call -> tool message with tool_call_id -> final."""
    base = "https://openrouter.ai/api/v1"
    replies = iter(
        [
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "resolve_artists",
                                        "arguments": '{"names": ["Big Thief"]}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            {"choices": [{"message": {"content": '{"summary": "s", "stops": [], "risks": []}'}}]},
        ]
    )
    route = respx.post(f"{base}/chat/completions").mock(
        side_effect=lambda req: httpx.Response(200, json=next(replies))
    )
    out = await run_agent(OpenAICompatibleLLM(base, "k", "m"), toolbox)
    second = json.loads(route.calls[1].request.content)
    tool_msg = second["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "c1"
    assert "Big Thief" in tool_msg["content"]
    assert second["messages"][-2]["tool_calls"][0]["id"] == "c1"
    assert out["final"]["summary"] == "s"
    assert toolbox.state.route is not None  # auto-completed the rest


async def test_baseline_audit_flags_unverifiable_plan(pnw_request):
    state = ToolState(pnw_request, resolve_region("Pacific Northwest"))
    tb = Toolbox(client(), state)
    await tb.call("rank_cities", {})
    baseline = {
        "stops": [
            {
                "city": "Seattle",
                "lat": 47.6,
                "lon": -122.3,
                "venue": "Seattle Music Hall",
                "opener": "a popular local indie band",
            },
            {"city": "Atlantis", "lat": 0, "lon": 0, "venue": "", "opener": ""},
        ]
    }
    audit = await audit_baseline(baseline, state, client())
    m = audit["metrics"]
    assert m["stops"] == 2 and m["cities_in_qloo_top"] == 1
    assert m["venues_verified"] == 0 and m["openers_verified"] == 0
    assert audit["stops"][0]["qloo_rank"] is not None and audit["stops"][1]["qloo_rank"] is None
    assert audit["route"]["total_km"] > 10_000  # the made-up stop is far away
    assert audit["route"]["fits_days"] is False


async def test_baseline_audit_verifies_real_names(pnw_request):
    state = ToolState(pnw_request, resolve_region("Pacific Northwest"))
    tb = Toolbox(client(), state)
    await tb.call("rank_cities", {})
    v = (await client().search("The Anchor Tavern", types=["urn:entity:place"]))[0]
    audit = await audit_baseline(
        {
            "stops": [
                {
                    "city": "Bellingham",
                    "lat": v.lat,
                    "lon": v.lon,
                    "venue": "The Anchor Tavern",
                    "opener": "Big Thief",
                }
            ]
        },
        state,
        client(),
    )
    assert audit["metrics"]["venues_verified"] == 1 and audit["metrics"]["openers_verified"] == 1


async def test_plan_tour_shape(pnw_request):
    out = await plan_tour(
        pnw_request,
        resolve_region("pnw"),
        ScriptedAgentLLM(),
        ScriptedBaselineLLM(),
        client(),
        client(),
        [],
        12,
        NoopSpan(),
    )
    g = out["grounded"]
    assert g["route"]["stops"] and all(s["why"] for s in g["route"]["stops"])
    assert g["metrics"]["mean_affinity"] > out["baseline"]["audit"]["metrics"]["mean_affinity"]
    assert out["baseline"]["audit"]["metrics"]["venues_verified"] == 0
    assert out["qloo_calls"] and out["audit_calls"]
    assert {"lat", "lon", "affinity"} <= set(g["route"]["stops"][0]["hotspots"][0])


async def test_plan_tour_without_baseline(pnw_request):
    out = await plan_tour(
        pnw_request,
        resolve_region("pnw"),
        ScriptedAgentLLM(),
        ScriptedBaselineLLM(),
        client(),
        client(),
        [],
        12,
        NoopSpan(),
        include_baseline=False,
    )
    assert out["baseline"]["audit"] is None and out["audit_calls"] == []


async def test_baseline_llm_error(pnw_request):
    out = await plan_tour(
        pnw_request,
        resolve_region("pnw"),
        ScriptedAgentLLM(),
        FailingLLM(),
        client(),
        client(),
        [],
        12,
        NoopSpan(),
    )
    assert out["baseline"]["error"] == "boom" and out["baseline"]["audit"] is None


def test_grounded_metrics_empty(pnw_request):
    assert grounded_metrics(ToolState(pnw_request, resolve_region("pnw"))) == {}
