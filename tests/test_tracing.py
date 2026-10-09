import sys
import types

from app.tracing import LangSmithSpan, NoopSpan, Tracer


def test_disabled_without_key():
    t = Tracer("")
    assert not t.enabled
    span = t.root("x", {})
    assert isinstance(span, NoopSpan) and span.url is None
    span.child("c", "tool", {}).end({"ok": 1})


class FakeRun:
    created: list["FakeRun"] = []

    def __init__(self, name="root", **kw):
        self.name, self.kw, self.posted, self.patched, self.ended = name, kw, 0, 0, None
        FakeRun.created.append(self)

    def post(self):
        self.posted += 1

    def patch(self):
        self.patched += 1

    def end(self, outputs=None, error=None):
        self.ended = (outputs, error)

    def create_child(self, name, run_type, inputs):
        return FakeRun(name=name, run_type=run_type, inputs=inputs)

    def get_url(self):
        return "https://smith.langchain.com/o/x/projects/p/y/r/z"


def install_fake_langsmith(monkeypatch):
    ls = types.ModuleType("langsmith")
    ls.Client = lambda **kw: ("client", kw)
    rt = types.ModuleType("langsmith.run_trees")
    rt.RunTree = FakeRun
    monkeypatch.setitem(sys.modules, "langsmith", ls)
    monkeypatch.setitem(sys.modules, "langsmith.run_trees", rt)


def test_langsmith_spans(monkeypatch):
    install_fake_langsmith(monkeypatch)
    FakeRun.created.clear()
    t = Tracer("ls-key", "roadie-test", "https://eu.api.smith.langchain.com")
    assert t.enabled and t._client == (
        "client",
        {"api_key": "ls-key", "api_url": "https://eu.api.smith.langchain.com"},
    )
    root = t.root("plan", {"a": 1})
    assert isinstance(root, LangSmithSpan)
    child = root.child("rank_cities", "tool", {"limit": 3})
    child.end({"n": 3})
    root.end({"done": True})
    r, c = FakeRun.created
    assert r.kw["project_name"] == "roadie-test" and r.posted == 1 and r.patched == 1
    assert c.kw["run_type"] == "tool" and c.posted == 1 and c.ended == ({"n": 3}, None)
    assert root.url.startswith("https://smith.langchain.com/")
    assert child.url is None  # only the root exposes a link


def test_tracing_failures_never_raise(monkeypatch):
    install_fake_langsmith(monkeypatch)

    class Broken(FakeRun):
        def post(self):
            raise RuntimeError("network")

        def get_url(self):
            raise RuntimeError("no url")

        def create_child(self, *a, **k):
            raise RuntimeError("child")

        def end(self, *a, **k):
            raise RuntimeError("end")

    sys.modules["langsmith.run_trees"].RunTree = Broken
    t = Tracer("k")
    assert isinstance(t.root("x", {}), NoopSpan)
    span = LangSmithSpan(Broken(), root=True)
    assert isinstance(span.child("c", "tool", {}), NoopSpan)
    span.end({})
    assert span.url is None


def test_missing_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "langsmith", None)
    assert not Tracer("k").enabled
