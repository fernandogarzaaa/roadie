"""FastAPI app: JSON API + the single-page UI."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import time
from collections import OrderedDict, defaultdict, deque
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from app import __version__
from app.agent.llm import ChatModel, OpenAICompatibleLLM
from app.agent.orchestrator import plan_tour
from app.agent.scripted import ScriptedAgentLLM, ScriptedBaselineLLM
from app.agent.tools import TourRequest
from app.config import Settings, load_settings
from app.geo import REGIONS, resolve_region
from app.qloo import constants as C
from app.qloo.cache import ResponseCache
from app.qloo.client import QlooClient
from app.qloo.mock import MOCK_KEY, mock_transport
from app.tracing import Tracer

log = logging.getLogger("roadie")
STATIC = Path(__file__).parent / "static"

DEMO_PRESETS = [
    {
        "label": "Indie folk · Pacific Northwest",
        "artists": ["Phoebe Bridgers", "Big Thief"],
        "home_region": "Pacific Northwest",
        "tour_days": 10,
        "budget_usd": 6000,
        "band_size": 3,
    },
    {
        "label": "Psych-soul · Texas",
        "artists": ["Khruangbin"],
        "home_region": "Texas",
        "tour_days": 8,
        "budget_usd": 5000,
        "band_size": 3,
    },
    {
        "label": "OPM folk-pop · Luzon",
        "artists": ["Ben&Ben"],
        "home_region": "Luzon, Philippines",
        "tour_days": 7,
        "budget_usd": 3000,
        "band_size": 4,
    },
    {
        "label": "Hardcore · Northeast",
        "artists": ["Turnstile"],
        "home_region": "Northeast US",
        "tour_days": 9,
        "budget_usd": 5500,
        "band_size": 4,
    },
]


class PlanRequest(BaseModel):
    artists: list[str] = Field(min_length=1, max_length=5)
    home_region: str = Field(min_length=1, max_length=80)
    tour_days: int = Field(ge=2, le=45)
    budget_usd: float = Field(gt=0, le=1_000_000)
    band_size: int = Field(default=3, ge=1, le=12)
    start_date: date | None = None
    local_openers: list[str] = Field(default_factory=list, max_length=8)
    include_baseline: bool = True

    @field_validator("artists", "local_openers")
    @classmethod
    def _clean(cls, values: list[str]) -> list[str]:
        cleaned = [v.strip()[:80] for v in values if v and v.strip()]
        return cleaned

    @field_validator("artists")
    @classmethod
    def _need_one(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("at least one artist is required")
        return values


class RateLimiter:
    def __init__(self, limit: int, window_s: float) -> None:
        self.limit, self.window_s = limit, window_s
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.window_s:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        self._evict(now)
        return True

    def _evict(self, now: float) -> None:
        # Drop clients whose whole window has expired so the dict does not grow
        # forever on a public demo. Amortised: only sweep when it has grown.
        if len(self.hits) < 1024:
            return
        stale = [k for k, q in self.hits.items() if not q or now - q[-1] > self.window_s]
        for k in stale:
            del self.hits[k]


class Runtime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cache = ResponseCache(settings.qloo_cache_ttl_s, disk_dir=settings.qloo_cache_dir)
        self.transport = mock_transport() if settings.use_qloo_mock else None
        self.tracer = Tracer(
            settings.langsmith_api_key, settings.langsmith_project, settings.langsmith_endpoint
        )
        self.plans: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.limiter = RateLimiter(int(os.getenv("RATE_LIMIT_PER_10MIN", "12")), 600)

    def qloo(self) -> QlooClient:
        s = self.settings
        return QlooClient(
            api_key=MOCK_KEY if s.use_qloo_mock else s.qloo_api_key,
            base_url=s.qloo_base_url,
            timeout_s=s.qloo_timeout_s,
            transport=self.transport,
            cache=self.cache,
        )

    def llms(self) -> tuple[ChatModel, ChatModel]:
        s = self.settings
        if s.use_llm_mock:
            return ScriptedAgentLLM(), ScriptedBaselineLLM()
        real = OpenAICompatibleLLM(s.llm_base_url, s.llm_api_key, s.llm_model, s.llm_timeout_s)
        return real, real

    def modes(self) -> dict[str, Any]:
        s = self.settings
        return {
            "qloo": "mock" if s.use_qloo_mock else "live",
            "qloo_base_url": s.qloo_base_url,
            "llm": "scripted-mock" if s.use_llm_mock else s.llm_model,
            "llm_base_url": None if s.use_llm_mock else s.llm_base_url,
            "tracing": self.tracer.enabled,
            "langsmith_project": s.langsmith_project if self.tracer.enabled else None,
        }

    async def prewarm(self) -> None:
        """Warm the private cache with demo artists so the presets respond fast."""
        names = {a for p in DEMO_PRESETS for a in p["artists"]}  # type: ignore[attr-defined]
        client = self.qloo()
        try:
            for name in sorted(names):
                with contextlib.suppress(Exception):
                    await client.search(name, types=[C.TYPE_ARTIST], take=3)
        finally:
            await client.aclose()


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime = Runtime(settings or load_settings())

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = None
        if os.getenv("PREWARM", "1") == "1":
            task = asyncio.create_task(runtime.prewarm())
        yield
        if task is not None and not task.done():
            task.cancel()

    app = FastAPI(title="Roadie", version=__version__, lifespan=lifespan)
    app.state.runtime = runtime
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/config")
    async def config() -> dict[str, Any]:
        return {
            "modes": runtime.modes(),
            "presets": DEMO_PRESETS,
            "regions": [r.name for r in REGIONS],
            "version": __version__,
        }

    @app.post("/api/plan")
    async def plan(body: PlanRequest, request: Request) -> JSONResponse:
        client_ip = request.client.host if request.client else "anon"
        try:
            region = resolve_region(body.home_region)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        key = hashlib.sha256(
            json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()
        if key in runtime.plans:
            runtime.plans.move_to_end(key)
            return JSONResponse({**runtime.plans[key], "cached": True})
        if not runtime.limiter.allow(client_ip):
            raise HTTPException(status_code=429, detail="Too many plans; try again shortly.")
        req = TourRequest(
            artists=body.artists,
            home_region=body.home_region,
            tour_days=body.tour_days,
            budget_usd=body.budget_usd,
            band_size=body.band_size,
            start_date=body.start_date,
            local_openers=body.local_openers,
        )
        agent_llm, baseline_llm = runtime.llms()
        client, audit_client = runtime.qloo(), runtime.qloo()
        span = runtime.tracer.root("roadie.plan_tour", body.model_dump(mode="json"))
        started = time.perf_counter()
        try:
            result = await plan_tour(
                req,
                region,
                agent_llm,
                baseline_llm,
                client,
                audit_client,
                runtime.settings.venue_tags,
                runtime.settings.agent_max_steps,
                span,
                include_baseline=body.include_baseline,
            )
        except Exception as exc:
            span.end(error=str(exc))
            log.exception("plan failed")
            raise HTTPException(status_code=502, detail="planning failed; see server logs") from exc
        finally:
            await client.aclose()
            await audit_client.aclose()
        route = result["grounded"]["route"] or {}
        span.end(
            {"stops": [s["city"] for s in route.get("stops", [])], "warnings": result["warnings"]}
        )
        result.update(
            {
                "modes": runtime.modes(),
                "trace_url": span.url,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "cached": False,
            }
        )
        runtime.plans[key] = result
        while len(runtime.plans) > 64:
            runtime.plans.popitem(last=False)
        return JSONResponse(result)

    return app


app = create_app()
