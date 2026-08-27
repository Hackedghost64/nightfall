"""Async downloader — streaming httpx + resume + semaphore (max 3 concurrent)."""
from __future__ import annotations
import asyncio
from pathlib import Path
from typing import AsyncGenerator, Optional
import httpx

_SEM = asyncio.Semaphore(3)

async def download(url: str, dest: Path, headers: Optional[dict]=None, resume: bool=True) -> AsyncGenerator[tuple[int,int], None]:
    """Stream-download url -> dest, yielding (done_bytes, total_bytes) for progress.
    Supports Range resume if partial file exists and server honors it.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = dict(headers or {})
    start = 0
    if resume and dest.exists():
        start = dest.stat().st_size
        if start>0:
            headers["Range"] = f"bytes={start}-"
    # also handle .part file
    part = dest.with_suffix(dest.suffix + ".part") if not dest.suffix else Path(str(dest)+".part")
    # if dest exists incomplete, use part logic? simplify: download to dest directly with resume
    async with _SEM:
        async with httpx.AsyncClient(follow_redirects=True, timeout=None) as client:
            async with client.stream("GET", url, headers=headers) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                if r.status_code==206 and "content-range" in r.headers:
                    # parse total from Content-Range: bytes 100-999/1000
                    try:
                        total = int(r.headers["content-range"].split("/")[-1])
                    except Exception:
                        pass
                else:
                    if start>0 and r.status_code==200:
                        # server ignored Range — restart from 0
                        start=0
                        if dest.exists():
                            dest.unlink()
                mode = "ab" if start>0 else "wb"
                done = start
                # total is full file size if Range respected; else content-length
                full_total = total if total else 0
                with open(dest, mode) as f:
                    async for chunk in r.aiter_bytes(65536):
                        f.write(chunk)
                        done += len(chunk)
                        yield done, full_total

async def download_simple(url: str, dest: Path, headers: Optional[dict]=None) -> Path:
    async for _ in download(url, dest, headers=headers):
        pass
    return dest
