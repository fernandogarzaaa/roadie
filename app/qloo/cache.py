"""Private server-side response cache.

The hackathon guide allows private server-side caching with no time limit,
but Qloo data must never land in a public repo. The disk cache therefore
defaults to off, and when enabled lives outside the project tree by
convention (``.cache/`` is gitignored too).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any


class ResponseCache:
    def __init__(self, ttl_s: int = 7 * 24 * 3600, max_items: int = 2048, disk_dir: str = ""):
        self.ttl_s = ttl_s
        self.max_items = max_items
        self._mem: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._dir = Path(disk_dir) if disk_dir else None
        if self._dir is not None:
            self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path | None:
        if self._dir is None:
            return None
        return self._dir / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def get(self, key: str) -> dict[str, Any] | None:
        now = time.time()
        item = self._mem.get(key)
        if item is not None:
            stored, value = item
            if now - stored <= self.ttl_s:
                self._mem.move_to_end(key)
                return value
            del self._mem[key]
        path = self._path(key)
        if path is not None and path.exists():
            try:
                blob = json.loads(path.read_text())
                if now - float(blob["stored"]) <= self.ttl_s:
                    cached: dict[str, Any] = blob["value"]
                    self._remember(key, float(blob["stored"]), cached)
                    return cached
            except (ValueError, KeyError, OSError):
                return None
        return None

    def set(self, key: str, value: dict[str, Any]) -> None:
        now = time.time()
        self._remember(key, now, value)
        path = self._path(key)
        if path is not None:
            with contextlib.suppress(OSError):
                path.write_text(json.dumps({"stored": now, "value": value}))

    def _remember(self, key: str, stored: float, value: dict[str, Any]) -> None:
        self._mem[key] = (stored, value)
        self._mem.move_to_end(key)
        while len(self._mem) > self.max_items:
            self._mem.popitem(last=False)

    def __len__(self) -> int:
        return len(self._mem)
