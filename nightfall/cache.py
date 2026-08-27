"""Tiny TTL cache with per-bucket TTLs, hit/miss counters, + distributed backends (memory/file/redis)."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
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

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        exp = time.time() + (ttl if ttl is not None else self.ttl)
        with self._lock:
            if len(self._store) > 4096:
                cutoff = time.time()
                for k in [k for k, (e, _) in self._store.items() if e < cutoff]:
                    del self._store[k]
            self._store[key] = (exp, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


class FileDistributedCache:
    """File-backed distributed cache: data/cache/<kind>/<hash>.json with {exp, val}."""

    def __init__(self, base_dir: Path, ttl_seconds: int):
        self.ttl = ttl_seconds
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def _path(self, key: str) -> Path:
        h = hashlib.sha256(key.encode()).hexdigest()[:16]
        # sanitize key prefix for debugging
        safe = "".join(c if c.isalnum() else "_" for c in key[:24])
        return self.base_dir / f"{safe}_{h}.json"

    def get(self, key: str) -> Optional[Any]:
        p = self._path(key)
        if not p.exists():
            self.misses += 1
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            exp = float(data.get("exp", 0))
            if exp < time.time():
                try: p.unlink()
                except: pass
                self.misses += 1
                return None
            self.hits += 1
            return data.get("val")
        except Exception:
            self.misses += 1
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        exp = time.time() + (ttl if ttl is not None else self.ttl)
        p = self._path(key)
        try:
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps({"exp": exp, "val": value}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)
        except Exception:
            pass

    def clear(self) -> None:
        for f in self.base_dir.glob("*.json"):
            try: f.unlink()
            except: pass


class RedisDistributedCache:
    """Redis-backed cache — requires redis-py. Falls back gracefully if unavailable."""

    def __init__(self, redis_url: str, ttl_seconds: int, prefix: str = "nightfall:cache:"):
        self.ttl = ttl_seconds
        self.prefix = prefix
        self.url = redis_url
        self.hits = 0
        self.misses = 0
        self._client = None
        self._available = False
        try:
            import redis  # type: ignore
            self._client = redis.Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2, decode_responses=True)
            self._client.ping()
            self._available = True
        except Exception:
            self._client = None
            self._available = False

    def _k(self, key: str) -> str:
        return self.prefix + hashlib.sha256(key.encode()).hexdigest()[:24]

    def get(self, key: str) -> Optional[Any]:
        if not self._available or self._client is None:
            self.misses += 1
            return None
        try:
            raw = self._client.get(self._k(key))
            if raw is None:
                self.misses += 1
                return None
            data = json.loads(raw)
            exp = float(data.get("exp", 0))
            if exp < time.time():
                self.misses += 1
                return None
            self.hits += 1
            return data.get("val")
        except Exception:
            self.misses += 1
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        if not self._available or self._client is None:
            return
        exp = time.time() + (ttl if ttl is not None else self.ttl)
        try:
            payload = json.dumps({"exp": exp, "val": value}, ensure_ascii=False)
            # redis ex is seconds
            ex = int(exp - time.time()) + 1
            self._client.set(self._k(key), payload, ex=max(1, ex))
        except Exception:
            pass

    def clear(self) -> None:
        if not self._available or self._client is None:
            return
        try:
            for k in self._client.scan_iter(match=self.prefix + "*"):
                self._client.delete(k)
        except Exception:
            pass


class HybridCache:
    """Memory + distributed (file/redis) hybrid. Reads memory first, then distributed, writes both."""

    def __init__(self, ttl_seconds: int, kind: str, backend: str = "memory", distributed: bool = False, redis_url: str = "", file_dir: Optional[Path] = None):
        self.ttl = ttl_seconds
        self.kind = kind
        self.backend = backend
        self.distributed = distributed
        self.mem = TTLCache(ttl_seconds)
        self.file_cache: Optional[FileDistributedCache] = None
        self.redis_cache: Optional[RedisDistributedCache] = None
        if distributed:
            if backend == "redis":
                self.redis_cache = RedisDistributedCache(redis_url or "redis://127.0.0.1:6379/0", ttl_seconds, prefix=f"nightfall:{kind}:")
            elif backend == "file":
                base = (file_dir or Path("data/cache")) / kind
                self.file_cache = FileDistributedCache(base, ttl_seconds)
            # memory backend with distributed=False is just mem

    def get(self, key: str) -> Optional[Any]:
        v = self.mem.get(key)
        if v is not None:
            return v
        # miss in mem, try distributed
        if self.file_cache:
            v = self.file_cache.get(key)
            if v is not None:
                # repopulate mem
                self.mem.set(key, v)
                return v
        if self.redis_cache:
            v = self.redis_cache.get(key)
            if v is not None:
                self.mem.set(key, v)
                return v
        return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        self.mem.set(key, value, ttl=ttl)
        if self.file_cache:
            self.file_cache.set(key, value, ttl=ttl)
        if self.redis_cache:
            self.redis_cache.set(key, value, ttl=ttl)

    def clear(self) -> None:
        self.mem.clear()
        if self.file_cache:
            self.file_cache.clear()
        if self.redis_cache:
            self.redis_cache.clear()

    @property
    def hits(self): return self.mem.hits + (self.file_cache.hits if self.file_cache else 0) + (self.redis_cache.hits if self.redis_cache else 0)
    @property
    def misses(self): return self.mem.misses  # keep simple

    def stats(self) -> dict:
        return {
            "ttl": self.ttl,
            "backend": self.backend,
            "distributed": self.distributed,
            "mem_entries": len(self.mem._store),
            "mem_hits": self.mem.hits,
            "mem_misses": self.mem.misses,
            "file_hits": self.file_cache.hits if self.file_cache else 0,
            "redis_hits": self.redis_cache.hits if self.redis_cache else 0,
            "redis_available": self.redis_cache._available if self.redis_cache else None,
        }


class CacheBucket:
    def __init__(self, ttls: Dict[str, int], backend: str = "memory", distributed: bool = False, redis_url: str = "", file_dir: Optional[Path] = None):
        # late import to avoid circular
        self._backend = backend
        self._distributed = distributed
        self._redis_url = redis_url
        self._file_dir = file_dir
        self._buckets: Dict[str, HybridCache] = {}
        for name, ttl in ttls.items():
            self._buckets[name] = HybridCache(int(ttl), kind=name, backend=backend, distributed=distributed, redis_url=redis_url, file_dir=file_dir)

    def for_kind(self, kind: str) -> HybridCache:
        if kind not in self._buckets:
            # default bucket with same backend
            self._buckets[kind] = HybridCache(300, kind=kind, backend=self._backend, distributed=self._distributed, redis_url=self._redis_url, file_dir=self._file_dir)
        return self._buckets[kind]

    def clear(self) -> None:
        for b in self._buckets.values():
            b.clear()

    def stats(self) -> dict:
        return {name: b.stats() for name, b in self._buckets.items()}
