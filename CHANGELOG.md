# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-13

First tagged release (Qloo Agentic Hackathon submission build).

### Added
- Tour-routing agent for independent musicians grounded in Qloo taste data: candidate cities, venues and openers scored against the artist's audience, budget/break-even and day-by-day route.
- FastAPI JSON API (`/api/plan`, `/api/config`, `/health`) and a single-page Leaflet UI comparing the Qloo-grounded plan with an LLM-only baseline.
- Mock Qloo fixtures and a scripted LLM so the demo runs without keys; optional LangSmith tracing; Dockerfile with healthcheck.

### Security / fixes
- `X-Forwarded-For` trusted only from `FORWARDED_ALLOW_IPS` (default 127.0.0.1) so the per-IP rate limiter cannot be spoofed; rate-limiter evicts idle clients; 502 responses no longer leak upstream error text.
- Tests isolate `QLOO_MOCK` from the ambient environment.
