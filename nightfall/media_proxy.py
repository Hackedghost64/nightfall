"""Range-aware media relay used when mode=proxy (or ?mode=proxy per request).

Security guards: https/http only, host suffix allowlist from protocol.yaml.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from .config import settings


def _validate_target(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http"):
        raise HTTPException(400, "only http(s) media URLs allowed")
    suffixes = settings().get("media_allow_host_suffixes")
    if suffixes is None:
        suffixes = ("aoneroom.com", "aliyuncs.com", "akamaized.net", "cloudcdn.net", "hakunaymatata.com")
    host = (parsed.hostname or "").lower()
    if not any(host == s or host.endswith("." + s) for s in suffixes):
        raise HTTPException(403, f"media host '{host}' not in allowlist")


async def relay(url: str, request: Request,
                content_type: Optional[str] = None) -> StreamingResponse:
    _validate_target(url)
    headers: dict = {}
    rng = request.headers.get("range")
    if rng:
        headers["Range"] = rng
    cookie = request.headers.get("cookie")
    if cookie:
        headers["Cookie"] = cookie
    client = httpx.AsyncClient(follow_redirects=True, timeout=None)
    req = client.build_request("GET", url, headers=headers)

    try:
        upstream_resp = await client.send(req, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(502, f"media fetch failed: {exc}")

    def _close() -> None:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(client.aclose())
            else:
                loop.run_until_complete(client.aclose())
        except Exception:
            pass

    resp_headers = {}
    for h in ("content-range", "content-length", "accept-ranges",
              "content-type", "etag", "last-modified"):
        if h in upstream_resp.headers:
            resp_headers[h] = upstream_resp.headers[h]
    if content_type:
        resp_headers["content-type"] = content_type

    return StreamingResponse(
        upstream_resp.aiter_bytes(64 * 1024),
        status_code=upstream_resp.status_code,
        headers=resp_headers,
        background=_close,
    )
