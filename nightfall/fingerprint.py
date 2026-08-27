"""Dynamic header & fingerprint spoofing for upstream evasion.

Rotates device fingerprints (brand/model/os_version/lang/timezone) and injects
browser-like headers per request to avoid upstream fingerprinting.
"""
from __future__ import annotations

import contextvars
import random
import time
import threading
from typing import Any, Dict, List, Optional

_request_profile: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar("_fp_request_profile", default=None)

# Fallback profiles if config missing
DEFAULT_PROFILES = [
    {"brand": "Google", "model": "Pixel 8 Pro", "os_version": "14", "lang": "en", "timezone": "America/New_York"},
    {"brand": "Google", "model": "Pixel 7", "os_version": "14", "lang": "en", "timezone": "America/Los_Angeles"},
    {"brand": "Samsung", "model": "SM-S928B", "os_version": "14", "lang": "en", "timezone": "America/New_York"},
    {"brand": "Xiaomi", "model": "2304FPN6DC", "os_version": "13", "lang": "en", "timezone": "Europe/Berlin"},
    {"brand": "OnePlus", "model": "CPH2609", "os_version": "14", "lang": "en", "timezone": "Asia/Kolkata"},
]

# Chrome UA variants for header spoofing
UA_VARIANTS = [
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; 2304FPN6DC) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; CPH2609) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
]

SEC_CH_UA_POOL = [
    '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    '"Chromium";v="123", "Google Chrome";v="123", "Not-A.Brand";v="99"',
    '"Chromium";v="122", "Google Chrome";v="122", "Not-A.Brand";v="99"',
]


class FingerprintManager:
    _instance: Optional["FingerprintManager"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._cfg = self._load_cfg()
        self._profiles: List[Dict[str, Any]] = self._cfg.get("profiles") or DEFAULT_PROFILES
        self._mode: str = self._cfg.get("rotation", "per_request")
        self._interval: int = int(self._cfg.get("rotation_interval_seconds", 300))
        self._enabled: bool = bool(self._cfg.get("enabled", True))
        self._spoof_headers: bool = bool(self._cfg.get("spoof_headers", True))
        self._current: Optional[Dict[str, Any]] = None
        self._last_rotate: float = 0
        self._session_profile: Optional[Dict[str, Any]] = None
        if self._mode == "per_session":
            self._session_profile = random.choice(self._profiles)

    def _load_cfg(self) -> Dict[str, Any]:
        try:
            from .config import settings
            cfg = settings().get("fingerprint", {}) or {}
            # deep copy to avoid mutation
            return dict(cfg)
        except Exception:
            return {"enabled": True, "rotation": "per_request", "rotation_interval_seconds": 300, "spoof_headers": True, "profiles": DEFAULT_PROFILES}

    @classmethod
    def instance(cls) -> "FingerprintManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._instance = None

    def is_enabled(self) -> bool:
        return self._enabled

    def current_profile(self) -> Dict[str, Any]:
        if not self._enabled:
            return self._profiles[0] if self._profiles else DEFAULT_PROFILES[0]
        if self._mode == "per_session":
            return self._session_profile or random.choice(self._profiles)
        if self._mode == "timed":
            now = time.time()
            with self._lock:
                if self._current is None or (now - self._last_rotate) > self._interval:
                    self._current = random.choice(self._profiles)
                    self._last_rotate = now
                return self._current
        # per_request — one profile per request context (ContextVar) for consistency
        cur = _request_profile.get()
        if cur is None:
            cur = random.choice(self._profiles)
            _request_profile.set(cur)
        return cur

    def reset_request(self) -> None:
        try:
            _request_profile.set(None)
        except Exception:
            pass

    def spoofed_headers(self, base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Return dynamic spoofed headers to merge onto base."""
        if not self._enabled or not self._spoof_headers:
            return {}
        prof = self.current_profile()
        # browser-like headers
        hdrs: Dict[str, str] = {}
        # Accept-Language based on profile lang
        lang = prof.get("lang", "en")
        hdrs["Accept-Language"] = f"{lang}-US,{lang};q=0.9"
        # Sec-CH-UA
        hdrs["Sec-Ch-Ua"] = random.choice(SEC_CH_UA_POOL)
        hdrs["Sec-Ch-Ua-Mobile"] = "?1" if "Mobile" in random.choice(UA_VARIANTS) else "?0"
        hdrs["Sec-Ch-Ua-Platform"] = '"Android"'
        # jittered X-Forwarded style not needed; add DNT
        if random.random() < 0.3:
            hdrs["DNT"] = "1"
        # cache control jitter
        if random.random() < 0.5:
            hdrs["Cache-Control"] = "no-cache"
        # Purge brand/model via X-Client hints already in base; spoof here for variation
        return hdrs

    def profile_for_identity(self, explicit_brand: Optional[str] = None) -> Dict[str, Any]:
        """Helper for DeviceIdentity to get brand/model/os for client_info."""
        prof = self.current_profile()
        # allow override
        if explicit_brand:
            # find matching
            for p in self._profiles:
                if p.get("brand") == explicit_brand:
                    return p
        return prof

    def stats(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "mode": self._mode,
            "interval": self._interval,
            "profiles": len(self._profiles),
            "current": self.current_profile() if self._enabled else None,
            "spoof_headers": self._spoof_headers,
        }


def get_manager() -> FingerprintManager:
    return FingerprintManager.instance()

def spoofed_headers(base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    return get_manager().spoofed_headers(base)

def current_profile() -> Dict[str, Any]:
    return get_manager().current_profile()
