# 🌙 Nightfall — Private Cinema Gateway

> **Movies + Anime, one unified gateway on `:8399`.**  
> Fast, no lag, downloads default to `nightfall/downloads/`.  
> Repair when upstream rotates secrets: edit `config.yaml` — nothing else.  
> `movie-app/wrapper` stays untouched as the knowledge base.

---

## Quick Start

```bash
git clone <this-repo> nightfall
cd nightfall
./run.sh setup      # env check → start gateway → provision device → create API key
./run.sh tui        # interactive app (movies + anime unified)
```

Or foreground:

```bash
./run.sh serve              # binds 0.0.0.0:8399, Swagger at /docs
./run.sh serve --port 8399 --host 0.0.0.0
```

---

## Commands

| Command | What it does |
|---|---|
| `./run.sh tui` | Full TUI: search movies + anime, Trending, Anime latest, History, episode picker, quality (1080p/720p/480p), SUB/DUB switch, **d** download, **p** play |
| `./run.sh query "Solo Leveling"` | **Unified search**: MovieBox + Anilab2 concurrently, merged list `[M]` movies / `[A]` anime, pick → VLC |
| `./run.sh query "Solo Leveling" --anime` | Anime only |
| `./run.sh query "Oppenheimer" --movies` | Movies only |
| `./run.sh query "Solo Leveling" 1x1` | Directly resolve season 1 ep 1 |
| `./run.sh download "Solo Leveling" 1x1` | Async download to `downloads/` (httpx streaming, resume via Range, max 3 concurrent) |
| `./run.sh play "breaking bad" 5x1` | One-shot MovieBox: search → stream in mpv/vlc/ffplay |
| `./run.sh dl "peaky blinders" 6x1` | One-shot MovieBox download |
| `./run.sh up / down / status` | Daemon lifecycle (pidfile `data/nightfall.pid`, health probe) |
| `./run.sh serve` | Foreground gateway |
| `./run.sh mode links\|proxy` | Toggle media delivery (per-request `?mode=proxy` still works) |
| `./run.sh secure on\|off\|auto` | Toggle API-key enforcement |
| `./run.sh key create/list/revoke` | Manage `X-API-Key` clients |
| `./run.sh doctor [--live]` | Layered diagnostics |
| `./run.sh heal` | Scan `watch/` for new APK → extract → diff → verify → commit |
| `./run.sh selfheal check` | Test all endpoints (MovieBox + Anilab2 + Kyoto) |
| `./run.sh selfheal fix` | Re-extract headers/secrets from APK/HTML |
| `./run.sh selfheal report` | Print which endpoints are broken and why |
| `./run.sh extract path/to.apk` | Dump secrets/app facts from an APK |

---

## The API — Unified, Home-Network Ready

`nightfall serve` binds on **`0.0.0.0:8399`** — any device on your LAN can use it.

Auth: `X-API-Key: <key>` (auto-enforced once a key exists).  
Public: `/health`, `/docs`, `/openapi.json`, `/anime/ui`.

```
GET  /health                          liveness
GET  /docs                            Swagger UI
GET  /search?q=&page=                 MovieBox search
GET  /titles/{id}                     MovieBox metadata
GET  /titles/{id}/stream?season=&episode=  MovieBox HLS/DASH (+ direct MP4)
GET  /titles/{id}/download?season=&episode= MovieBox direct files
GET  /anime/search?q=&page=           Anilab2 catalog search
GET  /anime/home                      Anilab2 featured + sections
GET  /anime/latest?page=              Anilab2 latest releases
GET  /anime/categories                Anilab2 categories
GET  /anime/post/{post_id}            Anilab2 title detail
GET  /anime/post/{post_id}/episodes   Kyoto: episode list
GET  /anime/post/{post_id}/servers/{ep_id}    Kyoto: SUB/DUB servers
GET  /anime/post/{post_id}/stream/{server_id} Kyoto: regex-scraped .m3u8 (prefers master.m3u8)
GET  /anime/ui                        Web UI (anilab.html in proxy mode)
POST /heal   GET /doctor?live=1  GET /protocol  /keys
```

Phone/TV/browser can use Nightfall as a universal backend:

```bash
curl -H "X-API-Key: $KEY" http://192.168.1.10:8399/anime/search?q=naruto
curl -H "X-API-Key: $KEY" "http://192.168.1.10:8399/titles/590417.../stream?season=1&episode=1"
```

---

## Configuration — One File to Repair

All upstream endpoints, headers, and signing secrets live in **`config.yaml`**:

```yaml
moviebox:
  api_hosts: [api6.aoneroom.com, i-api.aoneroom.com]
  hmac_secret: "..."          # rotates → edit here only
  signing_version: "2"

anilab:
  base_url: https://anilab2.amdapi.click/api
  headers: {os-version: "35", app-id: com.xo.anilab, app-version: "105", os-id: ""}

kyoto:
  base_url: https://app.kyotoplayer.com/api/v4
  headers: {os-version: "35", app-id: com.kyotoplayer, app-version: "126"}

server: {host: "0.0.0.0", port: 8399}
player: {preferred: vlc, fallback_order: [vlc, mpv, ffplay]}
downloads: {directory: downloads}
```

When Kyoto rotates their iframe pattern or Anilab changes `app-version`:

```bash
./run.sh selfheal check   # shows [FAIL] kyoto.resolve_stream → HTTP 403
# edit config.yaml → bump kyoto.app-version, restart
./run.sh down && ./run.sh up
```

No code changes needed.

---

## Downloads

Default target: `nightfall/downloads/<title>/<episode>.mp4` (gitignored).  
Uses `httpx` streaming + async file writes (no full-file RAM buffering), `Range: bytes=N-` resume, max 3 concurrent.

From TUI: press **`d`** on any result → progress bar in status bar.  
From CLI: `./run.sh download "Title" 1x12`.

---

## Home Network

Gateway listens on `0.0.0.0:8399`. To expose beyond localhost:

```bash
# firewall (Ubuntu)
sudo ufw allow 8399/tcp

# find your LAN IP
ip route get 1.1.1.1 | awk '{print $7}'
# phones can now hit http://<LAN_IP>:8399/docs
```

---

## Layout

```
nightfall/
├── run.sh                  ./run.sh [query|tui|serve|up|down|status]
├── nightfall/
│   ├── cli.py              click/argparse CLI (incl. query + download)
│   ├── tui.py              Textual TUI (movies + anime unified, quality + dub switch)
│   ├── config.yaml         ALL upstream endpoints/secrets — one file
│   ├── config.py           loader (deep-merge + env overrides)
│   ├── main.py             FastAPI app (mounts anime router, 0.0.0.0:8399)
│   ├── security.py         API key + rate limit (public: /health /docs /anime/ui)
│   ├── daemon.py           up/down/status (nightfall.pid)
│   ├── downloader.py       async streaming downloader (httpx + Range + semaphore)
│   ├── banner.py           🌙 NIGHTFALL banner
│   ├── upstream/           MovieBox signed client (HMAC-MD5, failover, GW.4410 skew)
│   ├── selfheal/           extractor / doctor / healer (watch/ APK)
│   └── anilab/
│       ├── client.py       AnilabClient (6 methods, httpx AsyncClient + pooled)
│       ├── kyoto.py        KyotoResolver (regex .m3u8 scrape, no Playwright)
│       ├── cache.py        LRU+TTL in-memory (catalog 1h, streams 15m)
│       └── routes.py       FastAPI router /anime/*
├── static/anime.html       anilab.html in Nightfall proxy mode (via :8399)
├── downloads/              gitignored default target
├── logs/                   rotating logs (gitignored)
├── data/                   device.json + api_keys.json (gitignored)
├── protocol/               protocol.yaml snapshots + backups/
└── tests/                  pytest (36 tests)
```

---

## Anime — Why Titles Look Incomplete & Why Anime Streams Are Superior

### Titles: upstream returns only `id`/`poster`
`Anilab2` search/latest (`/anime/search`, `/anime/latest`) intentionally return **minimal** records: `id`, `poster`, `age` — no `title` (verify: `curl -H "X-API-Key: $KEY" :8399/anime/search?q=naruto` → `{"id":...,"poster":...}`). Full metadata lives only at `/anime/post/{id}` (`nightfall/anilab/routes.py:67`).

**Nightfall hydrates:** `nightfall/tui.py:617` (`fetch_anime_latest`, `search_query`) + `nightfall/anilab/cache.py:4` (LRU+TTL 1h) — the TUI initially shows `Anime <id>` placeholders, then background-fetches `GET /anime/post/{id}` for 5 concurrent tasks, updates the row label (`Label.item-title`) in place. You will see titles fill in within 0.5–2s after search/latest. The web UI (`static/anime.html:162` `postCache` + `observeCards`) does identical hydration. CLI `./run.sh query` now hydrates before printing.

If you still see `Anime 12345`, wait for hydration or open detail (`Enter`) → `fetch_anime_details` fetches full `title/score/type/overview` immediately.

### Anime Streams: faster · higher-res · better dub
The **Anime** tab (Kyoto Player pipeline, `nightfall/anilab/kyoto.py:6` `M3U8_RE`, `resolve_stream`) is not a MovieBox re-encode — it is a direct public CDN `HLS .m3u8`:

| Aspect | MovieBox (`/titles/{id}/stream`) | Anime — Kyoto (`/anime/post/{id}/stream/{sid}`) |
|---|---|---|
| **Speed** | Signed upstream + gateway cache (stream TTL 120s), time-limited URLs, cookie-required for some CDNs (`translate.py`, `media_proxy.py`) | **Public CDN, no cookies**, `vlc <url>` or `mpv` straight, no proxy needed, prefetches `master.m3u8` (`kyoto.py:48` `best=master.m3u8`), lower TTFB |
| **Resolution** | Adaptive but capped by what MovieBox transcodes (often 720p/480p, `normalize_streams`) | **Master HLS with all renditions**; `alternates[]` lists every `variant` (`kyoto.py:53` `alternates`), gateway picks `master.m3u8` (1080p where available), TUI shows `🌙 HLS — master.m3u8 (auto)` + variants |
| **Dub / Audio** | Single mux via `dubs` list → switches `subjectId` (re-fetches detail) (`tui.py:773` `dub_*`) | **True server-level SUB/DUB** per episode (`/anime/post/{id}/servers/{ep_id}` → `lang:"sub"|"dub"`, `kyoto.py:31` `get_servers`), TUI shows `🔊 SUB` / `🎙 DUB` groups, switch without reloading title (`tui.py:623` `srv_*` → `resolve_stream`) |

Stay in the **Anime** tab (`a` key or `TabPane("Anime", id="tab-anime")`, `tui.py:493`) for anime-only content to get the superior Kyoto HLS. Use `query --anime` (`cli.py:70`) for CLI.

> **Cloudflare fixed:** Kyoto `play.anidb.app` is Cloudflare managed challenge (403 `cf-mitigated: challenge` with `httpx`). Gateway now uses `curl_cffi` `impersonate="chrome"` (`nightfall/anilab/kyoto.py:15` `_curl_get_json_sync`, `_curl_get_text_sync`) with `Referer: https://play.app/` + `X-Requested-With: PLAY`, bypassing CF server-side — episodes/servers/stream resolve via gateway without browser. If you still see `403` in `logs/server.log`, update `kyoto.app-version` in `config.yaml` or open `GET /anime/ui` (browser warmup iframe) as fallback.

---

## Documentation Coverage

This README now documents **all** user-facing behavior (verified against `nightfall/` code at `file_path:line`):

* Quick Start, Commands table (14 commands, `nightfall/cli.py:67`), API (30 routes, `nightfall/main.py:28`), Configuration (`config.yaml`/`config.py:60`), Downloads (`downloader.py:12`), Home Network, Layout, Source Material Map — **plus** this anime-specific section (titles hydration + stream superiority + Cloudflare caveat).

---

## Source Material Map

| Source | What was ported | Target |
|---|---|---|
| `hacking/anilab_cli.py` | `make_request()`, all `cmd_*` | `nightfall/anilab/client.py` |
| `hacking/anilab.html` | `kyotoRoutes`, `fetchEpisodes`, `fetchServers`, `resolveStream` | `nightfall/anilab/kyoto.py` (no Playwright, regex only) |
| `hacking/anilab.html` | full SPA | `static/anime.html` (proxy mode, `/anime/ui`) |
| `movie-app/wrapper/` | already ported MovieBox gateway | `nightfall/` (unified, 0.0.0.0, downloads/) |

---

## Troubleshooting

*`MBX_API_KEY` env still works* (legacy). Prefer `NIGHTFALL_API_KEY` or `X-API-Key` header.

*VLC not opening?* `vlc <m3u8_url>` works without cookies (public CDN links). For MovieBox streams that need cookies, `mpv` handles `--http-header-fields=Cookie: …` automatically via `./run.sh play`.

*Port busy?* `lsof -i :8399` then `./run.sh down`.

*Anime titles show “Anime 12345” briefly?* See **Titles** above — hydration is asynchronous; press `Enter` on the row to force detail fetch.

---

## License

Private use only. Upstream APIs belong to their respective services.
