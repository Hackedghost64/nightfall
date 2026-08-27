"""Signing profiles.

The active profile is selected by `signature.profile` in protocol.yaml.
Adding support for a future algorithm = implement one class + register it;
no other code changes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import unquote


@dataclass
class SignRequest:
    method: str
    path: str                      # e.g. /wefeed-mobile-bff/subject-api/get
    query: str                     # RAW encoded query string (as sent on the wire)
    headers: Dict[str, str]
    body: Optional[bytes]
    timestamp_ms: int


def canonicalize_query(query_string: str) -> str:
    """Mirror com.transsion.api.gateway.sercurity.c.a():

    - split on '&', each pair at first '='
    - URL-DECODE key and value
    - HashMap semantics: duplicate keys -> LAST occurrence wins
    - sort entries by KEY ONLY (sercurity/b.java)
    - rejoin as k=v&k=v WITHOUT re-encoding (raw decoded values)
    """
    if not query_string:
        return ""
    merged: Dict[str, str] = {}
    for part in query_string.split("&"):
        if not part:
            continue
        idx = part.find("=")
        k_raw, v_raw = (part[:idx], part[idx + 1:]) if idx >= 0 else (part, "")
        try:
            k = unquote(k_raw)
            v = unquote(v_raw)
        except Exception:
            k, v = k_raw, v_raw
        if k == "":
            continue
        merged[k] = v
    ordered = sorted(merged.items(), key=lambda kv: kv[0])
    return "&".join(f"{k}={v}" for k, v in ordered)


def _field_value(field: str, req: SignRequest, body_md5_limit: int) -> str:
    h = req.headers
    if field == "method":
        return req.method.upper()
    if field == "accept":
        return h.get("accept", "")
    if field == "content_type":
        return h.get("content-type", "")
    if field == "content_length":
        return h.get("content-length", "")
    if field == "timestamp":
        return str(req.timestamp_ms)
    if field == "body_md5":
        if not req.body:
            return ""
        blob = req.body[:body_md5_limit] if len(req.body) > body_md5_limit else req.body
        return hashlib.md5(blob).hexdigest()
    if field == "path_query":
        cq = canonicalize_query(req.query)
        return f"{req.path}?{cq}" if cq else req.path
    raise KeyError(f"unknown canonical field: {field}")


class SigningProfile:
    name = "base"

    def __init__(self, sig_cfg: dict):
        self.cfg = sig_cfg

    def header_value(self, req: SignRequest, secret_bytes: bytes) -> str:
        fields: List[str] = self.cfg.get(
            "canonical_fields",
            ["method", "accept", "content_type", "content_length",
             "timestamp", "body_md5", "path_query"])
        limit = int(self.cfg.get("body_md5_limit", 102400))
        canonical = "\n".join(_field_value(f, req, limit) for f in fields)
        digest = self._digest(canonical.encode("utf-8"), secret_bytes)
        return self.cfg.get("format", "{timestamp}|{version}|{digest}").format(
            timestamp=req.timestamp_ms,
            version=self.cfg.get("version_tag", "2"),
            digest=digest)

    def _digest(self, payload: bytes, secret: bytes) -> str:
        raise NotImplementedError


class HmacMd5V2(SigningProfile):
    """Current app profile: HMAC-MD5 over base64-decoded manifest secret."""
    name = "hmac_md5_v2"

    def _digest(self, payload: bytes, secret: bytes) -> str:
        mac = hmac.new(secret, payload, hashlib.md5).digest()
        return base64.b64encode(mac).decode("ascii")


_REGISTRY = {
    cls.name: cls for cls in (HmacMd5V2,)
}


def build_profile(sig_cfg: dict) -> SigningProfile:
    name = sig_cfg.get("profile")
    cls = _REGISTRY.get(name)
    if cls is None:
        raise KeyError(
            f"signature.profile '{name}' unknown. Registered: {sorted(_REGISTRY)}. "
            f"If the app changed algorithms, add a profile class to upstream/signers.py.")
    return cls(sig_cfg)


def derive_secret_bytes(sig_cfg: dict, secrets: dict) -> bytes:
    raw = secrets.get(sig_cfg.get("secret_field", "gateway_secret_online"), "")
    if not raw:
        raise KeyError("configured signature.secret_field missing from protocol secrets")
    if sig_cfg.get("secret_is_base64", True):
        return base64.b64decode(raw)
    return raw.encode("utf-8")
