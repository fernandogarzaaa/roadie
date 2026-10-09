"""Optional LangSmith tracing of the agent run and each tool call.

Enabled when ``LANGSMITH_API_KEY`` is set (project from ``LANGSMITH_PROJECT``).
The ``langsmith`` package is an optional extra; if it is missing or the API
errors, tracing degrades to a no-op and the plan still succeeds.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

log = logging.getLogger("roadie.tracing")


class Span(Protocol):
    def child(self, name: str, run_type: str, inputs: dict[str, Any]) -> Span: ...
    def end(self, outputs: dict[str, Any] | None = None, error: str | None = None) -> None: ...
    @property
    def url(self) -> str | None: ...


class NoopSpan:
    def child(self, name: str, run_type: str, inputs: dict[str, Any]) -> NoopSpan:
        return self

    def end(self, outputs: dict[str, Any] | None = None, error: str | None = None) -> None:
        return None

    @property
    def url(self) -> str | None:
        return None


class LangSmithSpan:
    def __init__(self, run: Any, root: bool = False) -> None:
        self._run = run
        self._root = root
        self._url: str | None = None

    def child(self, name: str, run_type: str, inputs: dict[str, Any]) -> Span:
        try:
            run = self._run.create_child(name=name, run_type=run_type, inputs=inputs)
            run.post()
            return LangSmithSpan(run)
        except Exception as exc:  # tracing must never break planning
            log.warning("langsmith child failed: %s", exc)
            return NoopSpan()

    def end(self, outputs: dict[str, Any] | None = None, error: str | None = None) -> None:
        try:
            self._run.end(outputs=outputs or {}, error=error)
            self._run.patch()
        except Exception as exc:
            log.warning("langsmith end failed: %s", exc)

    @property
    def url(self) -> str | None:
        if self._url is None and self._root:
            try:
                self._url = str(self._run.get_url())
            except Exception as exc:
                log.warning("langsmith url failed: %s", exc)
        return self._url


class Tracer:
    def __init__(self, api_key: str = "", project: str = "roadie", endpoint: str = "") -> None:
        self.enabled = False
        self._client: Any = None
        self._project = project
        if not api_key:
            return
        try:
            from langsmith import Client

            kwargs: dict[str, Any] = {"api_key": api_key}
            if endpoint:
                kwargs["api_url"] = endpoint
            self._client = Client(**kwargs)
            self.enabled = True
        except Exception as exc:
            log.warning("LangSmith disabled: %s", exc)

    def root(self, name: str, inputs: dict[str, Any]) -> Span:
        if not self.enabled:
            return NoopSpan()
        try:
            from langsmith.run_trees import RunTree

            run = RunTree(
                name=name,
                run_type="chain",
                inputs=inputs,
                project_name=self._project,
                ls_client=self._client,
            )
            run.post()
            return LangSmithSpan(run, root=True)
        except Exception as exc:
            log.warning("langsmith root failed: %s", exc)
            return NoopSpan()
