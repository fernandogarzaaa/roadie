import pytest
from fastapi.testclient import TestClient

from app.config import Settings, load_settings
from app.main import RateLimiter, create_app

BODY = {
    "artists": ["Phoebe Bridgers"],
    "home_region": "Pacific Northwest",
    "tour_days": 8,
    "budget_usd": 5000,
}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("PREWARM", "0")
    with TestClient(create_app(Settings(qloo_mock=True, llm_mock=True))) as c:
        yield c


def test_health_is_cheap(api):
    assert api.get("/health").json()["ok"] is True


def test_index_and_static(api):
    assert "Roadie" in api.get("/").text
    assert api.get("/static/app.js").status_code == 200


def test_config_reports_modes(api):
    cfg = api.get("/api/config").json()
    assert cfg["modes"]["qloo"] == "mock" and cfg["modes"]["llm"] == "scripted-mock"
    assert cfg["modes"]["tracing"] is False and cfg["presets"]


def test_plan_and_cache(api):
    first = api.post("/api/plan", json=BODY)
    assert first.status_code == 200
    data = first.json()
    assert data["cached"] is False and data["trace_url"] is None
    assert data["grounded"]["route"]["stops"]
    assert data["baseline"]["audit"]["metrics"]
    for call in data["qloo_calls"]:
        assert "mock-key" not in str(call)
    assert api.post("/api/plan", json=BODY).json()["cached"] is True


@pytest.mark.parametrize(
    "patch,status",
    [
        ({"home_region": "Atlantis"}, 422),
        ({"artists": []}, 422),
        ({"artists": ["  "]}, 422),
        ({"tour_days": 1}, 422),
        ({"budget_usd": 0}, 422),
        ({"artists": ["a"] * 6}, 422),
    ],
)
def test_validation(api, patch, status):
    assert api.post("/api/plan", json={**BODY, **patch}).status_code == status


def test_rate_limit(monkeypatch):
    monkeypatch.setenv("PREWARM", "0")
    monkeypatch.setenv("RATE_LIMIT_PER_10MIN", "1")
    with TestClient(create_app(Settings(qloo_mock=True, llm_mock=True))) as c:
        assert c.post("/api/plan", json=BODY).status_code == 200
        assert c.post("/api/plan", json={**BODY, "tour_days": 9}).status_code == 429


def test_rate_limiter_window(monkeypatch):
    t = [0.0]
    monkeypatch.setattr("app.main.time.monotonic", lambda: t[0])
    rl = RateLimiter(2, 10)
    assert rl.allow("a") and rl.allow("a") and not rl.allow("a") and rl.allow("b")
    t[0] = 11
    assert rl.allow("a")


def test_prewarm_runs(monkeypatch):
    monkeypatch.setenv("PREWARM", "1")
    app = create_app(Settings(qloo_mock=True, llm_mock=True))
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200


def test_settings_from_env(monkeypatch):
    monkeypatch.delenv("QLOO_MOCK", raising=False)
    monkeypatch.setenv("QLOO_API_KEY", "q")
    monkeypatch.setenv("QLOO_BASE_URL", "https://hackathon.api.qloo.com/")
    monkeypatch.setenv("LLM_API_KEY", "l")
    monkeypatch.setenv("QLOO_VENUE_TAGS", "urn:tag:a, urn:tag:b,")
    monkeypatch.setenv("LLM_MOCK", "yes")
    s = load_settings()
    assert s.qloo_base_url == "https://hackathon.api.qloo.com"
    assert s.llm_base_url == "https://openrouter.ai/api/v1"
    assert s.venue_tags == ["urn:tag:a", "urn:tag:b"]
    assert not s.use_qloo_mock and s.use_llm_mock and not s.tracing_enabled
    assert Settings().use_qloo_mock and Settings().use_llm_mock
    assert Settings(qloo_api_key="x", qloo_mock=True).use_qloo_mock
    assert Settings(langsmith_api_key="x").tracing_enabled


def test_live_modes_reported(monkeypatch):
    monkeypatch.setenv("PREWARM", "0")
    app = create_app(Settings(qloo_api_key="q", llm_api_key="l", llm_model="m"))
    with TestClient(app) as c:
        modes = c.get("/api/config").json()["modes"]
    assert modes["qloo"] == "live" and modes["llm"] == "m"
    assert modes["qloo_base_url"] == "https://hackathon.api.qloo.com"
