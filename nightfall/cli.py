"""mbx - private streaming gateway control.

  setup                         first-run wizard (env, server, API key)
  tui                           interactive terminal app
  up | down | status            daemon lifecycle
  serve [--host --port]         foreground gateway
  mode [links|proxy]            toggle media delivery
  secure [on|off|auto]          toggle API-key enforcement
  key create|list|revoke        API key management
  play "query" sXeY             one-shot: search -> mpv/vlc
  dl   "query" sXeY             one-shot: search -> download
  doctor [--live]               diagnostics
  heal                          watch-folder self-heal scan
"""
from __future__ import annotations

import argparse
import json
import re
import sys


def _set_config_line(path, key_pattern: str, new_line: str) -> bool:
    """Targeted single-line config edit; preserves surrounding format."""
    pat = re.compile(key_pattern)
    lines = path.read_text().splitlines(keepends=True)
    for i, ln in enumerate(lines):
        if pat.match(ln):
            lines[i] = new_line + ("\n" if not ln.endswith("\n") else "")
            path.write_text("".join(lines))
            return True
    return False


def _print_serve_guide(host: str, port: int, compact: bool = False) -> None:
    """Serve setup guide — like ./run.sh guide but focused on `serve`."""
    from .banner import c, dim, green, yellow
    print(c("╭──────────────────────────────────────────────────╮", "35"))
    print(c("│  🌙  NIGHTFALL · Serve Setup Guide               │", "35"))
    print(c("╰──────────────────────────────────────────────────╯", "35"))
    print(f"  Host {host}:{port}  (config.yaml: server.host/port, env: NIGHTFALL_SERVER__PORT)")
    # Always show API key (user requested)
    try:
        from pathlib import Path
        from .config import settings as _s
        cfg = _s()
        # cli.key is the auto local-cli key used by TUI/CLI
        cli_key = Path(cfg.device_file.parent) / "cli.key"
        api_keys = Path(cfg.device_file.parent) / "api_keys.json"
        shown = False
        if cli_key.exists():
            k = cli_key.read_text().strip()
            if k:
                print(f"  {green('🔑 API key')}  {k}  {dim('(header: X-API-Key: '+k[:10]+'…)')}")
                print(f"     curl -H \"X-API-Key: {k}\" http://127.0.0.1:{port}/health")
                shown = True
        if api_keys.exists() and not shown:
            import json as _j
            try:
                lst = _j.loads(api_keys.read_text())
                if lst:
                    print(f"  {yellow('🔑 API keys')}: {len(lst)} in data/api_keys.json")
                    for rec in lst[:3]:
                        print(f"     - {rec.get('name')}  prefix={rec.get('prefix')}")
                    shown = True
            except: pass
        if not shown:
            print(f"  {yellow('🔑 API key')}  none yet — run: ./run.sh key create phone")
            print(f"     then: curl -H \"X-API-Key: $KEY\" http://127.0.0.1:{port}/search?q=dune")
        print()
    except Exception:
        pass
    steps = [
        ("1. Port free?", f"lsof -i :{port}  # if 8399 in use: ./run.sh down  or  ./run.sh serve --port {port+1}"),
        ("2. Firewall (LAN)", f"sudo ufw allow {port}/tcp  # else phone can't reach"),
        ("3. LAN IP", f"ip route get 1.1.1.1 | awk '{{print $7}}'  # → http://<LAN_IP>:{port}/docs"),
        ("4. API key", "./run.sh key create phone  # header X-API-Key: $KEY  (auto if ./run.sh setup)"),
        ("5. Start", f"./run.sh serve  # this — or  ./run.sh up  (daemon)"),
        ("6. Verify", f"curl -H \"X-API-Key: $KEY\" http://127.0.0.1:{port}/health | jq  # {{ok:true, cache, fingerprint}}"),
        ("7. Docs", f"http://127.0.0.1:{port}/docs  (Swagger, try /search, /titles/{{id}}/stream)"),
        ("8. If 98 (in use)", f"./run.sh status; ./run.sh down; lsof -i :{port}"),
        ("9. Root 401?", "GET / → 401 is normal (needs X-API-Key). Use /health or /docs"),
    ]
    for title, cmd in steps:
        if compact and title.startswith(("7.", "8.", "9.")):
            continue
        print(f"  {c(title, '36')}  {dim(cmd)}" if not compact else f"  {title} {cmd}")
    if not compact:
        print()
        print(dim("  Tip: ./run.sh serve --quick  # skip this guide"))
        print(dim("       ./run.sh serve --guide  # only guide, don't start"))
        print(dim("       ./run.sh guide            # whole project use-cases (caching, fingerprint)"))
    else:
        print(dim("  --guide for full steps, --quick to hide"))
    print()


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="nightfall",
        description="🌙 NIGHTFALL · Private Cinema Gateway — MovieBox only (anime separated)")
    sub = ap.add_subparsers(dest="cmd")
    sub.required = False

    sub.add_parser("setup", help="first-run wizard: env check, server, API key")
    sub.add_parser("tui", help="interactive terminal app")

    p_serve = sub.add_parser("serve", help="run gateway in foreground (use --guide for setup steps)")
    p_serve.add_argument("--host"); p_serve.add_argument("--port", type=int)
    p_serve.add_argument("--guide", action="store_true", help="show serve setup guide and exit")
    p_serve.add_argument("--quick", action="store_true", help="skip guide, start immediately")
    sub.add_parser("up", help="start gateway as background daemon")
    sub.add_parser("down", help="stop daemon")
    sub.add_parser("status", help="gateway + protocol state")

    p_mode = sub.add_parser("mode", help="media delivery mode (links|proxy)")
    p_mode.add_argument("value", nargs="?")

    p_sec = sub.add_parser("secure", help="API-key enforcement (on|off|auto)")
    p_sec.add_argument("value", nargs="?")

    p_key = sub.add_parser("key", help="API key management")
    key_sub = p_key.add_subparsers(dest="key_cmd", required=True)
    k_create = key_sub.add_parser("create"); k_create.add_argument("name")
    key_sub.add_parser("list")
    k_revoke = key_sub.add_parser("revoke"); k_revoke.add_argument("prefix")

    p_play = sub.add_parser("play", help='search & stream: play "query" sXeY')
    p_play.add_argument("query"); p_play.add_argument("sxe")

    p_dl = sub.add_parser("dl", help='search & download: dl "query" sXeY')
    p_dl.add_argument("query"); p_dl.add_argument("sxe")
    p_q = sub.add_parser("query", help='search: query "breaking bad" [1x1]')
    p_q.add_argument("q", nargs="?", default="")
    p_q.add_argument("sxe", nargs="?", default=None)
    p_q.add_argument("--page", type=int, default=1)

    p_doc = sub.add_parser("doctor")
    p_doc.add_argument("--live", action="store_true")
    sub.add_parser("heal")
    p_ext = sub.add_parser("extract"); p_ext.add_argument("source")
    p_guide = sub.add_parser("guide", help="whole project guide: use cases, setup, architecture (not just commands)")
    p_guide.add_argument("--json", action="store_true", help="JSON output for scripts")
    p_guide.add_argument("--use-cases", action="store_true", help="only use cases section")

    args = ap.parse_args()

    from .banner import banner, ok, info, fail, warn
    banner()

    # ---- no args: auto mode (ensure gateway -> interactive app) ------------
    if args.cmd is None:
        from . import daemon
        st = daemon.status()
        if not st["running"]:
            info("starting gateway…")
            res = daemon.up()
            if res["status"].startswith("started-unverified"):
                fail("gateway failed to start - check logs/server.log")
                return 1
        from .tui import main as tui_main
        return tui_main()

    # ---- setup -------------------------------------------------------------
    if args.cmd == "setup":
        from .setup_wizard import run as setup_run
        return setup_run()

    # ---- toggles -----------------------------------------------------------
    if args.cmd == "mode":
        from .config import settings as _s
        if args.value:
            if args.value not in ("links", "proxy"):
                info("value must be links|proxy"); return 1
            if _set_config_line(_s().config_path, r"^\s*mode\s*:",
                                f"mode: {args.value}"):
                ok(f"media delivery mode → {args.value} (restart to apply)")
            else:
                info("config.yaml has no 'mode:' line")
        else:
            info(f"current mode: {_s().get('mode')}   usage: mbx mode links|proxy")
        return 0

    if args.cmd == "secure":
        from .config import settings as _s
        mapping = {"on": "true", "off": "false", "auto": "auto"}
        cur = _s().get("security.require_api_key", "auto")
        pretty = {"true": "on", "false": "off"}.get(str(cur), str(cur))
        if args.value:
            val = mapping.get(args.value)
            if val is None:
                info("value must be on|off|auto"); return 1
            if _set_config_line(_s().config_path, r"^\s*require_api_key\s*:",
                                f"  require_api_key: {val}"):
                ok(f"API-key enforcement → {args.value} (restart to apply)")
            else:
                info("config.yaml security.require_api_key line not found")
        else:
            info(f"API-key enforcement: {pretty}   usage: mbx secure on|off|auto")
        return 0

    # ---- lifecycle ---------------------------------------------------------
    if args.cmd in ("up", "down", "status"):
        from . import daemon
        fn = {"up": daemon.up, "down": daemon.down, "status": daemon.status}[args.cmd]
        print(json.dumps(fn(), indent=2))
        return 0

    if args.cmd == "serve":
        import uvicorn
        from .config import settings as _cfg
        host = args.host or _cfg().get("server.host", "0.0.0.0")
        port = args.port or int(_cfg().get("server.port", 8399))
        # --guide: show setup steps without starting (like ./run.sh guide but serve-focused)
        if args.guide:
            _print_serve_guide(host, port)
            return 0
        # default: show compact serve setup guide (like guide) then start — skip with --quick
        if not args.quick:
            _print_serve_guide(host, port, compact=True)
        # pre-flight: if port already bound, show helpful remediation (as seen in 8399 already-in-use)
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", int(port))) == 0:
                warn(f"port {port} already in use (daemon `up` running?). Try:")
                print(f"    ./run.sh status          # check daemon")
                print(f"    ./run.sh down            # free {port}")
                print(f"    ./run.sh serve --port {int(port)+1}  # or alternate port")
                print(f"    lsof -i :{port}           # who holds it")
                # still try to start — uvicorn will error gracefully as before
        uvicorn.run("nightfall.main:app", host=host, port=port)
        return 0

    # ---- keys ------------------------------------------------------------
    if args.cmd == "key":
        from .config import settings
        from .security import ApiKeyStore
        store = ApiKeyStore(settings().device_file.parent)
        if args.key_cmd == "create":
            out = store.create(args.name)
            print(json.dumps(out["record"], indent=2))
            print("\nKEY (shown once): " + out["plaintext"])
            print("use via header:  X-API-Key: " + out["plaintext"])
        elif args.key_cmd == "list":
            print(json.dumps(store.list(), indent=2))
        elif args.key_cmd == "revoke":
            print("revoked:", store.revoke(args.prefix))
        return 0

    # ---- one-shot play / download ----------------------------------------
    if args.cmd in ("play", "dl"):
        from .tui import api_soft as api, detect_player, launch_player, parse_sxe, smart_download
        d = api("/search", {"q": args.query})
        results = ((d.get("normalized") or {}).get("results")) or []
        season_titles = [r for r in results if r.get("title", "").lower()
                         == f"{args.query.lower()} s{parse_sxe(args.sxe)[0]}"]
        picked = season_titles[0] if season_titles else (results[0] if results else None)
        if not picked:
            print("no results"); return 1
        tid = str(picked["id"])
        se, ep_ = parse_sxe(args.sxe)
        print(f"→ {picked['title']}  S{se}E{ep_:02d}  (id={tid})")
        dl = api("/titles/" + tid + "/download",
                 {"season": se, "episode": ep_, "pages": 3}) or {}
        res = dl.get("selected") if dl.get("count") else None
        st = api("/titles/" + tid + "/stream", {"season": se, "episode": ep_}) or {}
        stream = st.get("selected")

        if args.cmd == "dl":
            from .tui import smart_download as _sd
            ok, dest = _sd(res, stream, picked["title"], se, ep_)
            print(("saved: " + str(dest)) if ok else "FAILED")
            return 0 if ok else 1

        url = (res or {}).get("signed_url") or (stream or {}).get("url")
        player = detect_player()
        if not player:
            print(f"no mpv/vlc found. open manually:\n  {url}"); return 1
        title = str(picked["title"])
        proc = launch_player(player, url, f"{title} S{se}E{ep_:02d}",
                             cookie=(stream or {}).get("cookie"))
        print(f"launched {player} (pid {proc.pid})")
        return proc.wait() or 0

    if args.cmd == "query":
        from .tui import api_soft as api, detect_player, launch_player, parse_sxe
        if not args.q:
            info("usage: nightfall query \"breaking bad\" [1x1]"); return 1
        q = args.q
        se_ep = args.sxe
        s_text = f" with {se_ep}" if se_ep else ""
        print(f"🔍 MovieBox search: {q}{s_text}")
        d = api("/search", {"q": q})
        results = ((d.get("normalized") or {}).get("results")) or []
        if not results:
            print("No results."); return 1
        for i, r in enumerate(results[:8], 1):
            print(f" {i:2d}. [M] {r.get('title')} id={r.get('id')} year={r.get('year') or ''} rating={r.get('rating') or ''}")
        try:
            choice = input("\nPick number (or \'q\' to quit): ").strip()
        except EOFError:
            return 0
        if choice.lower() in ("q", ""):
            return 0
        try:
            idx = int(choice) - 1
        except:
            print("invalid"); return 1
        if not (0 <= idx < len(results[:8])):
            print("out of range"); return 1
        picked = results[idx]
        tid = str(picked["id"])
        se, ep = (1, 1)
        if se_ep:
            try:
                se, ep = parse_sxe(se_ep)
            except Exception as e:
                print(f"bad sxe {se_ep}: {e}"); return 1
        print(f"→ MovieBox: {picked.get('title')} id={tid} S{se}E{ep}")
        d2 = api(f"/titles/{tid}/stream", {"season": se, "episode": ep}) or {}
        stream = d2.get("selected")
        dl = api(f"/titles/{tid}/download", {"season": se, "episode": ep}) or {}
        res = dl.get("selected")
        url = (res or {}).get("signed_url") or (stream or {}).get("url")
        if not url:
            print("No stream URL. Try ?debug_raw=true"); return 1
        player = detect_player()
        if not player:
            print(f"Open manually:\n  {url}"); return 0
        proc = launch_player(player, url, f"{picked.get('title')} S{se}E{ep:02d}", cookie=(stream or {}).get("cookie"))
        print(f"launched {player} (pid {proc.pid})"); return proc.wait() or 0

    if args.cmd == "guide":
        from .guide import print_guide
        return print_guide(use_cases_only=bool(args.use_cases), as_json=bool(args.json))

    # ---- maintenance ------------------------------------------------------
    from .config import settings
    from .protocol_store import ProtocolStore
    store = ProtocolStore(settings().protocol_file)

    if args.cmd == "doctor":
        from .selfheal.detector import StalenessDetector
        from .selfheal.doctor import run as doctor_run
        rep = doctor_run(store, StalenessDetector(), live=args.live)
        print(json.dumps(rep.to_dict(), indent=2))
        return 0 if rep.healthy else 1

    if args.cmd == "heal":
        from .selfheal.detector import StalenessDetector
        from .selfheal.healer import Healer
        from .upstream.identity import DeviceIdentity
        ident = DeviceIdentity(settings().device_file, store.data)
        rep = Healer(store, ident).scan_and_heal(detector=StalenessDetector())
        print(json.dumps(rep.to_dict(), indent=2))
        return {"healed": 0, "current": 0}.get(rep.status, 1)

    if args.cmd == "extract":
        from .selfheal.extractor import extract
        ex = extract(args.source)
        print(json.dumps({
            "source": ex.source,
            "app": {"package": ex.package, "version_name": ex.version_name,
                    "version_code": ex.version_code},
            "secrets": {k: v[:6] + "…" for k, v in ex.secrets.items()},
            "meta_data_count": len(ex.all_meta),
        }, indent=2))
        return 0

    if args.cmd == "tui":
        # The TUI talks to the local gateway over HTTP.  Start the daemon
        # automatically so `./run.sh tui` is self-contained, just like the
        # no-argument auto mode above.
        from . import daemon
        st = daemon.status()
        if not st["running"] or not st["healthy"]:
            info("starting local gateway…")
            res = daemon.up()
            if res["status"].startswith("started-unverified"):
                fail("gateway failed to become ready - check logs/server.log")
                return 1
        else:
            from .config import settings as _settings
            info(f"local gateway ready on 127.0.0.1:{int(_settings().get('server.port', 8399))}")
        from .tui import main as tui_main
        return tui_main()

    return 2


if __name__ == "__main__":
    sys.exit(main())
