"""`mbx setup` - first-run wizard: environment, identity, server, key."""
from __future__ import annotations

import shutil
import sys

from .banner import bold, cyan, dim, green, ok, fail, info, warn


def _players() -> list:
    return [p for p in ("mpv", "vlc", "ffplay") if shutil.which(p)]


def run() -> int:
    from .config import settings
    cfg = settings()

    print(dim("\n  first-run setup\n"))

    # 1. environment ------------------------------------------------------
    ok(f"python {sys.version_info.major}.{sys.version_info.minor}")
    players = _players()
    if players:
        ok(f"players: {', '.join(players)}")
    else:
        warn("no player found (install mpv or vlc for `mbx play`)")
    if shutil.which("ffmpeg"):
        ok("ffmpeg present (HLS/DASH downloads enabled)")
    else:
        warn("ffmpeg missing - manifest downloads disabled")

    # 2. protocol + identity ----------------------------------------------
    try:
        from .protocol_store import ProtocolStore
        store = ProtocolStore(cfg.protocol_file)
        proto = store.data
        ok(f"protocol v{proto.get('version')} ready "
           f"({len(proto.get('endpoints', {}))} endpoints)")
    except Exception as exc:
        fail(f"protocol load failed: {exc}")
        return 1

    from .upstream.identity import DeviceIdentity
    ident = DeviceIdentity(cfg.device_file, proto,
                           region=cfg.get("region", "US"))
    ok(f"device identity {ident.device_id[:8]}… "
       f"(session: {'active' if ident.get_session() else 'will auto-bootstrap'})")

    # 3. server ------------------------------------------------------------
    from . import daemon
    st = daemon.status()
    if not st["running"]:
        info(f"starting gateway on {cfg.get('server.host')}:{cfg.get('server.port')} …")
        res = daemon.up()
        if res["status"].startswith("started-unverified"):
            fail("server did not become healthy - check logs/server.log")
            return 1
        ok(f"gateway running (pid {res['pid']})")
    else:
        ok(f"gateway already running (pid {st['pid']})")

    # 4. api key -----------------------------------------------------------
    from .security import ApiKeyStore
    keys = ApiKeyStore(cfg.device_file.parent)
    keyfile = cfg.device_file.parent / "cli.key"
    if keyfile.exists():
        ok("local API key present (data/cli.key)")
        raw = keyfile.read_text().strip()
    else:
        created = keys.create("setup")
        keyfile.write_text(created["plaintext"])
        keyfile.chmod(0o600)
        raw = created["plaintext"]
        ok("API key created and saved to data/cli.key")

    mode = cfg.get("security.require_api_key", "auto")
    enforced = True if mode is True else False if mode is False else keys.count > 0
    ok(f"security: {'enforced' if enforced else 'open'} "
       f"(mode={mode}, {keys.count} key(s)) · rate limit {cfg.get('rate_limit_per_minute')}/min")

    port = cfg.get("server.port", 8399)
    print(f"""
  {bold('Ready.')}
  {dim('─' * 46)}
  {cyan('curl')} -H "X-API-Key: {raw}" \\
    http://127.0.0.1:{port}/search?q=breaking+bad

  {bold('mbx play')} "breaking bad" 5x1     {dim('# stream via mpv/vlc')}
  {bold('mbx dl')}   "peaky blinders" 6x1 {dim('# download episode')}
  {bold('mbx tui')}                        {dim('# interactive app')}

  {bold('mbx secure on|off|auto')}   {dim('# toggle auth')}
  {bold('mbx mode links|proxy')}     {dim('# toggle media delivery')}
  {bold('mbx doctor --live')}        {dim('# diagnostics')}
""" )
    return 0
