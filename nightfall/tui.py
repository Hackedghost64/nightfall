"""MBX Textual Terminal App: Modern, visual interactive movie & series center.

Includes search, trending rankings, watch history, episode selector,
quality chooser, live download manager, and player launch (MPV/VLC).
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Grid, Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    OptionList,
    ProgressBar,
    Rule,
    Static,
    TabbedContent,
    TabPane,
)
from textual.widgets.option_list import Option

from .config import settings

PORT = None


def _base() -> str:
    global PORT
    if PORT is None:
        cfg = settings()
        PORT = int(cfg.get("server.port", 8399))
    return f"http://127.0.0.1:{PORT}"


def _cli_keyfile() -> Path:
    return Path(settings().device_file.parent) / "cli.key"


def _history_file() -> Path:
    return Path(settings().device_file.parent) / "history.json"


def ensure_local_key() -> str:
    import os
    kf = _cli_keyfile()
    if kf.exists():
        raw = kf.read_text().strip()
        if raw:
            return raw
    from .security import ApiKeyStore
    out = ApiKeyStore(kf.parent).create("local-cli")
    kf.write_text(out["plaintext"])
    kf.chmod(0o600)
    return out["plaintext"]


def _api_key() -> str:
    import os
    env = os.environ.get("MBX_API_KEY")
    if env:
        return env
    try:
        return ensure_local_key()
    except Exception:
        return ""


def api(path: str, params: dict | None = None, method: str = "GET",
        body: dict | None = None, key: str | None = None,
        timeout: float = 30.0) -> dict:
    if key is None:
        key = _api_key()
    url = _base() + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("accept", "application/json")
    if body is not None:
        req.add_header("content-type", "application/json")
    if key:
        req.add_header("x-api-key", key)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def api_soft(path: str, params: dict | None = None, **kw) -> dict | None:
    try:
        return api(path, params, **kw)
    except Exception:
        return None


# ---------------------------------------------------------------- Player Launch

def detect_player() -> str | None:
    for p in ("mpv", "vlc", "ffplay"):
        if shutil.which(p):
            return p
    return None


def launch_player(player: str, url: str, title: str, cookie: str | None = None
                  ) -> subprocess.Popen:
    if player == "mpv":
        cmd = [
            "mpv",
            f"--title={title}",
            f"--force-media-title={title}",
            "--profile=gpu-hq",
            "--hwdec=auto-safe",
            "--scale=spline36",
            "--cscale=spline36",
            "--dscale=mitchell",
            "--correct-downscaling=yes",
            "--ytdl-format=bestvideo+bestaudio/best",
            "--hls-bitrate=max",
            "--autofit=100%x100%",
        ]
        if cookie:
            cmd.extend([
                f"--http-header-fields=Cookie: {cookie}",
                f"--demuxer-lavf-o=headers=Cookie: {cookie}\r\n",
            ])
        cmd.append(url)
    elif player == "vlc":
        cmd = [
            "vlc",
            "--play-and-exit",
            f"--meta-title={title}",
            "--http-user-agent=MovieBox/3.0.14",
            "--avcodec-hw=any",
        ]
        if cookie:
            cmd.append(f"--http-cookies={cookie}")
        cmd.append(url)
    else:
        cmd = ["ffplay", "-window_title", title, "-autoexit"]
        if cookie:
            cmd.extend(["-headers", f"Cookie: {cookie}\r\n"])
        cmd.append(url)
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _downloads_dir() -> Path:
    d = settings().get("paths.downloads_dir")
    if d:
        return Path(d).expanduser()
    return settings().logs_dir.parent / "downloads"


def download_file(url: str, dest: Path, on_progress=None) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("content-length") or 0)
            done = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
        tmp.replace(dest)
        return True
    except Exception:
        tmp.unlink(missing_ok=True)
        return False


def ffmpeg_stream_download(manifest_url: str, dest: Path,
                           cookie: str | None = None) -> bool:
    if not shutil.which("ffmpeg"):
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y"]
    if cookie:
        cmd.extend(["-headers", f"Cookie: {cookie}\r\n"])
    cmd.extend(["-i", manifest_url, "-c", "copy", "-bsf:a", "aac_adtstoasc", str(dest)])
    rc = subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return rc == 0 and dest.exists()


def smart_download(res: dict | None, stream: dict | None,
                   title: str, se, ep) -> tuple[bool, Path | None]:
    base = "".join(c for c in str(title or "video")
                   if c.isalnum() or c in " .-_").strip().replace(" ", ".")
    name = f"{base}.S{int(se):02d}E{int(ep):02d}.mp4"
    dest = _downloads_dir() / name

    if res and res.get("signed_url"):
        return download_file(res["signed_url"], dest), dest

    if stream and stream.get("url"):
        ok = ffmpeg_stream_download(stream["url"], dest, stream.get("cookie"))
        if not ok and dest.exists():
            dest.unlink()
        return ok, dest

    return False, None


def parse_sxe(s: str) -> tuple[int, int]:
    m = re.match(r"^(\d+)[xe](\d+)$", s.strip().lower().replace(" ", ""))
    if not m:
        raise ValueError(f"bad season/episode {s!r} (want e.g. 1x1 or 1x01)")
    return int(m.group(1)), int(m.group(2))


def load_history() -> List[Dict[str, Any]]:
    hf = _history_file()
    if hf.exists():
        try:
            return json.loads(hf.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_history_item(item: Dict[str, Any]) -> None:
    hf = _history_file()
    history = load_history()
    history = [h for h in history if str(h.get("id")) != str(item.get("id"))]
    item["watched_at"] = int(time.time())
    history.insert(0, item)
    history = history[:50]
    try:
        hf.parent.mkdir(parents=True, exist_ok=True)
        hf.write_text(json.dumps(history, indent=2), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------- Textual UI

class TitleItemWidget(ListItem):
    """Custom list item widget for movie/show row."""
    def __init__(self, title_data: Dict[str, Any]):
        super().__init__()
        self.title_data = title_data

    def compose(self) -> ComposeResult:
        title = str(self.title_data.get("title") or "Unknown")
        year = str(self.title_data.get("year") or "")
        rating = self.title_data.get("rating")
        stype = self.title_data.get("type")
        type_badge = "🎬 Movie" if stype == 1 else ("📺 Series" if stype == 2 else "📼 Video")

        rating_str = f"★ {rating}" if rating else ""
        year_str = f"({year})" if year else ""

        yield Horizontal(
            Label(f"{title} {year_str}", classes="item-title"),
            Label(rating_str, classes="item-rating"),
            Label(type_badge, classes="item-type"),
            classes="item-row"
        )


class MBXApp(App):
    """🌙 NIGHTFALL · Private Cinema Gateway — Movies + Anime unified."""
    CSS = """
    Screen {
        background: #0f172a;
        color: #e2e8f0;
    }

    Header {
        background: #1e293b;
        color: #38bdf8;
        text-style: bold;
    }

    Footer {
        background: #1e293b;
        color: #94a3b8;
    }

    #status-bar {
        height: 1;
        background: #090d16;
        color: #64748b;
        padding: 0 1;
    }

    #main-container {
        height: 1fr;
    }

    #left-pane {
        width: 44%;
        height: 100%;
        border-right: solid #334155;
        padding: 0 1;
    }

    #search-box {
        margin: 1 0;
        background: #1e293b;
        border: tall #0284c7;
        color: #f8fafc;
    }

    #search-box:focus {
        border: tall #38bdf8;
    }

    #right-pane {
        width: 56%;
        height: 100%;
        padding: 1 2;
    }

    .item-row {
        height: auto;
        padding: 0 1;
    }

    .item-title {
        width: 1fr;
        color: #f1f5f9;
        text-style: bold;
    }

    .item-rating {
        width: 10;
        color: #facc15;
        text-style: bold;
    }

    .item-type {
        width: 12;
        color: #a855f7;
    }

    #detail-title {
        color: #38bdf8;
        text-style: bold;
        margin-bottom: 1;
    }

    #detail-badges {
        height: auto;
        margin-bottom: 1;
    }

    .badge {
        background: #1e293b;
        color: #cbd5e1;
        padding: 0 1;
        margin-right: 1;
        border: round #475569;
    }

    .badge-rating {
        background: #854d0e;
        color: #fef08a;
        text-style: bold;
    }

    .badge-genre {
        background: #1e1b4b;
        color: #c7d2fe;
    }

    .badge-sub {
        background: #064e3b;
        color: #a7f3d0;
    }

    #detail-synopsis {
        height: auto;
        max-height: 6;
        background: #182234;
        color: #94a3b8;
        padding: 1;
        border: round #334155;
        margin-bottom: 1;
    }

    #stream-info-box {
        height: auto;
        background: #0f233a;
        color: #38bdf8;
        padding: 1;
        border: round #0369a1;
        margin-bottom: 1;
    }

    .section-box {
        height: auto;
        margin-top: 1;
        background: #1e293b;
        border: round #475569;
        padding: 0 1;
    }

    .section-title {
        color: #38bdf8;
        text-style: bold;
        margin-top: 1;
    }

    #actions-box {
        height: auto;
        margin-top: 1;
        margin-bottom: 1;
    }

    Button {
        margin-right: 1;
        border: none;
    }

    #btn-play {
        background: #0284c7;
        color: #ffffff;
        text-style: bold;
    }

    #btn-play:hover {
        background: #38bdf8;
        color: #0f172a;
    }

    #btn-dl {
        background: #059669;
        color: #ffffff;
    }

    #btn-dl:hover {
        background: #34d399;
        color: #0f172a;
    }

    #episode-list, #quality-list, #dubs-list {
        height: 5;
    }
    """

    BINDINGS = [
        Binding("/", "focus_search", "Search", show=True),
        Binding("p", "play_title", "Play", show=True),
        Binding("d", "download_title", "Download", show=True),
        Binding("r", "load_trending", "Rankings", show=True),
        Binding("h", "load_history", "History", show=True),
        Binding("a", "load_anime", "Anime", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self):
        super().__init__()
        self.selected_item: Optional[Dict[str, Any]] = None
        self.selected_detail: Optional[Dict[str, Any]] = None
        self.seasons_data: Optional[Dict[str, Any]] = None
        self.all_resources: List[Dict[str, Any]] = []
        self.all_streams: List[Dict[str, Any]] = []
        self.resolved_stream: Optional[Dict[str, Any]] = None
        self.resolved_resource: Optional[Dict[str, Any]] = None
        self.dubs_list: List[Dict[str, Any]] = []
        self.current_se = 0
        self.current_ep = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="status-bar", content="Connecting to local gateway…")
        with Horizontal(id="main-container"):
            with Vertical(id="left-pane"):
                yield Input(placeholder="🔍 Search movies, series, anime… (Press Enter)", id="search-box")
                with TabbedContent(id="tabs"):
                    with TabPane("Results", id="tab-results"):
                        yield ListView(id="results-list")
                    with TabPane("Trending", id="tab-trending"):
                        yield ListView(id="trending-list")
                    with TabPane("Anime", id="tab-anime"):
                        yield ListView(id="anime-list")
                    with TabPane("History", id="tab-history"):
                        yield ListView(id="history-list")
            with VerticalScroll(id="right-pane"):
                yield Static("Select a movie or series from the list", id="detail-title")
                yield Horizontal(id="detail-badges")
                yield Static("", id="detail-synopsis")
                yield Static("", id="stream-info-box")

                with Horizontal(id="actions-box"):
                    yield Button("▶ Watch in HD", id="btn-play")
                    yield Button("⭳ Download", id="btn-dl")
                    yield Button("🗘 Refresh", id="btn-reload")

                with Vertical(id="quality-section", classes="section-box"):
                    yield Label("Stream & Resolution Quality (1080p / 720p / 480p):", classes="section-title")
                    yield OptionList(id="quality-list")

                with Vertical(id="dubs-section", classes="section-box"):
                    yield Label("Audio Dubs & Languages (Select to switch track):", classes="section-title")
                    yield OptionList(id="dubs-list")

                with Vertical(id="episodes-section", classes="section-box"):
                    yield Label("Seasons & Episodes:", classes="section-title")
                    yield OptionList(id="episode-list")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "🌙 NIGHTFALL · Private Cinema Gateway"
        self.update_gateway_status()
        self.load_history_tab()
        self.query_one("#search-box", Input).focus()
        self.fetch_trending()
        # also fetch anime latest in background
        self.fetch_anime_latest()

    @work(exclusive=True)
    async def update_gateway_status(self) -> None:
        bar = self.query_one("#status-bar", Static)
        health = await asyncio.to_thread(api_soft, "/health")
        if health and health.get("ok"):
            state = health.get("wrapper_state", "HEALTHY")
            mode = health.get("mode", "direct")
            bar.update(f"● GATEWAY ONLINE (:8399)  |  State: {state}  |  Mode: {mode}  |  Player: {detect_player() or 'None'}")
        else:
            bar.update("▲ GATEWAY OFFLINE or UNREACHABLE  |  Run 'mbx serve' or check port 8399")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if query:
            self.search_query(query)

    @work(exclusive=True)
    async def search_query(self, query: str) -> None:
        self.query_one("#status-bar", Static).update(f"Searching movies + anime for '{query}'…")
        # concurrent: movie + anime
        def _movie_search():
            return api_soft("/search", {"q": query})
        def _anime_search():
            return api_soft("/anime/search", {"q": query})
        resp, anime_resp = await asyncio.to_thread(lambda: (_movie_search(), _anime_search()))
        results = ((resp or {}).get("normalized") or {}).get("results") or []
        anime_posts = ((anime_resp or {}).get("posts") or [])[:10]
        # render movies in results, anime in anime tab
        self.query_one("#tabs", TabbedContent).active = "tab-results"
        list_view = self.query_one("#results-list", ListView)
        list_view.clear()
        for item in results:
            list_view.append(TitleItemWidget(item))
        # also populate anime list (hydrate titles in background)
        try:
            a_list = self.query_one("#anime-list", ListView)
            a_list.clear()
            for p in anime_posts:
                item = {"id": str(p.get("id")), "title": p.get("title") or f"Anime {p.get('id')}", "year": p.get("premiered") or p.get("age") or "", "rating": p.get("score") or "", "type": 2, "poster": p.get("poster")}
                item["_anime"] = True
                a_list.append(TitleItemWidget(item))
            # hydrate real titles asynchronously
            if anime_posts:
                await self._hydrate_anime_titles(anime_posts, a_list)
        except Exception:
            pass
        total = len(results) + len(anime_posts)
        if total==0:
            self.query_one("#status-bar", Static).update(f"No results found for '{query}'.")
            return
        self.query_one("#status-bar", Static).update(f"Found {len(results)} movies + {len(anime_posts)} anime for '{query}'. [a]=anime tab  |  Anime titles hydrate in background — see Anime tab")
        list_view.index = 0

    @work(exclusive=True)
    async def fetch_trending(self) -> None:
        resp = await asyncio.to_thread(api_soft, "/search/rank")
        items = []
        if resp and isinstance(resp.get("data"), dict):
            for sec in resp["data"].get("list", []):
                for sub in sec.get("subjects", []):
                    items.append(sub)
        if items:
            t_list = self.query_one("#trending-list", ListView)
            t_list.clear()
            from .translate import normalize_title_item
            for item in items[:25]:
                t_list.append(TitleItemWidget(normalize_title_item(item)))

    @work(exclusive=True)
    async def fetch_anime_latest(self) -> None:
        resp = await asyncio.to_thread(api_soft, "/anime/latest", {"page": 1})
        posts = (resp or {}).get("posts") or []
        if posts:
            try:
                a_list = self.query_one("#anime-list", ListView)
                a_list.clear()
                for p in posts[:25]:
                    # upstream search/latest only returns id/poster/age — hydrate title via post
                    item = {"id": str(p.get("id")), "title": p.get("title") or f"Anime {p.get('id')}", "year": p.get("premiered") or p.get("age") or "", "rating": p.get("score") or "", "type": 2, "poster": p.get("poster"), "_anime": True}
                    a_list.append(TitleItemWidget(item))
                # background hydration: replace placeholder titles with real titles from /anime/post/{id}
                await self._hydrate_anime_titles(posts[:25], a_list)
            except Exception:
                pass

    async def _hydrate_anime_titles(self, posts, list_view) -> None:
        """Fetch full titles for anime posts that only have id/poster (upstream limitation)."""
        import asyncio as _aio
        # limit concurrency to avoid rate limits
        sem = _aio.Semaphore(5)
        # collect widgets in order
        widgets = list(list_view.children) if hasattr(list_view, 'children') else []
        # but ListView stores items via query; use index mapping
        async def _fetch_one(idx, p):
            async with sem:
                pid = str(p.get("id"))
                # skip if already has real title
                if p.get("title"):
                    return
                try:
                    # use gateway if available, else direct
                    data = await _aio.to_thread(api_soft, f"/anime/post/{pid}")
                    info = (data or {}).get("data") or {}
                    title = info.get("title")
                    if title:
                        # update backing data
                        try:
                            w = list_view.children[idx] if idx < len(list_view.children) else None
                            if w and hasattr(w, 'title_data'):
                                w.title_data["title"] = title
                                w.title_data["year"] = info.get("premiered") or info.get("age") or w.title_data.get("year")
                                w.title_data["rating"] = info.get("score") or w.title_data.get("rating")
                                # update visible label: TitleItemWidget contains Horizontal > Label.item-title
                                try:
                                    label = w.query_one(".item-title", Label)
                                    label.update(f"{title} ({w.title_data.get('year') or ''})".strip())
                                except Exception:
                                    pass
                        except Exception:
                            pass
                except Exception:
                    pass
        if not widgets:
            # fallback: ListView may use internal list, hydrate sequentially
            for i, p in enumerate(posts):
                await _fetch_one(i, p)
        else:
            await _aio.gather(*[_fetch_one(i, p) for i, p in enumerate(posts)])

    def action_load_anime(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-anime"
        self.fetch_anime_latest()

    def load_history_tab(self) -> None:
        history = load_history()
        h_list = self.query_one("#history-list", ListView)
        h_list.clear()
        for item in history:
            h_list.append(TitleItemWidget(item))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, TitleItemWidget):
            self.selected_item = event.item.title_data
            if self.selected_item.get("_anime"):
                self.fetch_anime_details(str(self.selected_item.get("id")))
            else:
                self.fetch_details(str(self.selected_item.get("id")))

    @work(exclusive=True)
    async def fetch_details(self, subject_id: str) -> None:
        title_box = self.query_one("#detail-title", Static)
        badges_box = self.query_one("#detail-badges", Horizontal)
        synopsis_box = self.query_one("#detail-synopsis", Static)
        stream_box = self.query_one("#stream-info-box", Static)
        ep_list = self.query_one("#episode-list", OptionList)
        dubs_list = self.query_one("#dubs-list", OptionList)
        quality_list = self.query_one("#quality-list", OptionList)

        title_box.update("Fetching metadata and streams…")
        badges_box.remove_children()
        synopsis_box.update("")
        stream_box.update("Analyzing video streams…")
        ep_list.clear_options()
        dubs_list.clear_options()
        quality_list.clear_options()

        detail_resp = await asyncio.to_thread(api_soft, f"/titles/{subject_id}")
        info = (detail_resp or {}).get("data") or {}

        title = info.get("title") or self.selected_item.get("title") or "Title"
        rating = info.get("imdbRatingValue") or self.selected_item.get("rating")
        release = info.get("releaseDate") or self.selected_item.get("year")
        genre = info.get("genre") or "General"
        duration = info.get("duration") or ""
        country = info.get("countryName") or ""
        languages = info.get("language") or ""
        subtitles_str = info.get("subtitles") or ""
        stype = info.get("subjectType") or self.selected_item.get("type") or 1
        synopsis = info.get("description") or "No synopsis available."
        dubs = info.get("dubs") or []

        title_box.update(f"{title}")

        # Badges
        await badges_box.mount(Static(f"🎬 Movie" if stype == 1 else "📺 Series", classes="badge"))
        if rating:
            await badges_box.mount(Static(f"★ {rating} IMDb", classes="badge badge-rating"))
        if release:
            await badges_box.mount(Static(f"📅 {release}", classes="badge"))
        if duration:
            await badges_box.mount(Static(f"⏱ {duration}", classes="badge"))
        if languages:
            await badges_box.mount(Static(f"🗣 {languages[:35]}", classes="badge"))
        if genre:
            await badges_box.mount(Static(f"🏷 {genre}", classes="badge badge-genre"))
        if country:
            await badges_box.mount(Static(f"🌍 {country}", classes="badge"))
        if subtitles_str:
            top_subs = ", ".join(subtitles_str.split(",")[:4])
            await badges_box.mount(Static(f"💬 Subs: {top_subs}", classes="badge badge-sub"))

        synopsis_box.update(synopsis)

        # Audio Dubs list
        self.dubs_list = dubs
        if dubs:
            for idx, d in enumerate(dubs):
                is_orig = " (Original)" if d.get("original") else ""
                dubs_list.add_option(Option(f"🔊 {d.get('lanName', 'Audio')}{is_orig}", id=f"dub_{idx}"))
        else:
            dubs_list.add_option(Option("🔊 Standard / Original Audio", id="dub_0"))

        # Episode Tree
        if stype == 2:  # Series
            self.current_se = 1
            self.current_ep = 1
            seasons_resp = await asyncio.to_thread(api_soft, f"/titles/{subject_id}/seasons")
            self.seasons_data = (seasons_resp or {}).get("data") or {}
            seasons = self.seasons_data.get("seasons") or []
            ep_list.clear_options()
            for s in seasons:
                se_num = s.get("se", 1)
                max_ep = s.get("maxEp", 1)
                for ep in range(1, max_ep + 1):
                    ep_list.add_option(Option(f"Season {se_num} · Episode {ep:02d}", id=f"{se_num}x{ep}"))
        else:
            self.current_se = 0
            self.current_ep = 0
            ep_list.clear_options()
            ep_list.add_option(Option("Full Feature Movie (0x00)", id="0x0"))

        # Resolve streams and qualities
        await self._resolve_streams(subject_id, self.current_se, self.current_ep)

    async def _resolve_streams(self, subject_id: str, se: int, ep: int) -> None:
        stream_box = self.query_one("#stream-info-box", Static)
        quality_list = self.query_one("#quality-list", OptionList)
        stream_box.update(f"Resolving video streams and qualities for S{se}E{ep:02d}…")
        quality_list.clear_options()

        dl_resp = await asyncio.to_thread(api_soft, f"/titles/{subject_id}/download", {"season": se, "episode": ep})
        st_resp = await asyncio.to_thread(api_soft, f"/titles/{subject_id}/stream", {"season": se, "episode": ep})

        self.all_resources = (dl_resp or {}).get("all") or []
        self.resolved_resource = (dl_resp or {}).get("selected")
        self.resolved_stream = (st_resp or {}).get("selected")

        if self.all_resources:
            for idx, r in enumerate(self.all_resources):
                res_str = r.get("resolution", "HD")
                size_str = f"{r.get('size_mb')} MB" if r.get("size_mb") else ""
                codec_str = r.get("codec") or "HEVC/H264"
                quality_list.add_option(Option(f"💎 Direct MP4 · {res_str} ({size_str}) - {codec_str}", id=f"res_{idx}"))

        if self.resolved_stream:
            stream_kind = self.resolved_stream.get("kind", "dash").upper()
            max_res = self.resolved_stream.get("max_resolution", "1080p")
            quality_list.add_option(Option(f"⚡ Adaptive {stream_kind} Stream · {max_res}", id="stream_dash"))

        # Choose highest quality by default
        best_quality_str = "1080p Full HD"
        if self.resolved_resource:
            best_quality_str = f"Direct MP4 ({self.resolved_resource.get('resolution')})"
        elif self.resolved_stream:
            best_quality_str = f"Adaptive {self.resolved_stream.get('kind','').upper()} ({self.resolved_stream.get('max_resolution')})"

        stream_box.update(f"● Active Stream: [bold cyan]{best_quality_str}[/]  |  Highest Bitrate Ready")

    @work(exclusive=True)
    async def resolve_episode_worker(self, subject_id: str, se: int, ep: int) -> None:
        await self._resolve_streams(subject_id, se, ep)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        opt_id = str(event.option_id or "")
        # anime handlers first
        if opt_id.startswith("anime_ep_"):
            ep_id = opt_id.replace("anime_ep_","")
            self._load_anime_episode(ep_id)
            return
        if opt_id.startswith("anime_srv_"):
            srv_id = opt_id.replace("anime_srv_","")
            self._resolve_anime_stream(srv_id)
            return
        if opt_id.startswith("anime_hls_"):
            idx = int(opt_id.split("_")[-1])
            alts = (self.resolved_stream or {}).get("alternates") or []
            if 0 <= idx < len(alts):
                self.resolved_stream["url"] = alts[idx]
                self.query_one("#stream-info-box", Static).update(f"● Selected HLS variant {idx+1}: {alts[idx][:60]}…")
                self.notify(f"Selected HLS variant {idx+1}", severity="information")
            return
        if opt_id.startswith("res_"):
            idx = int(opt_id.split("_")[1])
            if idx < len(self.all_resources):
                self.resolved_resource = self.all_resources[idx]
                res_str = self.resolved_resource.get("resolution", "HD")
                self.query_one("#stream-info-box", Static).update(f"● Selected Quality: [bold green]{res_str} ({self.resolved_resource.get('size_mb')} MB)[/]")
                self.notify(f"Selected {res_str} quality", severity="information")
        elif opt_id == "stream_dash":
            self.resolved_resource = None  # Force stream mode
            self.query_one("#stream-info-box", Static).update("● Selected Quality: [bold green]Adaptive DASH Master Stream[/]")
            self.notify("Selected DASH Master Stream", severity="information")
        elif opt_id.startswith("dub_"):
            idx = int(opt_id.split("_")[1])
            if idx < len(self.dubs_list):
                dub_item = self.dubs_list[idx]
                target_id = str(dub_item.get("subjectId"))
                lan_name = dub_item.get("lanName", "Audio")
                self.notify(f"Switching to {lan_name}…", severity="information")
                self.fetch_details(target_id)
        elif "x" in opt_id:
            se, ep = parse_sxe(opt_id)
            self.current_se = se
            self.current_ep = ep
            if self.selected_item:
                self.resolve_episode_worker(str(self.selected_item["id"]), se, ep)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-play":
            self.action_play_title()
        elif event.button.id == "btn-dl":
            self.action_download_title()
        elif event.button.id == "btn-reload":
            if self.selected_item:
                self.fetch_details(str(self.selected_item["id"]))

    def action_play_title(self) -> None:
        if not self.selected_item:
            self.notify("Please select a movie or show first.", severity="warning")
            return

        player = detect_player()
        if not player:
            self.notify("No media player (MPV/VLC/FFplay) installed!", severity="error")
            return

        # Prefer high-res direct MP4 if selected, else adaptive stream
        url = (self.resolved_resource or {}).get("signed_url") or (self.resolved_stream or {}).get("url")
        if not url:
            self.notify("No playable stream URL available.", severity="error")
            return

        cookie = (self.resolved_stream or {}).get("cookie")
        title = str(self.selected_item.get("title") or "Video")
        if self.current_se or self.current_ep:
            title = f"{title} S{self.current_se:02d}E{self.current_ep:02d}"

        quality_label = (self.resolved_resource or {}).get("resolution") or (self.resolved_stream or {}).get("max_resolution") or "HD"
        title = f"{title} [{quality_label}]"

        save_history_item(self.selected_item)
        self.load_history_tab()

        self.notify(f"Launching {player} in Full HD: {title}…", severity="information")
        launch_player(player, url, title, cookie=cookie)

    @work(exclusive=True)
    async def action_download_title(self) -> None:
        if not self.selected_item:
            self.notify("Select a title first.", severity="warning")
            return

        title = str(self.selected_item.get("title") or "video")
        se, ep = self.current_se, self.current_ep
        self.notify(f"Starting download for {title}…", severity="information")

        ok, dest = await asyncio.to_thread(smart_download, self.resolved_resource,
                                          self.resolved_stream, title, se, ep)
        if ok and dest:
            self.notify(f"Download complete: {dest.name}", severity="information")
        else:
            self.notify(f"Download failed for {title}", severity="error")

    def action_focus_search(self) -> None:
        self.query_one("#search-box", Input).focus()

    def action_load_trending(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-trending"
        self.fetch_trending()

    def action_load_history(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-history"
        self.load_history_tab()

    @work(exclusive=True)
    async def fetch_anime_details(self, post_id: str) -> None:
        title_box = self.query_one("#detail-title", Static)
        badges_box = self.query_one("#detail-badges", Horizontal)
        synopsis_box = self.query_one("#detail-synopsis", Static)
        stream_box = self.query_one("#stream-info-box", Static)
        ep_list = self.query_one("#episode-list", OptionList)
        dubs_list = self.query_one("#dubs-list", OptionList)
        quality_list = self.query_one("#quality-list", OptionList)
        title_box.update("Fetching anime details…")
        badges_box.remove_children()
        synopsis_box.update("")
        stream_box.update("Resolving episodes via Kyoto…")
        ep_list.clear_options(); dubs_list.clear_options(); quality_list.clear_options()
        # fetch post details + episodes concurrently
        post_resp = await asyncio.to_thread(api_soft, f"/anime/post/{post_id}")
        info = (post_resp or {}).get("data") or {}
        title = info.get("title") or self.selected_item.get("title") or f"Anime {post_id}"
        score = info.get("score") or ""
        ptype = info.get("type") or "Anime"
        overview = info.get("overview") or "No synopsis."
        title_box.update(f"{title}")
        await badges_box.mount(Static(f"🌸 {ptype}", classes="badge"))
        if score: await badges_box.mount(Static(f"★ {score}", classes="badge badge-rating"))
        if info.get("age"): await badges_box.mount(Static(f"🔞 {info.get('age')}", classes="badge"))
        if info.get("status"): await badges_box.mount(Static(f"📺 {info.get('status')}", classes="badge"))
        synopsis_box.update(overview)
        # episodes via Kyoto (now via curl_cffi chrome impersonate to bypass CF — nightfall/anilab/kyoto.py:15)
        eps_resp = await asyncio.to_thread(api_soft, f"/anime/post/{post_id}/episodes")
        # api_soft returns None on auth error; check gateway error payload
        err = (eps_resp or {}).get("error") or eps_resp
        eps = (eps_resp or {}).get("episodes") or []
        if not eps:
            # Check if response was CF block or truly empty; show actionable hint
            detail = (err.get("message") if isinstance(err, dict) else "") or ""
            hint = ""
            if "403" in str(detail) or "cf" in str(detail).lower():
                hint = " (Cloudflare challenge — gateway now uses curl_cffi chrome, retry or check config.yaml kyoto.app-version, or use http://127.0.0.1:8399/anime/ui#post/{})".format(post_id)
            stream_box.update(f"No episodes found for this title{hint}. Try related seasons or web UI.")
            return
        self._anime_eps = eps
        self._anime_pid = post_id
        ep_list.clear_options()
        for e in eps:
            ep_list.add_option(Option(f"Ep {e.get('num') or e.get('id')}: {e.get('name') or ''}", id=f"anime_ep_{e.get('id')}"))
        self.current_se = 1; self.current_ep = 1
        # auto load first episode servers
        await self._load_anime_episode_inner(eps[0]["id"])
        stream_box.update(f"● Anime ready — {len(eps)} episodes, pick one to view servers")

    async def _load_anime_episode_inner(self, ep_id: str) -> None:
        dubs_list = self.query_one("#dubs-list", OptionList)
        quality_list = self.query_one("#quality-list", OptionList)
        stream_box = self.query_one("#stream-info-box", Static)
        dubs_list.clear_options(); quality_list.clear_options()
        stream_box.update(f"Loading servers for episode {ep_id}…")
        srvs_resp = await asyncio.to_thread(api_soft, f"/anime/post/{self._anime_pid}/servers/{ep_id}")
        srvs = (srvs_resp or {}).get("servers") or []
        self._anime_srvs = srvs
        self._anime_cur_ep = ep_id
        if not srvs:
            stream_box.update("No servers for this episode.")
            return
        for idx, s in enumerate(srvs):
            lang = s.get("lang","sub")
            badge = "🔊 SUB" if lang=="sub" else "🎙 DUB"
            dubs_list.add_option(Option(f"{badge} — {s.get('name')}", id=f"anime_srv_{s.get('id')}"))
        stream_box.update(f"● Servers loaded: {len(srvs)} (pick SUB/DUB to resolve .m3u8)")
        await self._resolve_anime_stream_inner(srvs[0]["id"])

    async def _resolve_anime_stream_inner(self, srv_id: str) -> None:
        stream_box = self.query_one("#stream-info-box", Static)
        quality_list = self.query_one("#quality-list", OptionList)
        stream_box.update(f"Resolving .m3u8 for server {srv_id}…")
        st = await asyncio.to_thread(api_soft, f"/anime/post/{self._anime_pid}/stream/{srv_id}")
        if not st or not st.get("ok"):
            stream_box.update(f"Stream resolve failed: {st}")
            return
        url = st.get("url")
        alts = st.get("alternates") or [url]
        self.resolved_stream = {"url": url, "alternates": alts, "kind": "hls", "max_resolution":"1080p"}
        self.resolved_resource = None
        quality_list.clear_options()
        for i, u in enumerate(alts):
            label = "master.m3u8 (auto)" if "master" in u else f"variant {i+1}"
            quality_list.add_option(Option(f"🌙 HLS — {label}", id=f"anime_hls_{i}"))
        stream_box.update(f"● HLS ready: {url[:60]}…  ({len(alts)} variant(s))")

    @work(exclusive=True)
    async def _load_anime_episode(self, ep_id: str) -> None:
        await self._load_anime_episode_inner(ep_id)

    @work(exclusive=True)
    async def _resolve_anime_stream(self, srv_id: str) -> None:
        await self._resolve_anime_stream_inner(srv_id)


# ---------------------------------------------------------------- Flow Helpers

def flow_search(key: str) -> None:
    main()


def flow_manage(key: str) -> None:
    main()


def tui() -> int:
    app = MBXApp()
    app.run()
    return 0


def quick(play_mode: bool, key: str) -> None:
    main()


def main() -> int:
    return tui()


if __name__ == "__main__":
    sys.exit(main())
