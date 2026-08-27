"""Persistent synthetic device identity.

One stable fingerprint is reused across restarts so the backend sees a single
consistent 'device' (stored at data/device.json).
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from typing import Any, Dict


from pathlib import Path


class DeviceIdentity:
    def __init__(self, file_path, protocol: dict, region: str = "US"):
        self.path = Path(file_path)
        self.protocol = protocol
        self.region = region
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = self._load_or_create()

    def _load_or_create(self) -> Dict[str, Any]:
        if self.path.exists():
            try:
                # ensure restrictive perms even on existing
                try:
                    import os
                    os.chmod(self.path, 0o600)
                except: pass
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        data = {"device_id": uuid.uuid4().hex, "first_seen": int(time.time())}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            import os
            os.chmod(tmp, 0o600)
        except: pass
        tmp.replace(self.path)
        try:
            import os
            os.chmod(self.path, 0o600)
            os.chmod(self.path.parent, 0o700)
        except: pass
        return data

    @property
    def device_id(self) -> str:
        return self._data["device_id"]

    # ------------------------------------------------------------ session ---
    # Mirrors com.transsnet.login.q.h(Response): the backend pushes an anonymous
    # account via the X-User response header; the client adopts it silently and
    # then authenticates with "Authorization: Bearer <token>" (eg.a / f.java).

    def get_session(self) -> dict | None:
        s = self._data.get("anon_session")
        return s if isinstance(s, dict) and s.get("token") else None

    def save_session(self, payload: dict) -> None:
        with self._lock:
            self._data["anon_session"] = {
                "token": payload.get("token"),
                "userId": str(payload.get("userId", "")),
                "userType": payload.get("userType"),
                "harvested_at": int(time.time()),
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            try:
                import os
                os.chmod(tmp, 0o600)
            except: pass
            tmp.replace(self.path)

    def clear_session(self) -> None:
        with self._lock:
            self._data.pop("anon_session", None)
            if self.path.exists():
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
                try:
                    import os
                    os.chmod(tmp, 0o600)
                except: pass
                tmp.replace(self.path)

    def client_token(self) -> str:
        """com.transsion.baselib.net.f / wg.a: ts + ',' + md5(reverse(ts))."""
        ts = str(int(time.time() * 1000))
        digest = hashlib.md5(ts[::-1].encode()).hexdigest()
        return f"{ts},{digest}"

    def client_info(self, net: str = "WIFI", language: str = "en",
                    timezone_name: str = "America/New_York",
                    brand: str = "Google", model: str = "Pixel 8 Pro",
                    os_version: str = "14") -> str:
        # Dynamic fingerprint: if enabled, use spoofed profile
        try:
            from ..fingerprint import get_manager
            fm = get_manager()
            if fm.is_enabled():
                prof = fm.current_profile()
                brand = prof.get("brand", brand)
                model = prof.get("model", model)
                os_version = prof.get("os_version", os_version)
                language = prof.get("lang", language)
                timezone_name = prof.get("timezone", timezone_name)
        except Exception:
            pass
        app = self.protocol.get("app", {})
        info = {
            "package_name": app.get("package"),
            "version_name": app.get("version_name"),
            "version_code": app.get("version_code"),
            "os": "android",
            "os_version": os_version,
            "device_id": self.device_id,
            "install_store": "google_play",
            "brand": brand,
            "model": model,
            "system_language": language,
            "net": net,
            "region": self.region,
            "timezone": timezone_name,
            "sp_code": "0",
        }
        return json.dumps(info, separators=(",", ":"))

    def base_headers(self) -> Dict[str, str]:
        proto = self.protocol
        ident = proto.get("identity_headers", {})
        token = self.client_token()
        # Dynamic fingerprint for User-Agent / brand
        ua_brand = "Google"
        ua_model = "Pixel 8 Pro"
        ua_os = "14"
        try:
            from ..fingerprint import get_manager
            fm = get_manager()
            if fm.is_enabled():
                prof = fm.current_profile()
                ua_brand = prof.get("brand", ua_brand)
                ua_model = prof.get("model", ua_model)
                ua_os = prof.get("os_version", ua_os)
        except Exception:
            pass
        headers = {
            "accept": "application/json, text/plain, */*",
            "user-agent": (f"MovieBox/{app_ver(proto)} (Android {ua_os}; {ua_model})"),
            "x-tr-app": proto.get("app", {}).get("package", ""),
            "x-tr-version": app_ver(proto),
            "x-tr-device": self.device_id,
            "x-tr-region": self.region,
            "X-Client-Status": "1",
        }
        for h in ident.get("client_token", ["x-client-token"]):
            headers[h] = token
        ci = ident.get("client_info")
        if ci:
            headers[ci] = self.client_info()
        # Dynamic header spoofing (Sec-CH-UA etc.)
        try:
            from ..fingerprint import get_manager as _gm
            fm = _gm()
            if fm.is_enabled() and fm._spoof_headers:
                headers.update(fm.spoofed_headers())
        except Exception:
            pass
        return headers


def app_ver(proto: dict) -> str:
    return str(proto.get("app", {}).get("version_name", ""))
