"""Tiny TTL cache with per-bucket TTLs and hit/miss counters."""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional, Tuple


class TTLCache:
    def __init__(self, ttl_seconds: int):
        self.ttl = ttl_seconds
        self._store: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        now = time.time()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            expires, value = entry
            if expires < now:
                del self._store[key]
                self.misses += 1
                return None
            self.hits += 1
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if len(self._store) > 4096:
                cutoff = time.time()
                for k in [k for k, (exp, _) in self._store.items() if exp < cutoff]:
                    del self._store[k]
            self._store[key] = (time.time() + self.ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


class CacheBucket:
    def __init__(self, ttls: Dict[str, int]):
        self._buckets = {name: TTLCache(ttl) for name, ttl in ttls.items()}

    def for_kind(self, kind: str) -> TTLCache:
        return self._buckets.setdefault(kind, TTLCache(300))

    def stats(self) -> dict:
        return {name: {"ttl": c.ttl, "entries": len(c._store),
                       "hits": c.hits, "misses": c.misses}
                for name, c in self._buckets.items()}
