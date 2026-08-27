"""Range-aware media relay used when mode=proxy (or ?mode=proxy per request).

Security guards: https only, host suffix allowlist, private-IP deny, no open redirect.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from .config import settings

# singleton client reused across requests (lifespan)
_client: Optional[httpx.AsyncClient] = None

def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(30), headers={"User-Agent": "nightfall/1.0"})
    return _client


def _validate_target(url: str) -> None:
    parsed = urlparse(url)
    # https only
    if parsed.scheme != "https":
        raise HTTPException(400, "only https media URLs allowed")
    suffixes = settings().get("media_allow_host_suffixes")
    if suffixes is None:
        suffixes = ("aoneroom.com", "aliyuncs.com", "akamaized.net", "cloudcdn.net", "hakunaymatata.com")
    host = (parsed.hostname or "").lower()
    if not any(host == s or host.endswith("." + s) for s in suffixes):
        raise HTTPException(403, f"media host '{host}' not in allowlist")
    # private-IP deny after DNS (SSRF)
    try:
        infos = socket.getaddrinfo(host, 443, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
        for fam, _, _, _, sockaddr in infos:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise HTTPException(403, f"media host '{host}' resolves to private IP {ip_str}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"media host DNS failed: {e}")


async def relay(url: str, request: Request,
                content_type: Optional[str] = None) -> StreamingResponse:
    _validate_target(url)
    headers: dict = {}
    rng = request.headers.get("range")
    if rng and len(rng) < 128 and rng.startswith("bytes="):
        headers["Range"] = rng
    # do not forward arbitrary Cookie from caller; only if needed upstream already handles via _validate
    client = _get_client()
    # manual redirect handling with re-validation (max 3)
    cur_url = url
    for _ in range(3):
        req = client.build_request("GET", cur_url, headers=headers)
        try:
            upstream_resp = await client.send(req, stream=True)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"media fetch failed: {exc}")
        if upstream_resp.status_code in (301, 302, 303, 307, 308):
            loc = upstream_resp.headers.get("location")
            await upstream_resp.aclose()
            if not loc:
                raise HTTPException(502, "media redirect without location")
            # re-validate redirect target
            try:
                _validate_target(loc)
            except HTTPException:
                raise
            cur_url = loc
            continue
        break
    else:
        await upstream_resp.aclose()
        raise HTTPException(502, "too many redirects")

    # size cap ~2GB
    clen = upstream_resp.headers.get("content-length")
    try:
        if clen and int(clen) > 2_200_000_000:
            await upstream_resp.aclose()
            raise HTTPException(413, "media too large")
    except ValueError:
        pass

    # whitelist headers, strip set-cookie
    resp_headers = {}
    for h in ("content-range", "content-length", "accept-ranges",
              "content-type", "etag", "last-modified", "cache-control"):
        if h in upstream_resp.headers:
            resp_headers[h] = upstream_resp.headers[h]
    if content_type:
        resp_headers["content-type"] = content_type
    # remove hop-by-hop
    resp_headers.pop("set-cookie", None)

    return StreamingResponse(
        upstream_resp.aiter_bytes(64 * 1024),
        status_code=upstream_resp.status_code,
        headers=resp_headers,
        background=None,
    )
