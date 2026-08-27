"""Daemon lifecycle: up / down / status with pidfile + health wait — Nightfall."""
from __future__ import annotations
import os, signal, subprocess, sys, time, urllib.request
from pathlib import Path

def _pidfile() -> Path:
    from .config import settings
    d = settings().device_file.parent
    d.mkdir(parents=True, exist_ok=True)
    return d / "nightfall.pid"

def is_running(pid: int) -> bool:
    try: os.kill(pid, 0); return True
    except (ProcessLookupError, PermissionError, TypeError): return False

def get_pid() -> int | None:
    p=_pidfile()
    if not p.exists(): return None
    try: return int(p.read_text().strip())
    except Exception: return None

def _health(timeout: float=20.0) -> bool:
    deadline=time.time()+timeout
    cfg=__import__("nightfall.config", fromlist=["settings"]).settings()
    host=cfg.get("server.host","0.0.0.0")
    port=int(cfg.get("server.port",8399))
    # health is always on 127.0.0.1 regardless of bind
    url=f"http://127.0.0.1:{port}/health"
    while time.time()<deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status==200: return True
        except Exception: time.sleep(0.4)
    return False

def up(foreground_check: bool=True) -> dict:
    pid=get_pid()
    if pid and is_running(pid): return {"status":"already-running","pid":pid}
    root=Path(__file__).resolve().parent.parent
    logs_dir=root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log=logs_dir / "server.log"
    cfg=__import__("nightfall.config", fromlist=["settings"]).settings()
    host=str(cfg.get("server.host","0.0.0.0"))
    port=str(int(cfg.get("server.port",8399)))
    proc=subprocess.Popen([sys.executable,"-m","uvicorn","nightfall.main:app","--host",host,"--port",port],
        cwd=str(root), stdout=open(log,"ab"), stderr=subprocess.STDOUT, start_new_session=True)
    Path(_pidfile()).write_text(str(proc.pid))
    ok=_health() if foreground_check else True
    return {"status":"started" if ok else "started-unverified","pid":proc.pid,"log":str(log),"host":host,"port":port}

def down() -> dict:
    pid=get_pid()
    if not pid: return {"status":"not-running"}
    if not is_running(pid):
        _pidfile().unlink(missing_ok=True)
        return {"status":"stale-pidfile-removed","pid":pid}
    try: os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError): os.kill(pid, signal.SIGTERM)
    for _ in range(40):
        if not is_running(pid): break
        time.sleep(0.25)
    else:
        try: os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception: pass
    _pidfile().unlink(missing_ok=True)
    return {"status":"stopped","pid":pid}

def status() -> dict:
    pid=get_pid()
    running=bool(pid and is_running(pid))
    healthy=False; state=None
    if running:
        try:
            import json as _j
            port=__import__("nightfall.config", fromlist=["settings"]).settings().get("server.port",8399)
            with urllib.request.urlopen(f"http://127.0.0.1:{int(port)}/health", timeout=3) as r:
                body=_j.loads(r.read()); healthy=bool(body.get("ok")); state=body.get("wrapper_state")
        except Exception: pass
    return {"running":running,"pid":pid,"healthy":healthy,"wrapper_state":state}
