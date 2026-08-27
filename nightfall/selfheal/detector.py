"""Auth-failure streak detector.

Tracks consecutive signature/auth rejections from upstream. When the streak
crosses the configured threshold, the wrapper flips to PROTOCOL_STALE and
starts instructing users how to heal it.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional


class StalenessDetector:
    def __init__(self, threshold: int = 3):
        self.threshold = max(1, int(threshold))
        self._lock = threading.Lock()
        self._streak = 0
        self._stale_since: Optional[float] = None
        self._last_reason = ""

    def on_auth_failure(self, reason: str) -> None:
        with self._lock:
            self._last_reason = reason
            self._streak += 1
            if self._streak >= self.threshold and self._stale_since is None:
                self._stale_since = time.time()

    def on_success(self) -> None:
        with self._lock:
            self._streak = 0
            self._stale_since = None

    def reset(self) -> None:
        self.on_success()

    def status(self) -> dict:
        with self._lock:
            stale = self._stale_since is not None
            return {
                "state": "PROTOCOL_STALE" if stale else "HEALTHY",
                "consecutive_auth_failures": self._streak,
                "threshold": self.threshold,
                "last_reason": self._last_reason,
                "stale_since": time.strftime(
                    "%Y-%m-%dT%H:%M:%S%z", time.localtime(self._stale_since)) if stale else None,
                "remediation": (
                    "Upstream rejected our signatures repeatedly. The app likely rotated "
                    "its secrets. Download the latest MovieBox APK and drop it into the "
                    "wrapper 'watch/' folder, then POST /heal (or restart). "
                    "Run GET /doctor?live=1 to diagnose." if stale else ""),
            }


def make_callbacks(detector: StalenessDetector) -> tuple[Callable[[str], None], Callable[[], None]]:
    return detector.on_auth_failure, detector.on_success
