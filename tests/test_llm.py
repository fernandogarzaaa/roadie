import json

import httpx
import pytest
import respx

from app.agent.llm import LLMError, LLMReply, OpenAICompatibleLLM, ToolCall, extract_json

OR = "https://openrouter.ai/api/v1"


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('Sure!\n```json\n{"a": {"b": "}"}}\n```', {"a": {"b": "}"}}),
        ('{bad} then {"ok": true}', {"ok": True}),
        ('{"s": "quote \\" inside"}', {"s": 'quote " inside'}),
        ("no json", None),
        ("", None),
        ("[1, 2]", None),
    ],
)
def test_extract_json(text, expected):
    assert extract_json(text) == expected


def test_as_message_roundtrip():
    msg = LLMReply("hi", [ToolCall("c1", "rank_cities", {"limit": 3})]).as_message()
    assert msg["tool_calls"][0]["function"] == {"name": "rank_cities", "arguments": '{"limit": 3}'}
    assert LLMReply("").as_message() == {"role": "assistant", "content": None}


@respx.mock
async def test_openrouter_request_and_tool_parse():
    route = respx.post(f"{OR}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "t1",
                                    "type": "function",
                                    "function": {
                                        "name": "rank_cities",
                                        "arguments": '{"limit": 5}',
                                    },
                                },
                                {"function": {"name": "find_venues", "arguments": "not json"}},
                            ],
                        }
                    }
                ],
                "usage": {"total_tokens": 42},
            },
        )
    )
    llm = OpenAICompatibleLLM(OR, "sk-or", "openai/gpt-4o-mini")
    reply = await llm.chat([{"role": "user", "content": "x"}], [{"type": "function"}], 300)
    req = route.calls.last.request
    body = json.loads(req.content)
    assert req.headers["authorization"] == "Bearer sk-or"
    assert req.headers["x-title"] == "Roadie"
    assert body["model"] == "openai/gpt-4o-mini" and body["tool_choice"] == "auto"
    assert body["max_tokens"] == 300
    assert [(c.id, c.name, c.arguments) for c in reply.tool_calls] == [
        ("t1", "rank_cities", {"limit": 5}),
        ("call_1", "find_venues", {}),
    ]
    assert reply.usage == {"total_tokens": 42} and llm.name == "openai/gpt-4o-mini"


@respx.mock
async def test_non_openrouter_has_no_attribution_headers_and_no_tools_key():
    base = "https://api.tokenfactory.nebius.com/v1"
    route = respx.post(f"{base}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    )
    reply = await OpenAICompatibleLLM(base, "k", "m").chat([{"role": "user", "content": "x"}])
    assert reply.content == "ok" and not reply.tool_calls
    assert "x-title" not in route.calls.last.request.headers
    assert "tools" not in json.loads(route.calls.last.request.content)


@respx.mock
async def test_llm_errors():
    respx.post(f"{OR}/chat/completions").mock(
        side_effect=[httpx.Response(401, text="bad key"), httpx.Response(200, json={"choices": []})]
    )
    llm = OpenAICompatibleLLM(OR, "k", "m")
    with pytest.raises(LLMError, match="401"):
        await llm.chat([])
    with pytest.raises(LLMError, match="unexpected"):
        await llm.chat([])
