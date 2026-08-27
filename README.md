# 🌙 Nightfall — Private Cinema Gateway (MovieBox)
---

## Quick Start

```bash
git clone <this-repo> nightfall
cd nightfall
./run.sh setup      # env check → start gateway → provision device → create API key
./run.sh tui        # MovieBox TUI (search, Trending, History)
```

Or foreground (always shows `🔑 API key` + curl + `Serve Setup Guide`):

```bash
./run.sh serve              # compact 8-step guide + API key + start 0.0.0.0:8399
./run.sh serve --guide      # full guide only (no start)
./run.sh serve --quick      # no guide, direct start
./run.sh serve --port 8000  # alt port if 8399 in use (lsof -i :8399 → ./run.sh down)
# Serve prints: Host 0.0.0.0:8399 + 🔑 API key  nf_... (X-API-Key: ...) + curl -H "X-API-Key: $KEY" /health
# GET / → 401 is normal (needs X-API-Key); use /health or /docs
```

---

## Commands

| Command | What it does |
|---|---|
| `./run.sh tui` | MovieBox TUI: search, Trending, History, episode picker, quality (1080p/720p/480p), `d` download, `p` play |
| `./run.sh query "breaking bad"` | MovieBox search, pick → VLC/mpv (`query "Title" 1x1` for S1E1) |
| `./run.sh download "Title" 1x1` | Async download to `downloads/` (httpx streaming, resume via Range, max 3 concurrent) |
| `./run.sh play "breaking bad" 5x1` | One-shot MovieBox: search → stream in mpv/vlc/ffplay |
| `./run.sh dl "peaky blinders" 6x1` | One-shot MovieBox download |
| `./run.sh up / down / status` | Daemon lifecycle (pidfile `data/nightfall.pid`, health probe) |
| `./run.sh serve` | Foreground gateway **+ setup guide** (prints 8-step `Serve Setup Guide`, `--quick` to skip, `--guide` to show only guide) |
| `./run.sh serve --guide` | Serve setup guide only (port/firewall/LAN IP/key/verify/docs) (`cli.py:41` `_print_serve_guide`) |
| `./run.sh serve --quick` | Serve without guide (direct `uvicorn`) |
| `./run.sh mode links\|proxy` | Toggle media delivery (per-request `?mode=proxy` still works) |
| `./run.sh secure on\|off\|auto` | Toggle API-key enforcement |
| `./run.sh key create/list/revoke` | Manage `X-API-Key` clients |
| `./run.sh doctor [--live]` | Layered diagnostics (signing, DNS/TLS, probe) |
| `./run.sh guide` | **Whole project guide** — architecture, 8 use cases, setup (`guide.py:1`), `--json`/`--use-cases` |
| `./run.sh heal` | Scan `watch/` for new APK → extract → diff → verify → commit |
| `./run.sh extract path/to.apk` | Dump secrets/app facts from an APK |

Anime: see `../anime-app/README.md` (Kyoto `curl_cffi` chrome, `anilab.html` proxy, separate FastAPI port).

---

## The API — Home-Network Ready

`nightfall serve` binds on **`0.0.0.0:8399`** — any device on your LAN can use it.

Auth: `X-API-Key: <key>` (auto-enforced once a key exists).  
Public: `/health`, `/docs`, `/openapi.json`.

```
GET  /health                          liveness
GET  /docs                            Swagger UI
GET  /search?q=&page=                 MovieBox search
GET  /titles/{id}                     MovieBox metadata
GET  /titles/{id}/stream?season=&episode=  MovieBox HLS/DASH (+ direct MP4)
GET  /titles/{id}/download?season=&episode= MovieBox direct files
GET  /titles/{id}/subtitles           MovieBox subtitles
POST /heal   GET /doctor?live=1  GET /protocol  /keys
```

Phone/TV/browser can use Nightfall as MovieBox backend:

```bash
curl -H "X-API-Key: $KEY" http://192.168.1.10:8399/search?q=naruto
curl -H "X-API-Key: $KEY" "http://192.168.1.10:8399/titles/590417.../stream?season=1&episode=1"
```

---

## Configuration — One File to Repair

All MovieBox endpoints, headers, and signing secrets live in **`config.yaml`**:

```yaml
moviebox:
  api_hosts: [api6.aoneroom.com, i-api.aoneroom.com]
  hmac_secret: "..."          # rotates → edit here only
  signing_version: "2"

server: {host: "0.0.0.0", port: 8399}
player: {preferred: vlc, fallback_order: [vlc, mpv, ffplay]}
downloads: {directory: downloads}
```

When MovieBox rotates secrets/app-version: `doctor --live` shows `[FAIL] signature`, edit `config.yaml` or drop new APK into `watch/` then `./run.sh heal`.

No anime `anilab`/`kyoto` keys — those live in `../anime-app`.

---

## Distributed Caching

`nightfall/cache.py:4` (`HybridCache` + `CacheBucket`) supports `config.yaml:cache.backend: memory|file|redis`, `distributed: true|false` (`nightfall/config.py:48`, `nightfall/main.py:50`).

| Backend | How | Use when |
|---|---|---|
| `memory` (default) | per-process `TTLCache` (`cache.py:8`), `cache_ttl: metadata 3600/search 600/stream 120` | single instance, dev |
| `file` | `data/cache/<kind>/<hash>.json` `{exp,val}` with file lock (`FileDistributedCache`, `HybridCache`, `nightfall/cache.py:55`), survives restarts, shared via filesystem | two `run.sh serve` on same host, NFS share |
| `redis` | `redis://127.0.0.1:6379/0`, `RedisDistributedCache` `ex`+`json` prefix `nightfall:<kind>:` (`nightfall/cache.py:95`) | LAN multi-host, horizontal scale |

Hybrid reads `mem → file/redis → upstream`, writes both. `GET /cache/stats` / `POST /cache/clear` (auth), `GET /health` includes `cache` hits. Enable:

```yaml
cache:
  backend: file      # or redis
  distributed: true
  redis_url: "redis://127.0.0.1:6379/0"
  file_dir: "data/cache"
```

```bash
./run.sh up
curl -H "X-API-Key: $KEY" http://127.0.0.1:8399/cache/stats | jq
curl -X POST -H "X-API-Key: $KEY" http://127.0.0.1:8399/cache/clear
```

---

## Dynamic Header & Fingerprint Spoofing

`nightfall/fingerprint.py:1` (`FingerprintManager`, `nightfall/upstream/identity.py:74`) rotates device fingerprints to evade upstream throttling.

```yaml
fingerprint:
  enabled: true
  rotation: per_request   # per_request | per_session | timed
  rotation_interval_seconds: 300
  spoof_headers: true
  profiles:
    - {brand: "Google", model: "Pixel 8 Pro", os_version: "14", lang: "en", timezone: "America/New_York"}
    - {brand: "Samsung", model: "SM-S928B", os_version: "14", lang: "en", timezone: "America/New_York"}
    # Xiaomi, OnePlus, Pixel 7 ...
```

`DeviceIdentity.base_headers()` (`upstream/identity.py:97`) injects `brand/model/os_version/lang/timezone` into `user-agent` (`MovieBox/... (Android 14; Pixel 8 Pro)`) and `X-Client-Info` JSON, plus spoofed `Sec-Ch-Ua`, `Sec-Ch-Ua-Mobile`, `Accept-Language`, `DNT` (`fingerprint.py:70` `spoofed_headers()`). Rotation per `per_request` = random each `base_headers()` call, `timed` = every `interval`, `per_session` = once.

```bash
curl http://127.0.0.1:8399/health | jq .fingerprint
curl -H "X-API-Key: $KEY" http://127.0.0.1:8399/fingerprint/stats | jq
curl -X POST -H "X-API-Key: $KEY" http://127.0.0.1:8399/fingerprint/rotate
cat logs/upstream.log | jq .request_headers  # verify X-Client-Info brand rotation
```

Disable: `fingerprint.enabled: false`.

---

## Guide — `./run.sh guide`

Whole-project guide (not just ` --help` commands), `nightfall/guide.py:1`:

```bash
./run.sh guide              # architecture, install, 8 use cases, config, API, troubleshooting
./run.sh guide --use-cases  # only use cases (couch TUI, quick CLI, home API, download manager, self-heal, headless, distributed cache, fingerprint)
./run.sh guide --json       # JSON for scripts
```

Covers: What is Nightfall, Architecture (`run.sh` → `config.yaml` → `main.py` → `upstream/` → `cache.py`/`fingerprint.py`), Install/Quick Health, Use Cases (see `guide.py:15`), Config, API Summary (`GET /health /cache/stats /fingerprint/stats /search /titles/{id}/stream`), Distributed Caching How, Fingerprint How.

---

## Downloads

Default target: `nightfall/downloads/<title>.S01E01.mp4` (gitignored).  
Uses `httpx` streaming + async file writes (no full-file RAM buffering), `Range: bytes=N-` resume, max 3 concurrent.

From TUI: press **`d`** on any result → progress bar in status bar.  
From CLI: `./run.sh download "Title" 1x12`.

---

## Home Network

Gateway listens on `0.0.0.0:8399`. To expose beyond localhost:

```bash
sudo ufw allow 8399/tcp
ip route get 1.1.1.1 | awk '{print $7}'
# phones can now hit http://<LAN_IP>:8399/docs
```

---

## Layout

```
nightfall/
├── run.sh                  ./run.sh [guide|tui|serve|up|down|query|...]
├── nightfall/
│   ├── cli.py              MovieBox CLI (query, play/dl, keys, doctor/heal, guide)
│   ├── guide.py            Whole-project guide (8 use cases, not just --help)
│   ├── tui.py              MovieBox TUI (no anime DuplicateID)
│   ├── config.yaml         MovieBox-only — one file (+ cache/fingerprint)
│   ├── config.py           loader (deep-merge + env overrides)
│   ├── main.py             MovieBox FastAPI (0.0.0.0:8399, /cache/stats, /fingerprint/stats)
│   ├── cache.py            HybridCache (memory/file/redis, TTL, distributed)
│   ├── fingerprint.py      FingerprintManager (dynamic header & device spoofing)
│   ├── security.py         API key + rate limit (public: /health /docs)
│   ├── daemon.py           up/down/status (nightfall.pid)
│   ├── downloader.py       async streaming (httpx + Range + semaphore)
│   ├── banner.py           🌙 NIGHTFALL banner
│   ├── upstream/           MovieBox signed client (HMAC-MD5, failover, GW.4410 skew, fingerprint)
│   └── selfheal/           extractor / doctor / healer (watch/ APK)
├── downloads/              gitignored
├── logs/                   rotating logs
├── data/                   device.json + api_keys.json + cache/ (gitignored)
├── protocol/               protocol.yaml
└── tests/                  pytest

../anime-app/              Separate Kyoto/Anilab2 (curl_cffi chrome, anilab.html)
├── anilab/{client.py,kyoto.py,cache.py,routes.py}
└── anime.html
```

---

## Why Anime Separated

TUI crashed on `Solo Leveling` (post `1000004883`) due to `DuplicateID: anime_srv_16704/jpn` — Kyoto returns duplicate server IDs (`16704/jpn` twice with same lang/name, `tui.py:986` `dubs_list.add_option(id='anime_srv_16704/jpn')` twice). Fix would deduplicate, but Cloudflare `play.anidb.app` also requires `curl_cffi` chrome impersonate (added `kyoto.py:15` `_curl_get_json_sync`). To keep Nightfall stable and MovieBox-only, anime stack moved to `../anime-app` as independent FastAPI (separate port, no DuplicateID impact).

---

## Source Material Map

| Source | What was ported | Target |
|---|---|---|
| `movie-app/wrapper/` | MovieBox gateway | `nightfall/` (MovieBox-only, 0.0.0.0, downloads/) |
| `hacking/anilab_*` etc. | Kyoto/Anilab | `../anime-app/` (separate) |

---

## Troubleshooting

*`MBX_API_KEY` env still works* (legacy). Prefer `NIGHTFALL_API_KEY` or `X-API-Key` header.

*VLC not opening?* `vlc <m3u8_url>` works without cookies. MovieBox streams needing cookies use `mpv --http-header-fields=Cookie:` via `./run.sh play`.

*Port busy?* `lsof -i :8399` then `./run.sh down`. `serve` now shows compact guide + `🔑 API key` before `Uvicorn running` (`cli.py:35` `_print_serve_guide`); use `--quick` to hide, `--guide` for full 9-step guide without start.

*Serve shows `GET / 401 Unauthorized` / `GET /favicon.ico 401`?* Normal — auth guard `security.py:68` public only `/health,/docs,/openapi.json`. Browser hits `/` → 401; use `http://127.0.0.1:8399/health` (public) or `http://127.0.0.1:8399/docs` with `X-API-Key`. Serve guide step 9 documents this.

*API key not shown?* `serve` always prints `🔑 API key  nf_...` from `data/cli.key` (`cli.py:41`) + `curl -H "X-API-Key: $KEY" /health`. If `none yet`, run `./run.sh key create phone` or `./run.sh setup`.

*Anime previously showed “Anime 12345” or “No episodes found”?* See `../anime-app/README.md` — upstream `search/latest` returns only `id/poster`, hydrates via `/anime/post/{id}`; now fixed with `curl_cffi`.

---

Private use only. Upstream APIs belong to their respective services.
