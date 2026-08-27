"""Signed upstream HTTP client.

Responsibilities:
- build full app-like header set + x-tr-signature via the active profile
- host failover across protocol hosts.primary/fallbacks
- GW.<skew> clock-drift retry (protocol.signature.time_skew_error_code)
- classify auth rejections and report them to a detector callback
- append raw req/resp evidence to logs/upstream.log for diff-debugging
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode

import httpx

from ..config import settings
from .endpoints import Endpoint
from .identity import DeviceIdentity
from .signers import SignRequest, build_profile, derive_secret_bytes


class UpstreamAuthError(RuntimeError):
    def __init__(self, status: int, body_code: str, detail: str = ""):
        super().__init__(f"upstream auth rejection: http={status} code={body_code} {detail}")
        self.status = status
        self.body_code = body_code


class UpstreamNetworkError(RuntimeError):
    pass


class UpstreamClient:
    def __init__(self, store, identity: DeviceIdentity,
                 on_auth_failure: Optional[Callable[[str], None]] = None,
                 on_success: Optional[Callable[[], None]] = None):
        self.store = store
        self.identity = identity
        self.on_auth_failure = on_auth_failure
        self.on_success = on_success
        self._time_offset_ms = 0
        self._offset_lock = threading.Lock()
        cfg = settings()
        timeout = float(cfg.get("upstream_timeout_seconds", 15))
        self._http = httpx.Client(timeout=timeout, follow_redirects=True)

    # ------------------------------------------------------------------ core

    def request(self, ep: Endpoint, params: Optional[Dict[str, Any]] = None,
                json_body: Optional[Dict[str, Any]] = None,
                extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        proto = self.store.data
        params = dict(params or {})
        if ep.needs_host_param and not params.get("host"):
            params["host"] = proto.get("hosts", {}).get("primary", "")
        query = urlencode({k: v for k, v in params.items() if v is not None})
        body_bytes: Optional[bytes] = None
        if json_body is not None:
            body_bytes = json.dumps(json_body, separators=(",", ":")).encode("utf-8")

        if not self._auth_header(proto) and self._needs_session(ep, proto):
            # cold start: mint the anonymous identity via a cheap warmup call,
            # otherwise content endpoints reject us before X-User ever arrives
            self._bootstrap_session(proto)

        last_err: Optional[Exception] = None
        for attempt in range(2):          # one retry budget for clock skew
            ts = int(time.time() * 1000) + self._get_offset()
            headers = self._build_headers(proto, ep, query, body_bytes, ts, extra_headers)
            resp, used_host = self._send_with_failover(proto, ep, query, body_bytes, headers)
            self._log_traffic(ep, used_host, query, body_bytes, headers, resp)

            body_code = ""
            try:
                parsed = resp.json()
                env = proto.get("response", {}).get("envelope_keys",
                                                   {"code": "code", "message": "message", "data": "data"})
                body_code = str(parsed.get(env["code"], ""))
            except Exception:
                parsed = None

            skew_code = proto.get("signature", {}).get("time_skew_error_code")
            if resp.status_code == 500 and skew_code and body_code == skew_code:
                if self._absorb_skew(resp.text) and attempt == 0:
                    continue
            break

        self._capture_session(resp)
        self._classify(proto, resp, body_code)
        return self._unwrap(proto, resp, parsed)

    # ------------------------------------------------------------- internals

    def _needs_session(self, ep, proto) -> bool:
        return ep.name in set(proto.get("session_token_explicit_for_endpoints") or [])

    def _bootstrap_session(self, proto) -> None:
        try:
            from .endpoints import Endpoints
            warm = Endpoints(proto["endpoints"]).get("search_suggest")
            ts = int(time.time() * 1000) + self._get_offset()
            headers = self._build_headers(proto, warm, "q=a", None, ts, None)
            resp, _ = self._send_with_failover(proto, warm, "q=a", None, headers)
            self._capture_session(resp)
        except Exception:
            pass

    def _hosts(self, proto: dict) -> List[str]:
        hosts = [proto.get("hosts", {}).get("primary")]
        hosts += list(proto.get("hosts", {}).get("fallbacks") or [])
        return [h for h in hosts if h]

    def _send_with_failover(self, proto, ep, query, body_bytes, headers):
        last_exc: Optional[Exception] = None
        for host in self._hosts(proto):
            url = f"https://{host}{ep.path}" + (f"?{query}" if query else "")
            try:
                resp = self._http.request(ep.method, url, content=body_bytes, headers=headers)
                return resp, host
            except httpx.HTTPError as exc:
                last_exc = exc
        raise UpstreamNetworkError(f"all upstream hosts failed: {last_exc}")

    def _build_headers(self, proto, ep, query, body_bytes, timestamp_ms, extra) -> Dict[str, str]:
        headers = self.identity.base_headers()
        auth = self._auth_header(proto)
        if auth:
            # f.java logged-in branch: Authorization replaces X-Client-Token
            for h in list(headers):
                if h.lower() in ("x-client-token", "token"):
                    del headers[h]
            headers["Authorization"] = auth
        if body_bytes is not None:
            headers["content-type"] = "application/json"
            headers["content-length"] = str(len(body_bytes))
        if extra:
            headers.update(extra)
        profile = build_profile(proto.get("signature", {}))
        secret = derive_secret_bytes(proto.get("signature", {}), proto.get("secrets", {}))
        sig_req = SignRequest(method=ep.method, path=ep.path, query=query,
                              headers=headers, body=body_bytes, timestamp_ms=timestamp_ms)
        sig_header = proto.get("signature", {}).get("header_name", "x-tr-signature")
        headers[sig_header] = profile.header_value(sig_req, secret)
        return headers

    def _auth_header(self, proto) -> str | None:
        """Explicit session_token (protocol.yaml) wins; else auto-harvested X-User."""
        explicit = (proto.get("session_token") or "").strip()
        if explicit:
            raw = explicit[7:].strip() if explicit.startswith("Bearer ") else explicit
            return "Bearer " + raw
        sess = self.identity.get_session()
        return ("Bearer " + sess["token"]) if sess else None

    def _capture_session(self, resp) -> None:
        """q.h(Response) parity: adopt server-pushed anonymous identity."""
        if (self.store.data.get("session_token") or "").strip():
            return
        xu = resp.headers.get("x-user")
        if not xu:
            return
        try:
            payload = json.loads(xu)
            token = payload.get("token")
            current = self.identity.get_session()
            if token and (not current or current.get("token") != token):
                self.identity.save_session(payload)
        except Exception:
            pass

    def _absorb_skew(self, error_body: str) -> bool:
        """GW.4410 carries server time; store offset like SafeStringUtils does."""
        try:
            data = json.loads(error_body)
            server_time = int(data.get("errorMsg") and json.loads(data["errorMsg"])["time"])
            with self._offset_lock:
                self._time_offset_ms = server_time - int(time.time() * 1000)
            return True
        except Exception:
            return False

    def _get_offset(self) -> int:
        with self._offset_lock:
            return self._time_offset_ms

    def _classify(self, proto, resp, body_code: str) -> None:
        signals = proto.get("auth_failure_signals", {})
        statuses = set(signals.get("http_statuses", [401, 403]))
        prefix = signals.get("body_codes_prefix", "GW.")
        rejected = resp.status_code in statuses or (
            resp.status_code >= 400 and body_code.startswith(prefix))
        if rejected:
            # expired/invalid session? drop it so the next response re-bootstraps X-User
            if body_code == "441" or resp.status_code in statuses:
                self.identity.clear_session()
            if self.on_auth_failure:
                self.on_auth_failure(f"http={resp.status_code} code={body_code}")
            raise UpstreamAuthError(resp.status_code, body_code)
        if resp.status_code >= 500:
            raise UpstreamNetworkError(f"upstream {resp.status_code}: {body_code}")
        if self.on_success:
            self.on_success()

    @staticmethod
    def _unwrap(proto, resp, parsed) -> Dict[str, Any]:
        ok_codes = set(proto.get("response", {}).get("ok_codes", ["0", "200"]))
        env = proto.get("response", {}).get("envelope_keys",
                                            {"code": "code", "message": "message", "data": "data"})
        if not isinstance(parsed, dict):
            return {"_raw": None if parsed is None else str(parsed)[:2000],
                    "_http_status": resp.status_code}
        code = str(parsed.get(env["code"], ""))
        ok = (not code and parsed.get(env["data"]) is not None) or code in ok_codes
        out = {"ok": ok, "upstream_code": code}
        msg = parsed.get(env["message"]) or parsed.get("reason")
        if msg:
            out["upstream_message"] = msg
        out["data"] = parsed.get(env["data"])
        return out

    def _log_traffic(self, ep, host, query, body_bytes, headers, resp) -> None:
        if not settings().get("log_raw_traffic"):
            return
        try:
            logs_dir = settings().logs_dir
            logs_dir.mkdir(parents=True, exist_ok=True)
            log = logs_dir / "upstream.log"
            safe_headers = {k: v for k, v in headers.items()
                            if k.lower() not in ("x-client-token", "token")}
            entry = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "endpoint": ep.name, "method": ep.method, "host": host,
                "path": ep.path, "query": query,
                "request_body": (body_bytes or b"").decode("utf-8", "replace")[:2000],
                "request_headers": safe_headers,
                "status": resp.status_code,
                "response_body": resp.text[:4000],
            }
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass
