"""Layered diagnostics: pinpoints WHICH layer broke after an app update.

Layers: protocol file -> identity -> DNS -> TCP/TLS -> signature accepted
        -> endpoint categories. Run via `python -m moviebox_wrapper doctor`
or GET /doctor?live=1.
"""
from __future__ import annotations

import socket
import ssl
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from ..config import settings
from ..protocol_store import ProtocolStore


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    layer: str = "general"


@dataclass
class DoctorReport:
    checks: List[Check] = field(default_factory=list)
    state: dict = field(default_factory=dict)

    @property
    def healthy(self) -> bool:
        return all(c.ok for c in self.checks if c.layer != "info")

    def to_dict(self) -> dict:
        return {
            "healthy": self.healthy,
            "state": self.state,
            "checks": [
                {"layer": c.layer, "check": c.name,
                 "ok": c.ok, "detail": c.detail}
                for c in self.checks
            ],
        }


def run(store: ProtocolStore, detector=None, client_factory=None, live: bool = False) -> DoctorReport:
    rep = DoctorReport()
    cfg = settings()

    # 1. protocol loads + required sections
    try:
        proto = store.data
        missing = [k for k in ("app", "secrets", "hosts", "signature", "endpoints") if k not in proto]
        ok = not missing and bool(proto["secrets"].get("gateway_secret_online"))
        rep.checks.append(Check("protocol.yaml structure", ok,
                                f"v{proto.get('version')} from {proto.get('source_apk')}"
                                if ok else f"missing sections/keys: {missing}",
                                layer="config"))
    except Exception as exc:
        rep.checks.append(Check("protocol.yaml structure", False, str(exc), layer="config"))
        return rep

    # 2. signing profile resolvable
    try:
        from ..upstream.signers import build_profile, derive_secret_bytes
        build_profile(proto["signature"])
        derive_secret_bytes(proto["signature"], proto["secrets"])
        rep.checks.append(Check("signing profile + secret decode", True,
                                f"profile={proto['signature'].get('profile')}", layer="config"))
    except Exception as exc:
        rep.checks.append(Check("signing profile + secret decode", False, str(exc), layer="config"))

    # 3. device identity
    try:
        from ..upstream.identity import DeviceIdentity
        ident = DeviceIdentity(cfg.device_file, proto)
        tok = ident.client_token()
        rep.checks.append(Check("device identity", bool(tok),
                                f"device_id={ident.device_id[:8]}…", layer="identity"))
    except Exception as exc:
        rep.checks.append(Check("device identity", False, str(exc), layer="identity"))

    if not live:
        rep.state = detector.status() if detector else {}
        return rep

    hosts = ([proto.get("hosts", {}).get("primary")] +
             list(proto.get("hosts", {}).get("fallbacks") or []))
    hosts = [h for h in hosts if h]

    # 4. DNS + TLS per host
    for host in hosts:
        try:
            ip = socket.gethostbyname(host)
            ctx = ssl.create_default_context()
            with socket.create_connection((host, 443), timeout=6) as sock:
                with ctx.wrap_socket(sock, server_hostname=host):
                    rep.checks.append(Check(f"DNS+TLS {host}", True, f"-> {ip}", layer="network"))
        except Exception as exc:
            rep.checks.append(Check(f"DNS+TLS {host}", False,
                                    f"{type(exc).__name__}: {exc}", layer="network"))

    # 5. signature acceptance probe (cheap endpoint)
    if any(c.ok for c in rep.checks if c.layer == "network"):
        try:
            from ..upstream.endpoints import Endpoints
            from ..upstream.client import UpstreamAuthError
            from ..upstream.identity import DeviceIdentity as DI
            if client_factory is None:
                from ..upstream.client import UpstreamClient
                ident = DI(cfg.device_file, proto)
                client = UpstreamClient(store, ident)
            else:
                client = client_factory(store, DI(cfg.device_file, proto))
            ep = Endpoints(proto["endpoints"]).get("search_suggest")
            t0 = time.time()
            result = client.request(ep, params={"q": "a"})
            took = int((time.time() - t0) * 1000)
            ok = bool(result.get("ok")) or "upstream_code" in result
            detail = (f"{took}ms upstream_code={result.get('upstream_code')!r}"
                      f" msg={result.get('upstream_message')!r}")
            if not ok:
                detail += " — envelope shape may have changed; check response.ok_codes"
            rep.checks.append(Check("signature accepted (live probe)", ok, detail,
                                    layer="signature"))

            # session-auth status for subject endpoints (informational)
            if ok:
                try:
                    sep = Endpoints(proto["endpoints"]).get("subject_get")
                    res = client.request(sep, params={"subjectId": "5904172458474619680"})
                    code = str(res.get("upstream_code", ""))
                    if code == "441" or res.get("upstream_message") == "miss token":
                        rep.checks.append(Check(
                            "subject-api session auth", False,
                            "441 miss token → capture a session token from your "
                            "logged-in device and POST /session/token {\"token\": …}",
                            layer="info"))
                    else:
                        rep.checks.append(Check("subject-api session auth", True,
                                                f"accepted (code={code!r})", layer="info"))
                except UpstreamAuthError as exc:
                    rep.checks.append(Check(
                        "subject-api session auth", False,
                        f"{exc} → capture a session token from your logged-in "
                        f"device and POST /session/token", layer="info"))
                except Exception as exc:
                    rep.checks.append(Check("subject-api session auth", False,
                                            f"{type(exc).__name__}: {exc}", layer="info"))
        except UpstreamAuthError as exc:
            hint = ("endpoint demands a session token; capture one from your "
                    "logged-in device and POST /session/token"
                    if "441" in str(exc) or "miss token" in str(exc).lower() else
                    "secrets likely rotated; drop new APK into watch/ then POST /heal")
            rep.checks.append(Check("signature accepted (live probe)", False,
                                    f"{type(exc).__name__}: {exc}  ← {hint}",
                                    layer="signature"))

    rep.state = detector.status() if detector else {}
    return rep
