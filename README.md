# 🌙 Nightfall — Private Cinema Gateway (MovieBox)

> **MovieBox-only gateway on `:8399`.** Anime (Anilab2/Kyoto) separated to `../anime-app` after TUI `DuplicateID` crash.  
> Fast, no lag, downloads default to `nightfall/downloads/`.  
> Repair when upstream rotates secrets: edit `config.yaml` — nothing else.  
> `movie-app/wrapper` stays untouched as the knowledge base.

---

## Quick Start

```bash
git clone <this-repo> nightfall
cd nightfall
./run.sh setup      # env check → start gateway → provision device → create API key
./run.sh tui        # MovieBox TUI (search, Trending, History)
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
| `./run.sh tui` | MovieBox TUI: search, Trending, History, episode picker, quality (1080p/720p/480p), `d` download, `p` play |
| `./run.sh query "breaking bad"` | MovieBox search, pick → VLC/mpv (`query "Title" 1x1` for S1E1) |
| `./run.sh download "Title" 1x1` | Async download to `downloads/` (httpx streaming, resume via Range, max 3 concurrent) |
| `./run.sh play "breaking bad" 5x1` | One-shot MovieBox: search → stream in mpv/vlc/ffplay |
| `./run.sh dl "peaky blinders" 6x1` | One-shot MovieBox download |
| `./run.sh up / down / status` | Daemon lifecycle (pidfile `data/nightfall.pid`, health probe) |
| `./run.sh serve` | Foreground gateway |
| `./run.sh mode links\|proxy` | Toggle media delivery (per-request `?mode=proxy` still works) |
| `./run.sh secure on\|off\|auto` | Toggle API-key enforcement |
| `./run.sh key create/list/revoke` | Manage `X-API-Key` clients |
| `./run.sh doctor [--live]` | Layered diagnostics (signing, DNS/TLS, probe) |
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
├── run.sh                  ./run.sh [query|tui|serve|up|down|status]
├── nightfall/
│   ├── cli.py              MovieBox CLI (query, play/dl, keys, doctor/heal)
│   ├── tui.py              MovieBox TUI (no anime DuplicateID)
│   ├── config.yaml         MovieBox-only — one file
│   ├── config.py           loader (deep-merge + env overrides)
│   ├── main.py             MovieBox FastAPI (0.0.0.0:8399)
│   ├── security.py         API key + rate limit (public: /health /docs)
│   ├── daemon.py           up/down/status (nightfall.pid)
│   ├── downloader.py       async streaming (httpx + Range + semaphore)
│   ├── banner.py           🌙 NIGHTFALL banner
│   ├── upstream/           MovieBox signed client (HMAC-MD5, failover, GW.4410 skew)
│   └── selfheal/           extractor / doctor / healer (watch/ APK)
├── downloads/              gitignored
├── logs/                   rotating logs
├── data/                   device.json + api_keys.json (gitignored)
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

*Port busy?* `lsof -i :8399` then `./run.sh down`.

*Anime previously showed “Anime 12345” or “No episodes found”?* See `../anime-app/README.md` — upstream `search/latest` returns only `id/poster`, hydrates via `/anime/post/{id}`; now fixed with `curl_cffi`.

---

## License

Private use only. Upstream APIs belong to their respective services.
