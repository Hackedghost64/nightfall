"""nightfall CLI — unified cinema & anime gateway control.
  setup, tui, up/down/status, serve, mode, secure, key, play, dl, doctor, heal
  + NEW: query, download, selfheal (anime), anime search
"""
from __future__ import annotations
import argparse, json, re, sys, asyncio
from pathlib import Path

def _set_config_line(path, key_pattern: str, new_line: str) -> bool:
    pat=re.compile(key_pattern)
    lines=path.read_text().splitlines(keepends=True)
    for i, ln in enumerate(lines):
        if pat.match(ln):
            lines[i]=new_line+("\n" if not ln.endswith("\n") else "")
            path.write_text("".join(lines)); return True
    return False

def main() -> int:
    ap=argparse.ArgumentParser(prog="nightfall", description="🌙 NIGHTFALL · Private Cinema Gateway — Movies + Anime unified :8399")
    sub=ap.add_subparsers(dest="cmd")
    sub.required=False
    sub.add_parser("setup", help="first-run wizard")
    sub.add_parser("tui", help="interactive terminal app (movies + anime)")
    p_serve=sub.add_parser("serve", help="run gateway in foreground")
    p_serve.add_argument("--host"); p_serve.add_argument("--port", type=int)
    sub.add_parser("up", help="start gateway daemon")
    sub.add_parser("down", help="stop daemon")
    sub.add_parser("status", help="gateway + protocol state")
    p_mode=sub.add_parser("mode", help="media delivery mode (links|proxy)")
    p_mode.add_argument("value", nargs="?")
    p_sec=sub.add_parser("secure", help="API-key enforcement (on|off|auto)")
    p_sec.add_argument("value", nargs="?")
    p_key=sub.add_parser("key", help="API key management")
    key_sub=p_key.add_subparsers(dest="key_cmd", required=True)
    k_create=key_sub.add_parser("create"); k_create.add_argument("name")
    key_sub.add_parser("list")
    k_revoke=key_sub.add_parser("revoke"); k_revoke.add_argument("prefix")
    p_play=sub.add_parser("play", help='search & stream: play "query" sXeY')
    p_play.add_argument("query"); p_play.add_argument("sxe")
    p_dl=sub.add_parser("dl", help='search & download: dl "query" sXeY')
    p_dl.add_argument("query"); p_dl.add_argument("sxe")
    # NEW commands per plan
    p_q=sub.add_parser("query", help='unified search: query "Solo Leveling" [--anime|--movies] [1x1]')
    p_q.add_argument("q", nargs="?", default="")
    p_q.add_argument("sxe", nargs="?", default=None)
    p_q.add_argument("--anime", action="store_true", help="anime only")
    p_q.add_argument("--movies", action="store_true", help="movies only")
    p_q.add_argument("--page", type=int, default=1)
    p_download=sub.add_parser("download", help='download: download "Solo Leveling" 1x1')
    p_download.add_argument("query"); p_download.add_argument("sxe", nargs="?", default="1x1")
    p_doc=sub.add_parser("doctor"); p_doc.add_argument("--live", action="store_true")
    sub.add_parser("heal")
    p_self=sub.add_parser("selfheal", help="selfheal: check|fix|report")
    p_self.add_argument("action", nargs="?", default="check", choices=["check","fix","report","doctor"])
    p_ext=sub.add_parser("extract"); p_ext.add_argument("source")
    args=ap.parse_args()
    from .banner import banner, ok, info, fail
    banner()
    if args.cmd is None:
        from . import daemon
        st=daemon.status()
        if not st["running"]:
            info("starting gateway…")
            res=daemon.up()
            if res["status"].startswith("started-unverified"):
                fail("gateway failed to start - check logs/server.log"); return 1
        from .tui import main as tui_main
        return tui_main()
    if args.cmd=="setup":
        from .setup_wizard import run as setup_run
        return setup_run()
    if args.cmd=="mode":
        from .config import settings as _s
        if args.value:
            if args.value not in ("links","proxy"): info("value must be links|proxy"); return 1
            if _set_config_line(_s().config_path, r"^\s*mode\s*:", f"mode: {args.value}"): ok(f"media delivery mode → {args.value} (restart to apply)")
            else: info("config.yaml has no 'mode:' line")
        else: info(f"current mode: {_s().get('mode')}   usage: nightfall mode links|proxy")
        return 0
    if args.cmd=="secure":
        from .config import settings as _s
        mapping={"on":"true","off":"false","auto":"auto"}
        cur=_s().get("security.require_api_key","auto")
        pretty={"true":"on","false":"off"}.get(str(cur), str(cur))
        if args.value:
            val=mapping.get(args.value)
            if val is None: info("value must be on|off|auto"); return 1
            if _set_config_line(_s().config_path, r"^\s*require_api_key\s*:", f"  require_api_key: {val}"): ok(f"API-key enforcement → {args.value} (restart to apply)")
            else: info("security.require_api_key line not found")
        else: info(f"API-key enforcement: {pretty}   usage: nightfall secure on|off|auto")
        return 0
    if args.cmd in ("up","down","status"):
        from . import daemon
        fn={"up":daemon.up,"down":daemon.down,"status":daemon.status}[args.cmd]
        print(json.dumps(fn(), indent=2)); return 0
    if args.cmd=="serve":
        import uvicorn
        from .config import settings
        host=args.host or settings().get("server.host","0.0.0.0")
        port=args.port or int(settings().get("server.port",8399))
        info(f"Serving Nightfall on {host}:{port} — docs at http://{host}:{port}/docs")
        uvicorn.run("nightfall.main:app", host=host, port=port)
        return 0
    if args.cmd=="key":
        from .config import settings
        from .security import ApiKeyStore
        store=ApiKeyStore(settings().device_file.parent)
        if args.key_cmd=="create":
            out=store.create(args.name)
            print(json.dumps(out["record"], indent=2))
            print("\nKEY (shown once): "+out["plaintext"])
            print("use via header:  X-API-Key: "+out["plaintext"])
        elif args.key_cmd=="list": print(json.dumps(store.list(), indent=2))
        elif args.key_cmd=="revoke": print("revoked:", store.revoke(args.prefix))
        return 0
    if args.cmd in ("play","dl"):
        from .tui import api_soft as api, detect_player, launch_player, parse_sxe, smart_download
        d=api("/search", {"q": args.query})
        results=((d.get("normalized") or {}).get("results")) or []
        season_titles=[r for r in results if r.get("title","").lower()==f"{args.query.lower()} s{parse_sxe(args.sxe)[0]}"]
        picked=season_titles[0] if season_titles else (results[0] if results else None)
        if not picked: print("no results"); return 1
        tid=str(picked["id"]); se, ep_=parse_sxe(args.sxe)
        print(f"→ {picked['title']}  S{se}E{ep_:02d}  (id={tid})")
        dl=api("/titles/"+tid+"/download", {"season": se, "episode": ep_, "pages":3}) or {}
        res=dl.get("selected") if dl.get("count") else None
        st=api("/titles/"+tid+"/stream", {"season": se, "episode": ep_}) or {}
        stream=st.get("selected")
        if args.cmd=="dl":
            ok2, dest=smart_download(res, stream, picked["title"], se, ep_)
            print(("saved: "+str(dest)) if ok2 else "FAILED"); return 0 if ok2 else 1
        url=(res or {}).get("signed_url") or (stream or {}).get("url")
        player=detect_player()
        if not player: print(f"no mpv/vlc found. open manually:\n  {url}"); return 1
        title=str(picked["title"])
        proc=launch_player(player, url, f"{title} S{se}E{ep_:02d}", cookie=(stream or {}).get("cookie"))
        print(f"launched {player} (pid {proc.pid})"); return proc.wait() or 0

    # --- query (unified) ---
    if args.cmd=="query":
        import urllib.request, urllib.parse
        from .config import settings as _s
        from .tui import detect_player, launch_player
        if not args.q:
            info('usage: nightfall query "Solo Leveling" [--anime|--movies] [1x1]'); return 1
        # concurrent search via gateway if running, else direct via clients
        sxe = args.sxe
        # detect if sxe looks like 1x1
        se_ep=None
        if sxe and re.match(r"^\d+[xX]\d+$", sxe):
            se_ep=sxe; query_str=args.q
        else:
            # if sxe is not None but not sxe pattern, it's part of query? treat as query continuation
            if sxe: query_str=f"{args.q} {sxe}"
            else: query_str=args.q
        print(f"🔍 Query: {query_str}  (--anime={args.anime} --movies={args.movies})")
        # Try gateway first
        from .tui import api_soft
        movies=[]
        anime=[]
        if not args.anime:
            try:
                d=api_soft("/search", {"q": query_str})
                if d and d.get("normalized"): movies=((d["normalized"] or {}).get("results") or [])[:8]
            except Exception: pass
        if not args.movies:
            try:
                # anime via gateway /anime/search
                d2=api_soft("/anime/search", {"q": query_str})
                if d2 and d2.get("posts"): anime=d2["posts"][:8]
                else:
                    # fallback direct
                    from .anilab.client import AnilabClient
                    async def _a():
                        c=AnilabClient(); r=await c.search(query_str); await c.close(); return r
                    anime=asyncio.run(_a())[:8]
            except Exception as e:
                # direct fallback
                try:
                    from .anilab.client import AnilabClient
                    async def _a2():
                        c=AnilabClient(); r=await c.search(query_str); await c.close(); return r
                    anime=asyncio.run(_a2())[:8]
                except Exception: pass
            # hydrate anime titles (search returns only id/poster, need post detail)
            hydrated_anime=[]
            for a in anime:
                if not a.get("title"):
                    try:
                        d3=api_soft(f"/anime/post/{a.get('id')}")
                        info=(d3 or {}).get("data") or {}
                        if info.get("title"): a=dict(a); a["title"]=info["title"]; a["score"]=info.get("score") or a.get("score")
                    except Exception: pass
                hydrated_anime.append(a)
            anime=hydrated_anime
        merged=[]
        for m in movies: merged.append(("[M]", m.get("title") or m.get("id"), m))
        for a in anime: merged.append(("[A]", a.get("title") or f"Anime {a.get('id')}", a))
        if not merged: print("No results found."); return 1
        for i,(tag,title,obj) in enumerate(merged,1):
            extra=""
            if tag=="[A]": extra=f" id={obj.get('id')} score={obj.get('score') or ''}"
            else: extra=f" id={obj.get('id')} year={obj.get('year') or ''}"
            print(f" {i:2d}. {tag} {title}{extra}")
        try: choice=input("\nPick number (or 'q' to quit): ").strip()
        except EOFError: return 0
        if choice.lower() in ("q",""): return 0
        try: idx=int(choice)-1
        except: print("invalid"); return 1
        if not (0 <= idx < len(merged)): print("out of range"); return 1
        tag,_,obj=merged[idx]
        if tag=="[M]":
            tid=str(obj["id"])
            se,ep = (1,1)
            if se_ep:
                try:
                    from .tui import parse_sxe as ps
                    se,ep=ps(se_ep)
                except: pass
            print(f"→ MovieBox: {obj.get('title')} id={tid} S{se}E{ep}")
            d=api_soft(f"/titles/{tid}/stream", {"season":se,"episode":ep}) or {}
            stream=d.get("selected")
            dl=api_soft(f"/titles/{tid}/download", {"season":se,"episode":ep}) or {}
            res=dl.get("selected")
            url=(res or {}).get("signed_url") or (stream or {}).get("url")
            if not url: print("No stream URL found. Try ?debug_raw=true"); return 1
            player=detect_player()
            if not player: print(f"Open manually:\n  {url}"); return 0
            proc=launch_player(player, url, f"{obj.get('title')} S{se}E{ep:02d}", cookie=(stream or {}).get("cookie"))
            print(f"launched {player} (pid {proc.pid})"); return proc.wait() or 0
        else:
            pid=str(obj.get("id"))
            # need episodes -> servers -> stream
            from .anilab.kyoto import KyotoResolver
            async def _resolve():
                ky=KyotoResolver()
                eps=await ky.get_episodes(pid)
                if not eps: raise RuntimeError("no episodes")
                # pick episode
                target_ep=eps[0]["id"]
                if se_ep:
                    try:
                        from .tui import parse_sxe as ps
                        se,epn=ps(se_ep)
                        # episodes have num field; try to match num == epn
                        # episode list order = ep num; approximate
                        if 1 <= epn <= len(eps):
                            target_ep=eps[epn-1]["id"]
                    except: pass
                srvs=await ky.get_servers(pid, target_ep)
                if not srvs: raise RuntimeError("no servers")
                # prefer sub
                pref=next((s for s in srvs if s["lang"]=="sub"), srvs[0])
                stream=await ky.resolve_stream(pid, pref["id"])
                await ky.close(); return eps, srvs, stream, target_ep
            try: eps,srvs,stream,target_ep=asyncio.run(_resolve())
            except Exception as e: print(f"Anime resolve failed: {e}"); return 1
            url=stream["url"]
            print(f"→ Anime: id={pid} ep={target_ep} server={srvs[0]['id']}  -> {url[:80]}...")
            # launch in vlc
            from .tui import detect_player, launch_player
            player=detect_player()
            if not player: print(f"Open manually (VLC → Ctrl+N):\n  {url}"); return 0
            # VLC needs no cookie for Kyoto HLS
            proc=launch_player(player, url, f"Anime {pid} ep {target_ep}")
            print(f"launched {player} (pid {proc.pid})"); return proc.wait() or 0

    if args.cmd=="download":
        from .tui import api_soft, smart_download, parse_sxe, detect_player
        try: se,ep = parse_sxe(args.sxe)
        except Exception as e: print(f"bad sxe {args.sxe}: {e}"); return 1
        print(f"⬇  Downloading: {args.query} S{se}E{ep:02d}")
        # try moviebox first via gateway
        d=api_soft("/search", {"q": args.query})
        results=((d.get("normalized") or {}).get("results")) or []
        picked=results[0] if results else None
        if picked:
            tid=str(picked["id"])
            dl=api_soft(f"/titles/{tid}/download", {"season":se,"episode":ep}) or {}
            res=dl.get("selected")
            st=api_soft(f"/titles/{tid}/stream", {"season":se,"episode":ep}) or {}
            stream=st.get("selected")
            ok2,dest=smart_download(res, stream, picked["title"], se, ep)
            print(("saved: "+str(dest)) if ok2 else "FAILED (moviebox)"); 
            if ok2: return 0
        # fallback anime: download m3u8 via ffmpeg or direct
        print("MovieBox not found or failed, trying Anime pipeline...")
        try:
            from .anilab.client import AnilabClient
            from .anilab.kyoto import KyotoResolver
            async def _anime_dl():
                c=AnilabClient(); posts=await c.search(args.query); await c.close()
                if not posts: raise RuntimeError("anime not found")
                pid=str(posts[0].get("id"))
                ky=KyotoResolver(); eps=await ky.get_episodes(pid)
                if ep <1 or ep > len(eps): raise RuntimeError("episode out of range")
                ep_id=eps[ep-1]["id"] if se==1 else eps[0]["id"]
                srvs=await ky.get_servers(pid, ep_id)
                pref=next((s for s in srvs if s["lang"]=="sub"), srvs[0])
                stream=await ky.resolve_stream(pid, pref["id"])
                await ky.close(); return posts[0], stream
            post, stream=asyncio.run(_anime_dl())
            url=stream["url"]
            # use ffmpeg via smart_download path: stream download
            from .tui import smart_download as sd
            # need to handle HLS download via ffmpeg_stream_download
            title=str(post.get("title") or args.query)
            ok2,dest=sd(None, {"url": url}, title, se, ep)
            print(("saved: "+str(dest)) if ok2 else f"FAILED — manual URL: {url}"); return 0 if ok2 else 1
        except Exception as e:
            print(f"Anime download failed: {e}"); return 1

    # maintenance
    from .config import settings
    from .protocol_store import ProtocolStore
    store=ProtocolStore(settings().protocol_file)
    if args.cmd=="doctor":
        from .selfheal.detector import StalenessDetector
        from .selfheal.doctor import run as doctor_run
        rep=doctor_run(store, StalenessDetector(), live=args.live)
        print(json.dumps(rep.to_dict(), indent=2)); return 0 if rep.healthy else 1
    if args.cmd=="heal":
        from .selfheal.detector import StalenessDetector
        from .selfheal.healer import Healer
        from .upstream.identity import DeviceIdentity
        ident=DeviceIdentity(settings().device_file, store.data)
        rep=Healer(store, ident).scan_and_heal(detector=StalenessDetector())
        print(json.dumps(rep.to_dict(), indent=2)); return {"healed":0,"current":0}.get(rep.status,1)
    if args.cmd=="selfheal":
        from .selfheal.detector import StalenessDetector
        from .selfheal.doctor import run as doctor_run
        if args.action in ("check","doctor"):
            rep=doctor_run(store, StalenessDetector(), live=True)
            print(json.dumps(rep.to_dict(), indent=2))
            # also probe anime
            print("\n--- Anime endpoints ---")
            try:
                from .anilab.client import AnilabClient
                async def _probe():
                    c=AnilabClient()
                    try: await c.search("a"); print("[OK] anilab.search")
                    except Exception as e: print(f"[FAIL] anilab.search: {e}")
                    try: await c.categories(); print("[OK] anilab.categories")
                    except Exception as e: print(f"[FAIL] anilab.categories: {e}")
                    await c.close()
                asyncio.run(_probe())
            except Exception as e: print(f"anime probe error: {e}")
            return 0 if rep.healthy else 1
        elif args.action=="fix":
            from .selfheal.healer import Healer
            from .upstream.identity import DeviceIdentity
            ident=DeviceIdentity(settings().device_file, store.data)
            rep=Healer(store, ident).scan_and_heal(detector=StalenessDetector())
            print(json.dumps(rep.to_dict(), indent=2)); return {"healed":0,"current":0}.get(rep.status,1)
        elif args.action=="report":
            rep=doctor_run(store, StalenessDetector(), live=False)
            print(json.dumps(rep.to_dict(), indent=2)); return 0
    if args.cmd=="extract":
        from .selfheal.extractor import extract
        ex=extract(args.source)
        print(json.dumps({"source":ex.source,"app":{"package":ex.package,"version_name":ex.version_name,"version_code":ex.version_code},"secrets":{k: v[:6]+"…" for k,v in ex.secrets.items()},"meta_data_count":len(ex.all_meta)}, indent=2)); return 0
    if args.cmd=="tui":
        from .tui import main as tui_main
        return tui_main()
    return 2
if __name__=="__main__": sys.exit(main())
