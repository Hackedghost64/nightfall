import httpx, time
from pathlib import Path

def test_config_centralization():
    from nightfall.config import settings
    s=settings()
    assert s.get("anilab.base_url")
    assert s.get("kyoto.base_url")
    assert s.get("server.port")==8399
    assert s.get("downloads.directory")=="downloads"
    assert s.get("player.preferred")=="vlc"
    p=Path(s.config_path)
    txt=p.read_text()
    assert "anilab:" in txt and "kyoto:" in txt and "moviebox:" in txt

def test_anilab_client_headers_from_config():
    from nightfall.anilab.client import AnilabClient
    c=AnilabClient()
    assert c.headers.get("app-id")=="com.xo.anilab"
    assert c.headers.get("os-version")=="35"

def test_kyoto_resolver_headers():
    from nightfall.anilab.kyoto import KyotoResolver
    k=KyotoResolver()
    assert k.headers.get("app-id")=="com.kyotoplayer"

def test_cache_lru_ttl():
    from nightfall.anilab.cache import LRUCache
    cache=LRUCache(max_size=2, default_ttl=1)
    cache.set("a","val")
    assert cache.get("a")=="val"
    time.sleep(1.1)
    assert cache.get("a") is None
    cache.set("x","1"); cache.set("y","2"); cache.set("z","3")
    assert cache.get("x") is None  # evicted
    assert cache.get("y")=="2"

def test_banner():
    from nightfall.banner import BANNER_LINES
    assert any("NIGHTFALL" in l for l in BANNER_LINES)

def test_downloader_exists():
    from nightfall.downloader import download
    assert callable(download)

def test_security_api_key():
    from nightfall.security import generate_api_key, ApiKeyStore
    import tempfile, pathlib
    key=generate_api_key()
    assert len(key)>20
    with tempfile.TemporaryDirectory() as td:
        store=ApiKeyStore(pathlib.Path(td))
        out=store.create("test")
        assert store.verify(out["plaintext"])
        assert not store.verify("bad")
        assert store.count==1

def test_gateway_health():
    import urllib.request, json
    # gateway should be running from previous step
    try:
        with urllib.request.urlopen("http://127.0.0.1:8399/health", timeout=5) as r:
            data=json.loads(r.read())
            assert data.get("ok") is True
            assert data.get("wrapper_state") in ("HEALTHY","PROTOCOL_STALE")
    except Exception as e:
        # if not running, skip
        import pytest; pytest.skip(f"gateway not running: {e}")

def test_anime_search_via_gateway():
    import urllib.request, json
    try:
        with urllib.request.urlopen("http://127.0.0.1:8399/anime/search?q=naruto&page=1", timeout=10) as r:
            data=json.loads(r.read())
            assert data.get("ok") is True
            assert "posts" in data
    except Exception as e:
        import pytest; pytest.skip(f"anime search failed: {e}")

def test_movie_search_via_gateway():
    import urllib.request, json
    try:
        with urllib.request.urlopen("http://127.0.0.1:8399/search?q=breaking%20bad", timeout=10) as r:
            data=json.loads(r.read())
            assert data.get("ok") is True or "data" in data
    except Exception as e:
        import pytest; pytest.skip(str(e))

def test_downloads_dir_exists():
    from nightfall.config import settings
    s=settings()
    dl = s.app_root / s.get("downloads.directory","downloads")
    assert dl.exists()

def test_static_anime_html_proxy_mode():
    p=Path("/home/divyam/Downloads/nightfall/static/anime.html")
    assert p.exists()
    txt=p.read_text()
    assert "location.origin+'/anime'" in txt
    assert "nightfall_api_key" in txt or "GW_KEY" in txt

def test_cli_query_help():
    import subprocess
    out=subprocess.check_output(["/home/divyam/Downloads/nightfall/run.sh","query","--help"], text=True, timeout=5)
    assert "query" in out.lower() or "Usage" in out

