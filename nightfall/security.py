"""Nightfall security: API keys + rate limiting (union of moviebox wrapper + anilab)."""
from __future__ import annotations
import hashlib, hmac, json, secrets, threading, time, os
from pathlib import Path
from typing import List, Optional
from fastapi import HTTPException, Request

class ApiKeyStore:
    FILE = "api_keys.json"
    def __init__(self, directory: Path):
        self.path = directory / self.FILE
        self._lock = threading.Lock()

    def _load(self) -> List[dict]:
        if not self.path.exists(): return []
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:
            # fail closed: corrupted file should not disable auth — treat as non-empty
            # log to stderr but keep keys inaccessible (verify will fail closed via count)
            import sys
            print(f"[security] corrupted {self.path}: {e}", file=sys.stderr)
            # return sentinel that forces auth to stay enforced, verify will deny
            return [{"_corrupted": True}]

    def _save(self, keys: List[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(keys, indent=2), encoding="utf-8")
        try: os.chmod(tmp, 0o600)
        except: pass
        tmp.replace(self.path)
        try: os.chmod(self.path, 0o600)
        except: pass
        try: os.chmod(self.path.parent, 0o700)
        except: pass

    @staticmethod
    def _hash(raw: str, salt: Optional[str] = None) -> tuple[str, str]:
        # salted pbkdf2 for new keys; fallback sha256 for legacy verification
        if salt is None:
            # legacy fast path for verify without salt (handled in verify)
            return hashlib.sha256(raw.encode()).hexdigest(), ""
        # new: pbkdf2_hmac with per-key salt
        dk = hashlib.pbkdf2_hmac("sha256", raw.encode(), salt.encode(), 100_000)
        return dk.hex(), salt

    def create(self, name: str) -> dict:
        raw = "nf_" + secrets.token_urlsafe(24)
        prefix = raw[:10]
        salt = secrets.token_hex(16)
        h, _ = self._hash(raw, salt)
        rec = {"name": name, "prefix": prefix, "sha256": h, "salt": salt, "created": int(time.time())}
        with self._lock:
            keys = [k for k in self._load() if not k.get("_corrupted")]
            keys.append(rec)
            self._save(keys)
        return {"record": {k:v for k,v in rec.items() if k not in ("sha256","salt")}, "plaintext": raw}

    def list(self) -> List[dict]:
        with self._lock:
            return [{k: v for k, v in k_.items() if k not in ("sha256","salt")} for k_ in self._load() if not k_.get("_corrupted")]

    def revoke(self, prefix: str) -> bool:
        with self._lock:
            keys = self._load()
            if any(k.get("_corrupted") for k in keys):
                return False
            kept = [k for k in keys if k["prefix"] != prefix]
            if len(kept)==len(keys): return False
            self._save(kept); return True

    def verify(self, raw: str) -> bool:
        # header-only, constant-time compare, supports both legacy sha256 and salted pbkdf2
        with self._lock:
            keys = self._load()
        if any(k.get("_corrupted") for k in keys):
            return False
        for k in keys:
            stored = k.get("sha256","")
            salt = k.get("salt")
            if salt:
                h, _ = self._hash(raw, salt)
            else:
                # legacy
                h = hashlib.sha256(raw.encode()).hexdigest()
            if hmac.compare_digest(h, stored):
                return True
        return False

    @property
    def count(self) -> int:
        with self._lock:
            keys = self._load()
            if any(k.get("_corrupted") for k in keys):
                return 1  # fail closed: enforce auth even if corrupted
            return len(keys)

    def ensure_perms(self) -> None:
        try:
            if self.path.exists(): os.chmod(self.path, 0o600)
            os.chmod(self.path.parent, 0o700)
        except: pass

class SlidingWindowRateLimiter:
    def __init__(self, per_minute: int, trusted_proxies: Optional[List[str]] = None):
        self.per_minute = max(0, per_minute)
        self.trusted_proxies = set(trusted_proxies or [])
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _client_ip(self, request: Request) -> str:
        # only trust X-Forwarded-For if direct peer is trusted proxy; else use request.client.host
        try:
            peer = request.client.host if request.client else "?"
        except Exception:
            peer = "?"
        if peer in self.trusted_proxies:
            try:
                xff = request.headers.get("x-forwarded-for","")
                if xff:
                    return xff.split(",")[0].strip()
            except Exception:
                pass
        return peer if isinstance(peer, str) else str(peer)

    def check(self, request) -> bool:
        # compat: accept Request or raw ip string (tests call check("ip1"))
        if isinstance(request, str):
            return self.check_ip(request)
        if self.per_minute <= 0: return True
        ip = self._client_ip(request)
        now=time.time(); window=now-60.0
        with self._lock:
            hits=[t for t in self._hits.get(ip,[]) if t>window]
            if len(hits)>=self.per_minute:
                self._hits[ip]=hits; return False
            hits.append(now); self._hits[ip]=hits
            if len(self._hits)>4096:
                cutoff=now-120
                for ip in [ip for ip,ts in self._hits.items() if not ts or ts[-1]<cutoff]:
                    del self._hits[ip]
            return True
    # backward compat for callers that pass ip string
    def check_ip(self, client_ip: str) -> bool:
        if self.per_minute <= 0: return True
        now=time.time(); window=now-60.0
        with self._lock:
            hits=[t for t in self._hits.get(client_ip,[]) if t>window]
            if len(hits)>=self.per_minute:
                self._hits[client_ip]=hits; return False
            hits.append(now); self._hits[client_ip]=hits
            return True

def extract_api_key(request: Request) -> Optional[str]:
    # header-only: no query param to avoid log/referrer leak
    for h in ("x-api-key", "X-API-Key", "authorization"):
        v = request.headers.get(h)
        if v:
            if h == "authorization" and v.lower().startswith("bearer "):
                return v[7:].strip()
            if h != "authorization":
                return v.strip()
    return None

def make_auth_dependency(store: ApiKeyStore, mode: str):
    public_paths = {"/health"}
    # docs are public only if no keys or mode false; otherwise require auth
    def guard(request: Request) -> None:
        if request.url.path in public_paths:
            return
        # Swagger: allow only if not enforcing; otherwise require key
        if request.url.path.startswith("/docs") or request.url.path.startswith("/openapi"):
            enforce_docs = True if mode is True else False if mode is False else store.count>0
            if not enforce_docs:
                return
            # if enforcing, fall through to key check
            if request.url.path.startswith("/docs") or request.url.path.startswith("/openapi"):
                pass  # will check key below
            else:
                return
        enforce = True if mode is True else False if mode is False else store.count>0
        if not enforce: return
        raw=extract_api_key(request)
        if not raw or not store.verify(raw):
            raise HTTPException(status_code=401, detail="invalid or missing X-API-Key (create one: nightfall key create <name>)", headers={"WWW-Authenticate":"ApiKey"})
    return guard

def generate_api_key() -> str: return secrets.token_urlsafe(32)
def load_or_create_key(path: Path) -> str:
    if path.exists():
        txt=path.read_text().strip()
        if txt: return txt
    key=generate_api_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key)
    try: import os; os.chmod(path, 0o600)
    except: pass
    print(f"🔑 API key generated: {key}")
    print(f"   Save this — it's shown once. Check {path} to retrieve later.")
    return key
