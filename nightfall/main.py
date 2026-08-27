"""FastAPI application: unified private cinema & anime gateway."""
from __future__ import annotations
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from .cache import CacheBucket
from .config import settings
from .media_proxy import relay
from .protocol_store import ProtocolStore, mask_secrets
from .security import ApiKeyStore, SlidingWindowRateLimiter, make_auth_dependency
from .translate import normalize_downloads, normalize_search, normalize_streams, normalize_subtitles
from .upstream.client import UpstreamAuthError, UpstreamClient, UpstreamNetworkError
from .upstream.endpoints import Endpoints
from .upstream.identity import DeviceIdentity
from .selfheal.detector import StalenessDetector
from .selfheal.doctor import run as doctor_run
from .selfheal.healer import Healer

log = logging.getLogger("nightfall")

state: Dict[str, Any] = {}

def build_client() -> UpstreamClient:
    return UpstreamClient(state["store"], state["identity"],
                          on_auth_failure=state["detector"].on_auth_failure,
                          on_success=state["detector"].on_success)

@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = settings()
    for d in (cfg.watch_dir, cfg.logs_dir, cfg.device_file.parent):
        d.mkdir(parents=True, exist_ok=True)
    # ensure downloads dir
    dl = cfg.get("downloads.directory", "downloads")
    (cfg.app_root / dl).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    state["store"] = ProtocolStore(cfg.protocol_file)
    state["identity"] = DeviceIdentity(cfg.device_file, state["store"].data, region=cfg.get("region","US"))
    state["detector"] = StalenessDetector(threshold=int(cfg.get("selfheal.auth_fail_threshold",3)))
    state["client"] = build_client()
    state["cache"] = CacheBucket({k: int(v) for k,v in settings().get("cache_ttl",{}).items()})
    if cfg.get("selfheal.auto_scan_on_start"):
        try:
            report = Healer(state["store"], state["identity"]).scan_and_heal(
                detector=state["detector"], client_factory=lambda s,i: UpstreamClient(s,i, on_auth_failure=state["detector"].on_auth_failure, on_success=state["detector"].on_success))
            log.info("startup heal scan: %s %s", report.status, report.message)
        except Exception as e:
            log.warning("startup heal failed: %s", e)
    state["keys"] = ApiKeyStore(cfg.device_file.parent)
    state["limiter"] = SlidingWindowRateLimiter(int(cfg.get("rate_limit_per_minute",60) or 0))
    app.state.auth_guard = make_auth_dependency(state["keys"], cfg.get("security.require_api_key","auto"))
    yield

app = FastAPI(title="🌙 Nightfall — Private Cinema Gateway",
              description="Unified MovieBox + Anilab2/Kyoto gateway. Fast, no lag, downloads -> nightfall/downloads. Repair via config.yaml.",
              version="1.0.0", lifespan=lifespan)

# Mount anime router
try:
    from .anilab.routes import router as anime_router
    app.include_router(anime_router, prefix="/anime", tags=["anime"])
except Exception as e:
    log.warning("anime router mount failed: %s", e)

# Serve static anime UI if exists
_static_dir = Path(__file__).resolve().parent.parent / "static"
if _static_dir.exists():
    # serve at /anime/ui via file response, plus static mount
    pass

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    limiter = state.get("limiter")
    if limiter is not None and request.url.path not in ("/health",):
        client_ip = (request.client.host if request.client else request.headers.get("x-forwarded-for","?"))
        if not limiter.check(client_ip.split(",")[0].strip()):
            return JSONResponse({"ok": False, "error": {"code":"RATE_LIMITED","message": f"max {limiter.per_minute} req/min"}}, status_code=429)
    guard = getattr(app.state, "auth_guard", None)
    if guard is not None:
        try:
            guard(request)
        except HTTPException as exc:
            return JSONResponse({"ok": False, "error":{"code":"UNAUTHORIZED","message": exc.detail}}, status_code=exc.status_code, headers=exc.headers or {})
    return await call_next(request)

def _err(status:int, code:str, message:str, **extra) -> JSONResponse:
    body={"ok": False, "error":{"code":code,"message":message}, **extra}
    resp=JSONResponse(body, status_code=status)
    if state.get("detector") and state["detector"].status()["state"]=="PROTOCOL_STALE":
        resp.headers["X-Wrapper-State"]="PROTOCOL_STALE"
        resp.headers["X-Wrapper-Remediation"]="drop latest APK into wrapper/watch/ then POST /heal"
    return resp

def _call(cache_kind: Optional[str], cache_key: str, fn, debug_raw: bool=False) -> Dict[str,Any]:
    cache=state["cache"]
    if cache_kind and not debug_raw:
        hit=cache.for_kind(cache_kind).get(cache_key)
        if hit is not None: return hit
    result=fn()
    if cache_kind and not debug_raw:
        cache.for_kind(cache_kind).set(cache_key, result)
    return result

# ---- meta routes ----
@app.get("/health")
def health():
    det=state["detector"].status()
    return {"ok": True, "wrapper_state": det["state"], "consecutive_auth_failures": det["consecutive_auth_failures"],
            "protocol_version": state["store"].data.get("version"), "source_apk": state["store"].data.get("source_apk"),
            "mode": settings().get("mode"), "remediation": det["remediation"]}

@app.get("/doctor")
def doctor(live: bool=False):
    rep=doctor_run(state["store"], state["detector"], live=live, client_factory=lambda s,i: UpstreamClient(s,i))
    status=200 if rep.healthy else 503
    return JSONResponse(rep.to_dict(), status_code=status)

@app.post("/heal")
def heal():
    healer=Healer(state["store"], state["identity"])
    rep=healer.scan_and_heal(detector=state["detector"])
    if rep.status=="healed":
        state["client"]=build_client()
        state["cache"].clear()
    code={"healed":200,"current":200}.get(rep.status, 409 if rep.status in ("structural_change","probe_failed") else 400)
    return JSONResponse(rep.to_dict(), status_code=code)

@app.post("/session/token")
def set_session_token(body: dict = Body(...)):
    token=str(body.get("token") or "").strip()
    if not token: return _err(400,"BAD_REQUEST",'JSON body must be {"token": "..."}')
    data=dict(state["store"].data); data["session_token"]=token
    state["store"].save(data, backup_dir=settings().backups_dir)
    state["client"]=build_client()
    return {"ok": True, "message":"session token stored","token_preview": token[:8]+"…"}

@app.delete("/session/token")
def clear_session_token():
    data=dict(state["store"].data); data["session_token"]=""
    state["store"].save(data, backup_dir=settings().backups_dir)
    state["client"]=build_client()
    return {"ok": True, "message":"back to guest mode"}

@app.get("/protocol")
def protocol_view(): return mask_secrets(state["store"].data)

@app.get("/keys")
def keys_list(): return {"ok": True, "keys": state["keys"].list()}

@app.post("/keys")
def keys_create(body: dict = Body(...)):
    name=str(body.get("name") or "").strip() or "unnamed"
    created=state["keys"].create(name)
    return {"ok": True, "message":"store this key now - it is shown once", **created}

@app.get("/anime/ui")
def anime_ui():
    html_path = Path(__file__).resolve().parent.parent / "static" / "anime.html"
    if html_path.exists():
        return FileResponse(str(html_path), media_type="text/html")
    raise HTTPException(404, "anime.html not found — run build step 11")

# ---- content routes (same as wrapper) ----
@app.get("/search")
def search(q: str = Query(min_length=1), page: int=1, per_page: int=20, type: Optional[int]=None, debug_raw: bool=False):
    ep=Endpoints(state["store"].data["endpoints"]).get("search_v2")
    body={"page":page,"perPage":per_page,"keyword":q}
    if type is not None: body["subjectType"]=type
    try:
        res=_call("search", f"search:{q}:{page}:{per_page}:{type}", lambda: state["client"].request(ep, json_body=body), debug_raw)
    except UpstreamAuthError as exc: return _err(502,"UPSTREAM_AUTH_REJECTED", str(exc))
    except UpstreamNetworkError as exc: return _err(504,"UPSTREAM_UNREACHABLE", str(exc))
    return res if debug_raw else {**res, "normalized": normalize_search(res.get("data"))}

@app.get("/search/suggest")
def search_suggest(q: str = Query(min_length=1)):
    ep=Endpoints(state["store"].data["endpoints"]).get("search_suggest")
    try: return state["client"].request(ep, params={"q":q})
    except UpstreamAuthError as exc: return _err(502,"UPSTREAM_AUTH_REJECTED", str(exc))

@app.get("/search/rank")
def search_rank():
    ep=Endpoints(state["store"].data["endpoints"]).get("search_rank")
    try: return _call("metadata","rank", lambda: state["client"].request(ep))
    except UpstreamAuthError as exc: return _err(502,"UPSTREAM_AUTH_REJECTED", str(exc))

@app.get("/titles/{subject_id}")
def title_detail(subject_id: str, debug_raw: bool=False):
    ep=Endpoints(state["store"].data["endpoints"]).get("subject_get")
    try: res=_call("metadata", f"detail:{subject_id}", lambda: state["client"].request(ep, params={"subjectId": subject_id}), debug_raw)
    except UpstreamAuthError as exc: return _err(502,"UPSTREAM_AUTH_REJECTED", str(exc))
    return res

@app.get("/titles/{subject_id}/seasons")
def seasons(subject_id: str):
    ep=Endpoints(state["store"].data["endpoints"]).get("season_info")
    try: return _call("metadata", f"seasons:{subject_id}", lambda: state["client"].request(ep, params={"subjectId": subject_id}))
    except UpstreamAuthError as exc: return _err(502,"UPSTREAM_AUTH_REJECTED", str(exc))

@app.get("/titles/{subject_id}/episodes")
def episodes(subject_id: str, season: int=1, start:int=1, end:int=100):
    ep=Endpoints(state["store"].data["endpoints"]).get("season_info")
    params={"subjectId": subject_id, "se": season, "startPosition": start, "endPosition": end, "pagerMode":1}
    try: return _call("metadata", f"eps:{subject_id}:{season}:{start}:{end}", lambda: state["client"].request(ep, params=params))
    except UpstreamAuthError as exc: return _err(502,"UPSTREAM_AUTH_REJECTED", str(exc))

@app.get("/titles/{subject_id}/stream")
async def stream(request: Request, subject_id: str, season: Optional[int]=None, episode: Optional[int]=None, se: Optional[int]=None, ep: Optional[int]=None, resolution: Optional[str]=None, mode: Optional[str]=None, index: int=0, debug_raw: bool=False):
    explicit_se=season if season is not None else se
    explicit_ep=episode if episode is not None else ep
    s=explicit_se if explicit_se is not None else 0
    e=explicit_ep if explicit_ep is not None else 0
    ep_def=Endpoints(state["store"].data["endpoints"]).get("play_info")
    params={"subjectId": subject_id, "se": s, "ep": e}
    try: res=_call("stream", f"stream:{subject_id}:{s}:{e}", lambda: state["client"].request(ep_def, params=params), debug_raw)
    except UpstreamAuthError as exc: return _err(502,"UPSTREAM_AUTH_REJECTED", str(exc))
    if debug_raw: return res
    streams=normalize_streams(res.get("data"))
    if not streams and explicit_se is None and explicit_ep is None:
        try:
            res=_call("stream", f"stream:{subject_id}:1:1", lambda: state["client"].request(ep_def, params={"subjectId": subject_id, "se":1,"ep":1}), debug_raw)
            streams=normalize_streams(res.get("data"))
        except Exception: pass
    if not streams: return _err(404,"NO_STREAMS_FOUND","no URLs found in play-info payload; inspect with ?debug_raw=true")
    if resolution:
        want=resolution.rstrip("pP")
        exact=[st for st in streams if want in {str(r) for r in st.get("resolutions") or []} or str(st.get("max_resolution","")).rstrip("pP")==want]
        if exact: streams=exact
    chosen=streams[min(index,len(streams)-1)]
    effective_mode=mode or settings().get("mode","links")
    if effective_mode=="proxy": return await relay(chosen["url"], request)
    return {"ok": True, "selected": chosen, "alternatives": [{"kind":st["kind"],"resolutions":st.get("resolutions"),"size_mb":st.get("size_mb")} for st in streams]}

@app.get("/titles/{subject_id}/download")
async def download(request: Request, subject_id: str, season: Optional[int]=None, episode: Optional[int]=None, se: Optional[int]=None, ep: Optional[int]=None, page:int=1, pages:int=3, per_page:int=10, resolution:int=0, mode: Optional[str]=None, index:int=0, min_resolution:int=0, debug_raw: bool=False):
    s=season if season is not None else se
    e=episode if episode is not None else ep
    ep_def=Endpoints(state["store"].data["endpoints"]).get("resource")
    items, raw_first=[], None
    for p in range(page, page+max(1,pages)):
        params={"subjectId": subject_id, "page":p,"perPage":per_page,"all":0,"startPosition":1,"endPosition":per_page,"pagerMode":0,"resolution":resolution}
        if s is not None:
            params["se"]=s; params["epFrom"]=e if e is not None else 1; params["epTo"]=e if e is not None else per_page*p or per_page
        try: res=state["client"].request(ep_def, params=params)
        except UpstreamAuthError as exc: return _err(502,"UPSTREAM_AUTH_REJECTED", str(exc))
        data=res.get("data")
        if raw_first is None: raw_first=data
        lst=(data or {}).get("list") or [] if isinstance(data, dict) else []
        items.extend(lst)
        if not lst: break
    if s is not None and e is not None:
        items=[it for it in items if str(it.get("se"))==str(s) and str(it.get("ep"))==str(e)]
    if debug_raw: return {"ok": True, "upstream_code": res.get("upstream_code"), "count": len(items), "list": items}
    resources=[]
    for it in items:
        url=it.get("resourceLink")
        if not url: continue
        try: size_mb=int(it.get("size") or 0)/1e6
        except: size_mb=None
        resources.append({"se":it.get("se"),"ep":it.get("ep"),"episode":it.get("episode"),"title":it.get("title"),"resolution":f"{it.get('resolution')}p" if it.get("resolution") else None,"codec":it.get("codecName"),"size_mb":round(size_mb,1) if size_mb else None,"duration_seconds":it.get("duration"),"require_member_type":it.get("requireMemberType"),"signed_url":url,"source_url":it.get("sourceUrl")})
    resources.sort(key=lambda r: _res_num(r.get("resolution")), reverse=True)
    if min_resolution: resources=[r for r in resources if _res_num(r["resolution"])>=min_resolution] or resources
    if not resources: return _err(404,"NO_DOWNLOADS_FOUND","no resource links; try ?debug_raw=true to inspect payload")
    chosen=resources[min(index,len(resources)-1)]
    effective_mode=mode or settings().get("mode","links")
    if effective_mode=="proxy": return await relay(chosen["signed_url"], request)
    return {"ok": True, "selected": chosen, "count": len(resources), "all": resources}

def _res_num(resolution) -> int:
    try: return int(str(resolution).rstrip("pP"))
    except: return 0

@app.get("/titles/{subject_id}/resources")
async def resources_alias(subject_id: str, request: Request, season: Optional[int]=None, episode: Optional[int]=None, se: Optional[int]=None, ep: Optional[int]=None, page:int=1, pages:int=3, per_page:int=10, resolution:int=0, index:int=0, debug_raw: bool=False):
    return await download(request, subject_id, season=season, episode=episode, se=se, ep=ep, page=page, pages=pages, per_page=per_page, resolution=resolution, index=index, debug_raw=debug_raw)

@app.get("/titles/{subject_id}/subtitles")
def subtitles(subject_id: str, season:int=1, episode:int=1):
    ep=Endpoints(state["store"].data["endpoints"]).get("ext_captions")
    try: res=state["client"].request(ep, params={"subjectId": subject_id,"se":season,"ep":episode})
    except UpstreamAuthError as exc: return _err(502,"UPSTREAM_AUTH_REJECTED", str(exc))
    return {"ok": True, "subtitles": normalize_subtitles(res.get("data")), "upstream_code": res.get("upstream_code")}

@app.get("/discover/tabs")
def discover_tabs(tab_id:int=1, version:str="0"):
    ep=Endpoints(state["store"].data["endpoints"]).get("tab_operating")
    try: return _call("metadata", f"tabs:{tab_id}:{version}", lambda: state["client"].request(ep, params={"tabId": tab_id,"version":version}))
    except UpstreamAuthError as exc: return _err(502,"UPSTREAM_AUTH_REJECTED", str(exc))

@app.get("/proxy/media")
async def proxy_media(request: Request, url: str):
    return await relay(url, request)
