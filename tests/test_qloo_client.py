"""Mocked-HTTP tests (respx) proving the client follows the hackathon guide."""

import httpx
import pytest
import respx

from app.qloo.cache import ResponseCache
from app.qloo.client import QlooClient, QlooError, encode_params

BASE = "https://hackathon.api.qloo.com"


def make(**kw) -> QlooClient:
    return QlooClient(api_key="secret-key", **kw)


def test_encode_params():
    assert encode_params(
        {
            "b": ["x", "y"],
            "a": True,
            "c": None,
            "d": "",
            "e": 3,
            "f": [],
            "g": False,
            "h": 0.5,
        }
    ) == {"a": "true", "b": "x,y", "e": "3", "g": "false", "h": "0.5"}


@respx.mock
async def test_insights_is_get_with_header_and_query():
    route = respx.get(f"{BASE}/v2/insights").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "results": {
                    "entities": [
                        {
                            "entity_id": "D1",
                            "name": "Portland",
                            "query": {"affinity": 0.9},
                            "location": {"lat": 45.5, "lon": -122.6},
                        }
                    ]
                },
            },
        )
    )
    client = make()
    out = await client.insight_entities(
        "urn:entity:destination", **{"signal.interests.entities": ["A", "B"]}, take=10
    )
    assert out[0].name == "Portland"
    req = route.calls.last.request
    assert req.method == "GET"
    assert req.headers["x-api-key"] == "secret-key"
    assert "authorization" not in req.headers
    assert req.content == b""
    assert req.url.params["filter.type"] == "urn:entity:destination"
    assert req.url.params["signal.interests.entities"] == "A,B"
    assert "secret-key" not in str(req.url)
    rec = client.calls[-1]
    assert (rec.path, rec.status, rec.result_count, rec.cached) == ("/v2/insights", 200, 1, False)
    assert "secret-key" not in str(rec.to_dict())
    await client.aclose()


@respx.mock
async def test_dunder_kwargs_become_dotted():
    route = respx.get(f"{BASE}/v2/insights").mock(return_value=httpx.Response(200, json={}))
    await make().insights("urn:entity:place", signal__interests__entities="A")
    assert route.calls.last.request.url.params["signal.interests.entities"] == "A"


@pytest.mark.parametrize("path", ["/recs", "/recommendations", "/recs/x", "/v1/recommendations"])
async def test_legacy_endpoints_refused_before_network(path):
    with respx.mock(assert_all_called=False) as router:
        with pytest.raises(ValueError, match="legacy"):
            await make().get(path, {})
        assert router.calls.call_count == 0


async def test_path_and_type_validation():
    client = make()
    with pytest.raises(ValueError):
        await client.get("v2/insights", {})
    with pytest.raises(ValueError):
        await client.insights("")
    with pytest.raises(ValueError, match="take"):
        await client.insights("urn:entity:artist", take=51)
    with pytest.raises(ValueError, match="take"):
        await client.insights("urn:entity:artist", take=0)


@respx.mock
@pytest.mark.parametrize(
    "status,body,msg",
    [
        (401, {"message": "No API key found in request"}, "No API key"),
        (403, {"error": "You do not have permission to access urn:entity:music"}, "permission"),
        (400, {"reason": "Bad Request"}, "Bad Request"),
    ],
)
async def test_errors_raise_with_message(status, body, msg):
    respx.get(f"{BASE}/v2/insights").mock(return_value=httpx.Response(status, json=body))
    client = make(max_retries=0)
    with pytest.raises(QlooError) as info:
        await client.insights("urn:entity:artist")
    assert info.value.status == status and msg in info.value.message
    assert client.calls[-1].status == status


@respx.mock
async def test_non_json_error_and_body():
    respx.get(f"{BASE}/search").mock(
        side_effect=[
            httpx.Response(404, text="nope"),
            httpx.Response(200, text="not json"),
            httpx.Response(200, json=[1, 2]),
        ]
    )
    client = make(max_retries=0)
    with pytest.raises(QlooError, match="nope"):
        await client.search("x")
    with pytest.raises(QlooError, match="not JSON"):
        await client.search("x")
    with pytest.raises(QlooError, match="not an object"):
        await client.search("x")


@respx.mock
async def test_retries_429_then_succeeds(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr("app.qloo.client.asyncio.sleep", fake_sleep)
    route = respx.get(f"{BASE}/search").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "3"}),
            httpx.Response(503),
            httpx.Response(200, json={"results": [{"entity_id": "1", "name": "A"}]}),
        ]
    )
    out = await make().search("A")
    assert [e.name for e in out] == ["A"] and route.call_count == 3
    assert sleeps == [3.0, 1.0]


@respx.mock
async def test_gives_up_after_max_retries(monkeypatch):
    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.qloo.client.asyncio.sleep", fake_sleep)
    route = respx.get(f"{BASE}/search").mock(return_value=httpx.Response(429))
    with pytest.raises(QlooError):
        await make(max_retries=2).search("A")
    assert route.call_count == 3


@respx.mock
async def test_cache_prevents_second_request():
    route = respx.get(f"{BASE}/search").mock(
        return_value=httpx.Response(200, json={"results": [{"entity_id": "1", "name": "A"}]})
    )
    client = make(cache=ResponseCache())
    await client.search("A")
    await client.search("A")
    assert route.call_count == 1
    assert [c.cached for c in client.calls] == [False, True]


@respx.mock
async def test_search_tags_compare_heatmap_paths(monkeypatch):
    s = respx.get(f"{BASE}/search").mock(return_value=httpx.Response(200, json={"results": []}))
    t = respx.get(f"{BASE}/v2/tags").mock(
        return_value=httpx.Response(
            200, json={"results": {"tags": [{"id": "urn:tag:x", "name": "X"}]}}
        )
    )
    c = respx.get(f"{BASE}/v2/analysis/compare").mock(return_value=httpx.Response(200, json={}))
    i = respx.get(f"{BASE}/v2/insights").mock(
        return_value=httpx.Response(200, json={"results": {"heatmap": []}})
    )
    client = make()
    await client.search("Big Thief", types=["urn:entity:artist"], take=3)
    assert s.calls.last.request.url.params["types"] == "urn:entity:artist"
    assert (await client.tags("music venue", filter__tag__types="urn:tag:category:place"))[
        0
    ].name == "X"
    assert t.calls.last.request.url.params["filter.query"] == "music venue"
    assert t.calls.last.request.url.params["filter.tag.types"] == "urn:tag:category:place"
    await client.compare(["A"], ["B", "C"])
    p = c.calls.last.request.url.params
    assert (p["a.signal.interests.entities"], p["b.signal.interests.entities"]) == ("A", "B,C")
    await client.heatmap(**{"filter.location": "POINT(1 2)", "signal.interests.entities": "A"})
    assert i.calls.last.request.url.params["filter.type"] == "urn:heatmap"
    assert "output.heatmap.boundary" not in i.calls.last.request.url.params
    monkeypatch.setattr("app.qloo.constants.HEATMAP_BOUNDARY", "urn:entity:locality")
    await client.heatmap(**{"filter.location": "POINT(1 2)"})
    assert i.calls.last.request.url.params["output.heatmap.boundary"] == "urn:entity:locality"
