from nightfall.translate import (
    harvest_urls, normalize_search, normalize_streams, normalize_downloads,
    normalize_subtitles)


def test_harvest_finds_nested_urls_anywhere():
    payload = {
        "data": {
            "playList": [
                {"resolutionName": "720P",
                 "urlList": ["https://cdn.example.com/v/720/file.mp4?tok=1"],
                 "hls": "https://cdn.example.com/v/master.m3u8"},
            ],
            "posterImage": "https://img.example.com/x.jpg",   # no url-key, no media ext -> skipped
            "posterUrl": "https://img.example.com/y.jpg",     # url-ish key -> kept
        }
    }
    urls = harvest_urls(payload)
    got = {u["url"] for u in urls}
    assert "https://cdn.example.com/v/720/file.mp4?tok=1" in got
    assert "https://cdn.example.com/v/master.m3u8" in got
    assert "https://img.example.com/y.jpg" in got
    assert "https://img.example.com/x.jpg" not in got


def test_normalize_streams_classifies_kinds():
    payload = {"list": [{"urls": ["https://x.aoneroom.com/a.m3u8",
                                  "https://x.aoneroom.com/b.mp4"]}]}
    streams = normalize_streams(payload)
    kinds = {(s["kind"], s["url"].split("/")[-1]) for s in streams}
    assert ("hls", "a.m3u8") in kinds
    assert ("progressive", "b.mp4") in kinds


def test_normalize_streams_reads_upstream_schema():
    """Verified play-info shape: {streams:[{format,url,resolutions,size}]}."""
    payload = {"streams": [
        {"format": "HLS", "url": "https://cdn/e1-480/local.m3u8",
         "resolutions": "480", "size": "223316533"},
        {"format": "DASH", "url": "https://cdn/e1_1080/index.mpd",
         "resolutions": "1080,720,480", "size": "989000000"},
    ]}
    out = normalize_streams(payload)
    assert [o["kind"] for o in out] == ["hls", "dash"]
    assert out[0]["max_resolution"] == "480p"
    assert out[0]["size_mb"] == round(223316533 / 1e6, 1)
    assert out[1]["resolutions"] == ["1080", "720", "480"]
    assert out[1]["max_resolution"] == "1080p"


def test_normalize_streams_falls_back_to_harvester():
    payload = {"weird": {"nested": "https://c/x.mpd"}}
    out = normalize_streams(payload)
    assert out and out[0]["kind"] == "dash"


def test_search_normalizer_picks_subject_list():
    payload = {"code": 0, "data": {"subjects": [
        {"subjectId": "123", "subjectName": "Foo Movie",
         "cover": "https://i/f.jpg", "score": "7.5"},
        {"subjectId": "456", "title": "Bar"},
    ]}}
    norm = normalize_search(payload)
    assert norm["count"] == 2
    ids = [r["id"] for r in norm["results"]]
    titles = [r["title"] for r in norm["results"]]
    assert "123" in ids and ("Foo Movie" in titles or "Bar" in titles)


def test_download_normalizer_collects_episodes_raw():
    payload = {"data": {"episodes": [{"ep": 1, "url": "https://d/1.mp4"},
                                     {"ep": 2, "url": "https://d/2.mp4"}]}}
    out = normalize_downloads(payload)
    assert len(out["downloads"]) == 2
    assert {e["episode"] for e in out["episodes_raw"]} == {1, 2}


def test_subtitle_normalizer():
    payload = {"data": ["ignored", {"lang": "English", "url": "https://s/en.vtt"}]}
    subs = normalize_subtitles(payload)
    assert subs and subs[0]["language"] == "English"
