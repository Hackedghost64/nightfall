"""🌙 Nightfall Guide — whole project, use cases, not just commands."""
from __future__ import annotations

GUIDE = r"""
╭──────────────────────────────────────────────────╮
│  🌙  NIGHTFALL · Private Cinema Gateway          │
│  MovieBox-only (anime → ../anime-app)            │
│  Gateway :8399 · TUI · CLI · API                 │
╰──────────────────────────────────────────────────╯

WHAT IS NIGHTFALL?
  Self-contained private-repo-ready MovieBox gateway. One FastAPI (0.0.0.0:8399)
  translates clean API → signed upstream app traffic (HMAC-MD5, HMAC-MD5_v2,
  failover, GW.4410 clock-skew fix). Repairs by editing ONE file: config.yaml.
  Wrapper stays untouched as knowledge base.

ARCHITECTURE
  ┌─ run.sh (./run.sh [guide|tui|up|query|...])  ← one entrypoint, no pip
  ├─ nightfall/config.yaml ─── ALL volatile facts (hosts/secrets/headers)
  ├─ nightfall/main.py ─────── FastAPI 0.1.0, lifespan, auth_guard, rate-limit
  ├─ nightfall/upstream/ ───── signers.py (HMAC-MD5), identity.py (device.json),
  │                            client.py (signed httpx, failover, X-User harvest)
  ├─ nightfall/cache.py ────── TTL per-bucket (metadata 1h, search 10m, stream 2m)
  │                            + distributed (memory|file|redis) via CacheBucket
  ├─ nightfall/fingerprint.py ─ dynamic header & fingerprint spoofing (per_request)
  ├─ nightfall/selfheal/ ───── extractor (AXML), doctor, healer (watch/*.apk)
  ├─ nightfall/tui.py ──────── Textual TUI (Results/Trending/History, episode picker)
  └─ protocol/protocol.yaml ── single source of truth + backups/

INSTALL
  git clone <repo> nightfall && cd nightfall
  ./run.sh guide          # this guide
  ./run.sh setup          # env check (python, ffmpeg, players), start daemon, keys
  ./run.sh tui            # if gateway down, auto `up` then TUI
  Alternative: ./run.sh serve --host 0.0.0.0 --port 8399  (foreground, /docs)

QUICK HEALTH
  ./run.sh status         # {"running":true,"healthy":true,"wrapper_state":"HEALTHY"}
  curl 127.0.0.1:8399/health | jq
  ./run.sh doctor --live  # DNS/TLS, signature probe, session auth

USE CASES (not just commands)

  1) Couch — TUI binge
     ./run.sh tui  → "/" focus search, type "breaking bad", Enter, pick
     → Detail pane: badges, synopsis, dubs, episodes (OptList), quality (1080p/720p/480p)
     → `p` play (mpv→vlc→ffplay auto-detect, cookie injected), `d` download, `r` rankings
     Use when: TV-connected laptop, no browser.

  2) Quick CLI — one-liner watch
     ./run.sh query "breaking bad"        # MovieBox search, pick number → VLC
     ./run.sh query "breaking bad" 2x03   # S2E3 directly, no prompt
     ./run.sh play "breaking bad" 5x1     # legacy one-shot (search→play)
     Use when: SSH, automation, no TUI.

  3) Home-network universal API — phone/TV/browser
     Gateway binds 0.0.0.0:8399, Swagger /docs. Any LAN device:
       export KEY=$(cat data/cli.key) # or ./run.sh key create phone
       curl -H "X-API-Key: $KEY" http://192.168.1.10:8399/search?q=dune
       curl -H "X-API-Key: $KEY" "http://192.168.1.10:8399/titles/138246.../stream?season=1&episode=1"
     Use when: Kodi, custom app, TV browser.

  4) Download manager — binge offline
     ./run.sh download "dune" 1x01        # → downloads/Dune.S01E01.mp4
     TUI `d` on any result, `httpx` streaming + Range resume, max 3 concurrent (downloader.py:12)
     Template: downloads.filename_template in config.yaml.
     Use when: pre-download season.

  5) Self-healing after upstream rotates secrets
     Upstream flips to PROTOCOL_STALE after 3 auth rejections (detector.py).
     ./run.sh doctor --live    # shows [FAIL] signature
     Drop new APK into watch/ → ./run.sh heal  (extract → diff → probe → commit, healer.py)
     Or edit config.yaml moviebox.hmac_secret directly, ./run.sh down/up
     Use when: MovieBox app update.

  6) Headless / cron / automation
     MBX_API_KEY or NIGHTFALL_API_KEY env, `api_soft()` in tui.py handles X-API-Key.
     Script: `for s in {1..3}; do ./run.sh dl "show" ${s}x01; done`
     Use when: unattended.

  7) Distributed cache — multi-device / multi-process
     `config.yaml:cache.backend: memory|file|redis, distributed: true`
     - memory: per-process TTLCache (default)
     - file: data/cache/<kind>/<hash>.json with exp, shared via filesystem lock (HybridCache)
     - redis: redis://127.0.0.1:6379/0, RedisDistributedCache (ex+json, prefix nightfall:<kind>:)
     `GET /cache/stats` → hits/miss, `POST /cache/clear` clears both layers.
     Enable `distributed: true` + `backend: file` for two `run.sh serve` on same host;
     `redis` for LAN multi-host. Cache on `search`/`metadata`/`stream` via _call() in main.py:112
     Use when: TV + phone hit same gateway, or horizontal scale.

  8) Fingerprint spoofing — evade upstream fingerprinting
     `config.yaml:fingerprint.enabled: true, rotation: per_request|per_session|timed, rotation_interval_seconds: 300`
     Profiles: Pixel 8 Pro / Pixel 7 / Samsung SM-S928B / Xiaomi / OnePlus (brand/model/os_version/lang/timezone)
     `fingerprint.py:FingerprintManager` picks random per request (or timed/per_session), DeviceIdentity.client_info() and base_headers() inject brand/model/os/lang/timezone + spoofed Sec-CH-UA/Accept-Language/DNT.
     `GET /fingerprint/stats` → current profile, `POST /fingerprint/rotate` forces rotation.
     Use when: upstream throttles single device fingerprint.

CONFIG — ONE FILE TO REPAIR (config.yaml, nightfall/config.py:48)
  moviebox.api_hosts, hmac_secret, signing_version
  server.host/port, player.preferred, downloads.directory, mode, region
  cache.* , fingerprint.* , rate_limit_per_minute, selfheal.*, security.require_api_key

API SUMMARY
  GET /health, /docs, /openapi.json, /cache/stats, /fingerprint/stats
  GET /search?q=&page=&per_page=, /search/suggest, /search/rank
  GET /titles/{id}, /titles/{id}/seasons|episodes|stream|download|subtitles|resources
  GET /discover/tabs, /proxy/media?url= (allowlist)
  POST /heal, GET /doctor?live=1, GET /protocol, /keys, POST /cache/clear, POST /fingerprint/rotate

DISTRIBUTED CACHING — HOW
  HybridCache: mem (TTLCache) + file/redis. `CacheBucket.for_kind("search").get("search:q:1")` checks mem → file/redis → miss → upstream → set both. File: data/cache/<kind>/sha.json {exp,val}. Redis: ex = exp-now.

FINGERPRINT SPOOFING — HOW
  DeviceIdentity.base_headers() merges FingerprintManager.current_profile() into `user-agent` (`MovieBox/... (Android 14; Pixel 8 Pro)`) and `X-Client-Info` JSON, plus spoofed `Sec-Ch-Ua` etc. Rotation per_request = new random each call.

TROUBLESHOOTING
  Port busy → lsof -i :8399; ./run.sh down
  Gateway offline → ./run.sh status; cat logs/server.log
  Signature rejected → ./run.sh doctor --live; edit config.yaml or heal
  Cache stale → POST /cache/clear or ./run.sh down/up
  Fingerprint blocked → ./run.sh guide | grep fingerprint; POST /fingerprint/rotate

ANIME SEPARATED
  Anime (Anilab2/Kyoto, play.anidb.app CF) moved to ../anime-app (curl_cffi chrome, anilab.html proxy). Nightfall is MovieBox-only to avoid DuplicateID crash.

LINKS
  README.md (full), docs/PROTOCOL.md, docs/PROVISIONING.md, nightfall/cache.py:4, nightfall/fingerprint.py:1, nightfall/main.py:28

Run: ./run.sh guide            # this whole guide
     ./run.sh guide --json      # JSON (for scripts)
     ./run.sh guide --use-cases # only use cases
"""

import json as _json

def print_guide(use_cases_only: bool = False, as_json: bool = False) -> int:
    if as_json:
        data = {
            "project": "nightfall",
            "description": "MovieBox gateway (anime separated)",
            "guide": GUIDE.strip(),
            "use_cases": [l.strip() for l in GUIDE.splitlines() if l.strip().startswith(("1)", "2)", "3)", "4)", "5)", "6)", "7)", "8)"))],
        }
        print(_json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    if use_cases_only:
        # extract use cases block
        in_uc = False
        for line in GUIDE.splitlines():
            if "USE CASES" in line: in_uc = True
            if in_uc:
                print(line)
                if "CONFIG — ONE FILE" in line: break
        return 0
    print(GUIDE)
    return 0

def guide_data() -> dict:
    return {"guide": GUIDE.strip()}
