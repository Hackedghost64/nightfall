"""API key store + rate limiter + auth guard behavior."""
import hashlib

from fastapi import HTTPException

from nightfall.security import (ApiKeyStore, SlidingWindowRateLimiter,
                                       make_auth_dependency)


class FakeRequest:
    def __init__(self, path="/search", key=None):
        self.url = type("U", (), {"path": path})()
        self.headers = {"x-api-key": key} if key else {}
        self.query_params = {}


def test_key_create_verify_roundtrip(tmp_path):
    s = ApiKeyStore(tmp_path)
    out = s.create("test")
    assert out["plaintext"].startswith("nf_")
    assert s.verify(out["plaintext"])
    assert not s.verify("mbx_wrong")
    assert len(s.list()) == 1
    assert "sha256" not in s.list()[0]          # never leak hashes in listing


def test_revoke(tmp_path):
    s = ApiKeyStore(tmp_path)
    raw = s.create("a")["plaintext"]
    prefix = raw[:10]
    assert s.revoke(prefix)
    assert not s.verify(raw)
    assert not s.revoke(prefix)                 # already gone


def test_guard_auto_mode_enforces_only_with_keys(tmp_path):
    s = ApiKeyStore(tmp_path)
    guard = make_auth_dependency(s, "auto")
    guard(FakeRequest())                        # no keys -> open
    raw = s.create("x")["plaintext"]
    guard(FakeRequest(key=raw))                 # valid key passes
    try:
        guard(FakeRequest())
        assert False, "should have raised"
    except HTTPException as e:
        assert e.status_code == 401


def test_guard_health_always_open(tmp_path):
    s = ApiKeyStore(tmp_path)
    s.create("x")
    guard = make_auth_dependency(s, True)
    guard(FakeRequest(path="/health"))          # exempt


def test_rate_limiter_window():
    rl = SlidingWindowRateLimiter(per_minute=3)
    assert all(rl.check("ip1") for _ in range(3))
    assert not rl.check("ip1")                  # 4th within window
    assert rl.check("ip2")                      # other clients unaffected


def test_rate_limiter_disabled():
    rl = SlidingWindowRateLimiter(0)
    assert all(rl.check("any") for _ in range(100))
