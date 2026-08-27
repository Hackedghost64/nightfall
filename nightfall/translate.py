"""Response normalizers.

Upstream BFF response schemas are not fully known without live captures, so
normalizers are deliberately defensive:

- best-effort field mapping for common shapes
- a generic recursive URL harvester for stream/download discovery that works
  even if the JSON shape drifts between app versions
- `debug_raw=true` on any route returns the untouched upstream payload

When you capture real responses (see logs/upstream.log), tighten these
mappings and add golden fixtures under tests/fixtures/.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple

VIDEOISH = re.compile(r"\.(mp4|m3u8|mpd|ts)(\?|$)", re.IGNORECASE)
URL_KEYS = re.compile(r"url|uri|link|src", re.IGNORECASE)

_ID_KEYS = ["id", "subjectId", "subject_id", "sid"]
_TITLE_KEYS = ["title", "name", "subjectName", "subject_name"]
_POSTER_KEYS = ["cover", "poster", "imageUrl", "verticalUrl", "image", "coverUrl", "thumbnail"]
_YEAR_KEYS = ["year", "releaseYear", "release_year", "releaseDate", "release_date"]
_RATING_KEYS = ["score", "rating", "doubanScore", "imdbScore", "imdbRatingValue"]
_TYPE_KEYS = ["subjectType", "subject_type", "type"]
_DESC_KEYS = ["description", "introduce", "synopsis", "desc"]


def _first(d: Dict[str, Any], keys: Iterable[str], default=None):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return default


def normalize_title_item(item: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {"value": item}
    poster_raw = _first(item, _POSTER_KEYS)
    if isinstance(poster_raw, dict):
        poster = poster_raw.get("url") or poster_raw.get("imageUrl") or str(poster_raw)
    else:
        poster = poster_raw

    year_raw = _first(item, _YEAR_KEYS)
    year = str(year_raw)[:4] if year_raw else None

    rating_raw = _first(item, _RATING_KEYS)
    rating = str(rating_raw) if rating_raw is not None else None

    out = {
        "id": str(_first(item, _ID_KEYS, "")),
        "title": _first(item, _TITLE_KEYS),
        "poster": poster,
        "year": year,
        "rating": rating,
        "type": _first(item, _TYPE_KEYS),
        "description": _first(item, _DESC_KEYS),
    }
    extras = {k: v for k, v in item.items()
              if k not in set(_ID_KEYS + _TITLE_KEYS + _POSTER_KEYS + _DESC_KEYS)}
    if extras:
        out["extra"] = extras
    return out


def find_lists(data: Any) -> List[List[Dict[str, Any]]]:
    """Recursively find lists-of-dicts that look like result collections."""
    found: List[List[Dict[str, Any]]] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            dicts = [x for x in node if isinstance(x, dict)]
            if dicts and len(dicts) >= max(1, len(node) // 2):
                found.append(dicts)
            for x in node[:50]:
                walk(x)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)

    walk(data)
    return found


def normalize_search(payload: Any) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    seen_ids = set()

    # If top-level contains 'data', unwrap
    data_node = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(data_node, dict) and "items" in data_node and isinstance(data_node["items"], list):
        for section in data_node["items"]:
            if isinstance(section, dict) and "subjects" in section and isinstance(section["subjects"], list):
                for subj in section["subjects"]:
                    norm = normalize_title_item(subj)
                    sid = norm.get("id")
                    if sid and sid not in seen_ids:
                        seen_ids.add(sid)
                        items.append(norm)

    if not items:
        lists = find_lists(payload)
        for lst in lists:
            scored = [normalize_title_item(i) for i in lst]
            if scored and (scored[0].get("title") or scored[0].get("id")):
                items = scored
                break

    total = None
    if isinstance(payload, dict):
        for k in ("total", "totalCount", "count"):
            if isinstance(payload.get(k), int):
                total = payload[k]
                break
    return {"results": items, "count": len(items), "total": total}


def harvest_urls(node: Any, parent_key: str = "",
                 require_media_ext: bool = False) -> List[Dict[str, str]]:
    """Walk arbitrary JSON and pull out URL-ish strings with their key paths."""
    hits: List[Dict[str, str]] = []

    def walk(n: Any, path: str) -> None:
        if isinstance(n, dict):
            for k, v in n.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(n, list):
            for i, v in enumerate(n[:200]):
                walk(v, f"{path}[{i}]")
        elif isinstance(n, str) and n.startswith(("http://", "https://")):
            key_hit = bool(URL_KEYS.search(path.rsplit(".", 1)[-1]))
            media_hit = bool(VIDEOISH.search(n))
            if require_media_ext and not media_hit:
                return
            if key_hit or media_hit:
                hits.append({"key_path": path, "url": n})

    walk(node, parent_key)
    seen, out = set(), []
    for h in hits:
        if h["url"] not in seen:
            seen.add(h["url"])
            out.append(h)
    return out


def guess_resolution(url: str, context: Dict[str, Any]) -> str:
    blob = url.lower() + json_blob(context)
    for tag in ("1080", "720", "480", "360", "240"):
        if tag in blob:
            return f"{tag}p"
    m = re.search(r"(\d{3,4})p", blob)
    return f"{m.group(1)}p" if m else "unknown"


def json_blob(o: Any) -> str:
    import json as _j
    try:
        return _j.dumps(o).lower()
    except Exception:
        return ""


def _clean_cookie(raw) -> str | None:
    """signCookie arrives as 'K1=V1;K2=V2;K3=V3;' (trailing ';')."""
    if not raw:
        return None
    parts = [p.strip() for p in str(raw).split(";") if p.strip()]
    return "; ".join(parts) if parts else None


def normalize_streams(play_payload: Any) -> List[Dict[str, Any]]:
    """Map upstream play-info `streams[]` (verified schema):
    {format: HLS|DASH, url, resolutions: "1080,720,480", size, duration, ...}.
    Falls back to the generic harvester if the shape drifts."""
    out: List[Dict[str, Any]] = []
    seen = set()
    payload = play_payload.get("data") if (isinstance(play_payload, dict) and "data" in play_payload and isinstance(play_payload["data"], dict)) else play_payload
    raw_streams = payload.get("streams") if isinstance(payload, dict) else None
    for s in raw_streams or []:
        url = s.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        fmt = str(s.get("format") or "").upper()
        res_list = [r.strip() for r in str(s.get("resolutions") or "").split(",")
                    if r.strip().isdigit()]
        try:
            size_mb = round(int(s.get("size") or 0) / 1e6, 1)
        except (TypeError, ValueError):
            size_mb = None
        out.append({
            "url": url,
            "kind": "dash" if fmt == "DASH" else ("hls" if fmt == "HLS" else fmt.lower()),
            "resolutions": res_list,
            "max_resolution": f"{max(map(int, res_list))}p" if res_list else (f"{s.get('resolutions')}p" if s.get("resolutions") else None),
            "size_mb": size_mb,
            "duration_seconds": s.get("duration"),
            # CloudFront signed cookies -> required as Cookie header on CDN requests
            "cookie": _clean_cookie(s.get("signCookie")),
            "episode_title": s.get("title"),
            "source_key": "streams[]",
        })
    if not out:
        for h in harvest_urls(payload, require_media_ext=False):
            out.append({
                "url": h["url"],
                "kind": "hls" if ".m3u8" in h["url"].lower()
                        else ("dash" if ".mpd" in h["url"].lower() else "progressive"),
                "resolutions": [],
                "max_resolution": guess_resolution(h["url"], payload),
                "size_mb": None,
                "duration_seconds": None,
                "source_key": h["key_path"],
            })
    return out


def normalize_downloads(resource_payload: Any) -> Dict[str, Any]:
    payload = resource_payload.get("data") if (isinstance(resource_payload, dict) and "data" in resource_payload and isinstance(resource_payload["data"], dict)) else resource_payload
    urls = harvest_urls(payload, require_media_ext=False)
    downloads = [{
        "url": h["url"],
        "source_key": h["key_path"],
        "resolution": guess_resolution(h["url"], payload),
    } for h in urls]
    episodes_hint = []
    for lst in find_lists(payload):
        for item in lst:
            ep = _first(item, ["episode", "ep", "epNum", "number"])
            if ep is not None and len(episodes_hint) < 500:
                episodes_hint.append({"episode": ep,
                                      **{k: v for k, v in item.items()
                                         if isinstance(v, (str, int))}})
    return {"downloads": downloads, "episodes_raw": episodes_hint}


def normalize_subtitles(captions_payload: Any) -> List[Dict[str, Any]]:
    payload = captions_payload.get("data") if (isinstance(captions_payload, dict) and "data" in captions_payload) else captions_payload
    subs = []
    for lst in find_lists(payload):
        for item in lst:
            lang = _first(item, ["language", "lang", "languageName", "name"])
            url = _first(item, ["url", "fileUrl", "subtitleUrl", "path"])
            if url and not url.endswith((".mp4", ".m3u8", ".mpd", ".ts")):
                subs.append({"language": lang, "url": url})
    return subs
