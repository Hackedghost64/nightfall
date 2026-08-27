"""Daemon lifecycle: up / down / status with pidfile + health wait — Nightfall."""
from __future__ import annotations
import os, signal, subprocess, sys, time, urllib.request, json as _json
from pathlib import Path

def _pidfile() -> Path:
    from .config import settings
    d = settings().device_file.parent
    d.mkdir(parents=True, exist_ok=True)
    try: os.chmod(d, 0o700)
    except: pass
    return d / "nightfall.pid"

def _is_nightfall_pid(pid: int) -> bool:
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="ignore")
        return "nightfall.main" in cmd or "uvicorn" in cmd
    except Exception:
        # fallback: assume is_running means ours (no /proc)
        return True

def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        # extra check pid reuse
        return _is_nightfall_pid(pid)
    except (ProcessLookupError, PermissionError, TypeError):
        return False

def get_pid() -> int | None:
    p=_pidfile()
    if not p.exists(): return None
    try:
        # acquire shared lock to avoid race
        import fcntl
        with open(p, "r") as fh:
            try: fcntl.flock(fh, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except: pass
            txt=fh.read().strip()
            try:
                fcntl.flock(fh, fcntl.LOCK_UN)
            except: pass
        return int(txt)
    except Exception:
        try: return int(p.read_text().strip())
        except: return None

def _health(timeout: float=20.0) -> bool:
    deadline=time.time()+timeout
    cfg=__import__("nightfall.config", fromlist=["settings"]).settings()
    port=int(cfg.get("server.port",8399))
    url=f"http://127.0.0.1:{port}/health"
    # include API key if present (health is public but doctor may need auth)
    headers={}
    try:
        key_path=Path(cfg.device_file.parent) / "cli.key"
        if key_path.exists():
            k=key_path.read_text().strip()
            if k: headers["X-API-Key"]=k
    except: pass
    while time.time()<deadline:
        try:
            req=urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=2) as r:
                if r.status==200: return True
        except Exception: time.sleep(0.4)
    return False

def up(foreground_check: bool=True) -> dict:
    pid=get_pid()
    if pid and is_running(pid): return {"status":"already-running","pid":pid}
    # remove stale pidfile
    try: _pidfile().unlink(missing_ok=True)
    except: pass
    root=Path(__file__).resolve().parent.parent
    logs_dir=root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log=logs_dir / "server.log"
    cfg=__import__("nightfall.config", fromlist=["settings"]).settings()
    host=str(cfg.get("server.host","0.0.0.0"))
    port=str(int(cfg.get("server.port",8399)))
    proc=subprocess.Popen([sys.executable,"-m","uvicorn","nightfall.main:app","--host",host,"--port",port],
        cwd=str(root), stdout=open(log,"ab"), stderr=subprocess.STDOUT, start_new_session=True)
    # atomic pidfile with O_EXCL + fcntl
    p=_pidfile()
    try:
        import fcntl
        fd=os.open(str(p), os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            try: fcntl.flock(fh, fcntl.LOCK_EX)
            except: pass
            fh.write(str(proc.pid))
    except FileExistsError:
        # race: another up won
        try: proc.terminate()
        except: pass
        return {"status":"already-running","pid":get_pid()}
    except Exception:
        p.write_text(str(proc.pid))
        try: os.chmod(p, 0o600)
        except: pass
    ok=_health() if foreground_check else True
    return {"status":"started" if ok else "started-unverified","pid":proc.pid,"log":str(log),"host":host,"port":port}

def down() -> dict:
    pid=get_pid()
    if not pid: return {"status":"not-running"}
    if not is_running(pid):
        _pidfile().unlink(missing_ok=True)
        return {"status":"stale-pidfile-removed","pid":pid}
    try: os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError): 
        try: os.kill(pid, signal.SIGTERM)
        except: pass
    for _ in range(40):
        if not is_running(pid): break
        time.sleep(0.25)
    else:
        try: os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception:
            try: os.kill(pid, signal.SIGKILL)
            except: pass
    _pidfile().unlink(missing_ok=True)
    return {"status":"stopped","pid":pid}

def status() -> dict:
    pid=get_pid()
    running=bool(pid and is_running(pid))
    healthy=False; state=None
    if running:
        try:
            port=__import__("nightfall.config", fromlist=["settings"]).settings().get("server.port",8399)
            # include key for health if needed
            headers={}
            try:
                cfg=__import__("nightfall.config", fromlist=["settings"]).settings()
                kp=Path(cfg.device_file.parent) / "cli.key"
                if kp.exists():
                    k=kp.read_text().strip()
                    if k: headers["X-API-Key"]=k
            except: pass
            req=urllib.request.Request(f"http://127.0.0.1:{int(port)}/health", headers=headers)
            with urllib.request.urlopen(req, timeout=3) as r:
                body=_json.loads(r.read()); healthy=bool(body.get("ok")); state=body.get("wrapper_state")
        except Exception: pass
    return {"running":running,"pid":pid,"healthy":healthy,"wrapper_state":state}
