"""Runtime configuration, read from environment variables only."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

QLOO_HACKATHON_BASE_URL = "https://hackathon.api.qloo.com"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_LLM_MODEL = "openai/gpt-4o-mini"


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [part.strip() for part in raw.split(",") if part.strip()]


@dataclass(frozen=True)
class Settings:
    qloo_api_key: str = ""
    qloo_base_url: str = QLOO_HACKATHON_BASE_URL
    qloo_mock: bool = False
    qloo_timeout_s: float = 20.0
    qloo_cache_dir: str = ""
    qloo_cache_ttl_s: int = 7 * 24 * 3600
    venue_tags: list[str] = field(default_factory=list)

    llm_base_url: str = OPENROUTER_BASE_URL
    llm_api_key: str = ""
    llm_model: str = DEFAULT_LLM_MODEL
    llm_mock: bool = False
    llm_timeout_s: float = 60.0
    agent_max_steps: int = 12

    langsmith_api_key: str = ""
    langsmith_project: str = "roadie"
    langsmith_endpoint: str = ""

    @property
    def use_qloo_mock(self) -> bool:
        """Mock Qloo when asked to, or when no key is configured."""
        return self.qloo_mock or not self.qloo_api_key

    @property
    def use_llm_mock(self) -> bool:
        return self.llm_mock or not self.llm_api_key

    @property
    def tracing_enabled(self) -> bool:
        return bool(self.langsmith_api_key)


def load_settings() -> Settings:
    return Settings(
        qloo_api_key=os.getenv("QLOO_API_KEY", ""),
        qloo_base_url=os.getenv("QLOO_BASE_URL", QLOO_HACKATHON_BASE_URL).rstrip("/"),
        qloo_mock=_flag("QLOO_MOCK"),
        qloo_timeout_s=float(os.getenv("QLOO_TIMEOUT_S", "20")),
        qloo_cache_dir=os.getenv("QLOO_CACHE_DIR", ""),
        qloo_cache_ttl_s=int(os.getenv("QLOO_CACHE_TTL_S", str(7 * 24 * 3600))),
        venue_tags=_csv("QLOO_VENUE_TAGS"),
        llm_base_url=os.getenv("LLM_BASE_URL", OPENROUTER_BASE_URL).rstrip("/"),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_model=os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL),
        llm_mock=_flag("LLM_MOCK"),
        llm_timeout_s=float(os.getenv("LLM_TIMEOUT_S", "60")),
        agent_max_steps=int(os.getenv("AGENT_MAX_STEPS", "12")),
        langsmith_api_key=os.getenv("LANGSMITH_API_KEY", ""),
        langsmith_project=os.getenv("LANGSMITH_PROJECT", "roadie"),
        langsmith_endpoint=os.getenv("LANGSMITH_ENDPOINT", ""),
    )
