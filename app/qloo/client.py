"""Async Qloo client that follows the hackathon developer guide exactly.

* Base URL: https://hackathon.api.qloo.com (hackathon keys fail elsewhere).
* Auth: ``X-Api-Key`` header, never a query param or bearer token.
* ``/v2/insights`` is GET with query-string params, never POST/JSON.
* ``/search`` and ``/v2/tags`` are separate endpoints.
* Legacy ``/recs`` and ``/recommendations`` are refused locally.

Every request is recorded (path + params, never the key) so the UI can show
exactly which Qloo calls grounded each answer.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.qloo import constants as C
from app.qloo.cache import ResponseCache
from app.qloo.models import (
    Entity,
    HeatPoint,
    Tag,
    parse_heatmap,
    parse_insight_entities,
    parse_search_results,
    parse_tags,
)

FORBIDDEN_PATHS = C.FORBIDDEN_PATHS


class QlooError(RuntimeError):
    def __init__(self, status: int, message: str, path: str) -> None:
        super().__init__(f"Qloo {path} -> HTTP {status}: {message}")
        self.status = status
        self.path = path
        self.message = message


@dataclass
class CallRecord:
    path: str
    params: dict[str, str]
    status: int
    duration_ms: int
    result_count: int
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "params": self.params,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "result_count": self.result_count,
            "cached": self.cached,
        }


ParamValue = str | int | float | bool | Sequence[str] | None


def encode_params(params: Mapping[str, ParamValue]) -> dict[str, str]:
    """Flatten params for a GET query string.

    Lists become comma-separated strings (Qloo's documented format for
    ``signal.interests.entities``, ``filter.tags`` etc.); ``None`` is dropped;
    booleans become ``true``/``false``.
    """
    out: dict[str, str] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        elif isinstance(value, str):
            if value != "":
                out[key] = value
        elif isinstance(value, int | float):
            out[key] = str(value)
        else:
            items = [str(v) for v in value if str(v)]
            if items:
                out[key] = ",".join(items)
    return dict(sorted(out.items()))


@dataclass
class QlooClient:
    api_key: str
    base_url: str = "https://hackathon.api.qloo.com"
    timeout_s: float = 20.0
    transport: httpx.AsyncBaseTransport | None = None
    cache: ResponseCache | None = None
    max_retries: int = 2
    calls: list[CallRecord] = field(default_factory=list)
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"X-Api-Key": self.api_key, "Accept": "application/json"},
                timeout=self.timeout_s,
                transport=self.transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get(self, path: str, params: Mapping[str, ParamValue]) -> dict[str, Any]:
        if not path.startswith("/"):
            raise ValueError("Qloo path must start with '/'")
        if any(path.rstrip("/") == p or path.startswith(p + "/") for p in FORBIDDEN_PATHS):
            raise ValueError(f"{path} is a legacy endpoint not allowed in the hackathon")
        query = encode_params(params)
        cache_key = path + "?" + "&".join(f"{k}={v}" for k, v in query.items())
        if self.cache is not None:
            hit = self.cache.get(cache_key)
            if hit is not None:
                self.calls.append(CallRecord(path, query, 200, 0, _count(hit), cached=True))
                return hit

        attempt = 0
        while True:
            start = time.perf_counter()
            resp = await self._http().get(path, params=query)
            elapsed = int((time.perf_counter() - start) * 1000)
            if resp.status_code == 429 and attempt < self.max_retries:
                attempt += 1
                await asyncio.sleep(_retry_after(resp, attempt))
                continue
            if resp.status_code >= 500 and attempt < self.max_retries:
                attempt += 1
                await asyncio.sleep(0.5 * attempt)
                continue
            break

        if resp.status_code >= 400:
            self.calls.append(CallRecord(path, query, resp.status_code, elapsed, 0))
            raise QlooError(resp.status_code, _error_message(resp), path)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise QlooError(resp.status_code, "response was not JSON", path) from exc
        if not isinstance(payload, dict):
            raise QlooError(resp.status_code, "response JSON was not an object", path)
        self.calls.append(CallRecord(path, query, resp.status_code, elapsed, _count(payload)))
        if self.cache is not None:
            self.cache.set(cache_key, payload)
        return payload

    # ---- documented endpoints -------------------------------------------------

    async def search(
        self,
        query: str,
        types: Iterable[str] = (C.TYPE_ARTIST,),
        take: int = 5,
        **extra: Any,
    ) -> list[Entity]:
        params: dict[str, ParamValue] = {"query": query, "types": list(types), "take": take}
        params.update(extra)
        return parse_search_results(await self.get(C.PATH_SEARCH, params))

    async def insights(self, filter_type: str, **params: Any) -> dict[str, Any]:
        if not filter_type:
            raise ValueError("filter.type is required for /v2/insights")
        take = params.get("take")
        if isinstance(take, int) and not 1 <= take <= C.TAKE_MAX:
            raise ValueError(f"take must be 1..{C.TAKE_MAX}")
        full: dict[str, ParamValue] = {"filter.type": filter_type}
        full.update({_dotted(k): v for k, v in params.items()})
        return await self.get(C.PATH_INSIGHTS, full)

    async def insight_entities(self, filter_type: str, **params: Any) -> list[Entity]:
        return parse_insight_entities(await self.insights(filter_type, **params))

    async def heatmap(self, **params: Any) -> list[HeatPoint]:
        extra: dict[str, ParamValue] = {}
        if C.HEATMAP_BOUNDARY and "output__heatmap__boundary" not in params:
            extra["output.heatmap.boundary"] = C.HEATMAP_BOUNDARY
        return parse_heatmap(await self.insights(C.TYPE_HEATMAP, **params, **extra))

    async def tags(self, query: str, take: int = 10, **extra: Any) -> list[Tag]:
        params: dict[str, ParamValue] = {"filter.query": query, "take": take}
        params.update({_dotted(k): v for k, v in extra.items()})
        return parse_tags(await self.get(C.PATH_TAGS, params))

    async def compare(
        self, a_ids: Sequence[str], b_ids: Sequence[str], take: int = 10, **extra: Any
    ) -> dict[str, Any]:
        """GET /v2/analysis/compare (not /v2/insights/compare, which does not exist)."""
        params: dict[str, ParamValue] = {
            "a.signal.interests.entities": list(a_ids),
            "b.signal.interests.entities": list(b_ids),
            "take": take,
        }
        params.update({_dotted(k): v for k, v in extra.items()})
        return await self.get(C.PATH_COMPARE, params)


def _dotted(key: str) -> str:
    """Allow python kwargs like ``signal__interests__entities``."""
    return key.replace("__", ".")


def _count(payload: Mapping[str, Any]) -> int:
    results = payload.get("results")
    if isinstance(results, list):
        return len(results)
    if isinstance(results, dict):
        for key in ("entities", "heatmap", "tags"):
            if isinstance(results.get(key), list):
                return len(results[key])
    return 0


def _retry_after(resp: httpx.Response, attempt: int) -> float:
    header = resp.headers.get("retry-after")
    try:
        return min(10.0, float(header)) if header else 0.5 * 2**attempt
    except ValueError:
        return 0.5 * 2**attempt


def _error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:200] or resp.reason_phrase
    if isinstance(body, dict):
        for key in ("message", "error", "reason"):
            if body.get(key):
                value = body[key]
                return value if isinstance(value, str) else str(value)[:200]
    return str(body)[:200]
