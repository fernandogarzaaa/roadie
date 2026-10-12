"""Security regressions from the Oct 2026 audit."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import RateLimiter, create_app

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_does_not_trust_every_proxy_by_default():
    text = (ROOT / "Dockerfile").read_text()
    cmd = next(line for line in text.splitlines() if line.startswith("CMD"))
    assert "--forwarded-allow-ips='*'" not in cmd
    assert "FORWARDED_ALLOW_IPS" in cmd
    m = re.search(r"FORWARDED_ALLOW_IPS=(\S+)", text)
    assert m and m.group(1) == "127.0.0.1"


def test_rate_limiter_evicts_expired_clients(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("app.main.time.monotonic", lambda: now[0])
    rl = RateLimiter(1, 10)
    for i in range(1100):
        assert rl.allow(f"ip-{i}")
    now[0] = 100.0
    assert rl.allow("fresh")
    assert len(rl.hits) < 10


def test_plan_failure_does_not_leak_exception_text(monkeypatch):
    monkeypatch.setenv("PREWARM", "0")

    async def boom(*a, **k):
        raise RuntimeError("upstream secret detail sk-123")

    monkeypatch.setattr("app.main.plan_tour", boom)
    with TestClient(create_app(Settings(qloo_mock=True, llm_mock=True))) as c:
        r = c.post(
            "/api/plan",
            json={
                "artists": ["Phoebe Bridgers"],
                "home_region": "Pacific Northwest",
                "tour_days": 8,
                "budget_usd": 5000,
            },
        )
    assert r.status_code == 502
    assert "sk-123" not in r.text


def _clock(monkeypatch, start=0.0):
    now = [start]
    monkeypatch.setattr("app.main.time.monotonic", lambda: now[0])
    return now


def test_rate_limiter_window_boundary(monkeypatch):
    now = _clock(monkeypatch)
    rl = RateLimiter(1, 10)
    assert rl.allow("a") is True
    now[0] = 10.0
    assert rl.allow("a") is False  # exactly one window later: still counted
    now[0] = 10.5
    assert rl.allow("a") is True


def test_eviction_sweeps_at_threshold(monkeypatch):
    now = _clock(monkeypatch)
    rl = RateLimiter(1, 10)
    for i in range(1023):
        rl.allow(f"old-{i}")
    now[0] = 100.0
    rl.allow("new")  # dict reaches 1024 -> sweep runs
    assert set(rl.hits) == {"new"}


def test_eviction_below_threshold_keeps_entries(monkeypatch):
    now = _clock(monkeypatch)
    rl = RateLimiter(1, 10)
    for i in range(1022):
        rl.allow(f"old-{i}")
    now[0] = 100.0
    rl.allow("new")  # 1023 entries: no sweep yet
    assert len(rl.hits) == 1023


def test_eviction_keeps_clients_still_inside_window(monkeypatch):
    now = _clock(monkeypatch, 5.0)
    rl = RateLimiter(1, 10)
    for i in range(1023):
        rl.allow(f"old-{i}")
    now[0] = 15.0  # exactly one window after their hit: not stale yet
    rl.allow("new")
    assert len(rl.hits) == 1024
    assert rl.allow("old-0") is False
