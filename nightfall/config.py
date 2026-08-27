"""Runtime settings — centralized config.yaml (FINAL plan).

App root resolution order:
  1. $NIGHTFALL_HOME / $MBX_HOME
  2. source checkout (config.yaml next to package parent)
  3. XDG state home ~/.local/share/nightfall  (bootstrapped from packaged template)
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict

import yaml

PACKAGE_ROOT = Path(__file__).resolve().parent.parent  # nightfall/ in a checkout

def _bootstrap_app_root(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    pkg = Path(__file__).resolve().parent
    if not (root / "protocol" / "protocol.yaml").exists():
        (root / "protocol").mkdir(parents=True, exist_ok=True)
        src = pkg / "protocol_default.yaml"
        if src.exists():
            shutil.copy(src, root / "protocol" / "protocol.yaml")
    if not (root / "config.yaml").exists():
        src = pkg / "config_default.yaml"
        if src.exists():
            shutil.copy(src, root / "config.yaml")
        else:
            (root / "config.yaml").write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")
    return root


def _app_root() -> Path:
    for env_key in ("NIGHTFALL_HOME", "MBX_HOME"):
        env = os.environ.get(env_key)
        if env:
            return _bootstrap_app_root(Path(env).expanduser().resolve())
    if (PACKAGE_ROOT / "config.yaml").exists():
        return PACKAGE_ROOT  # source checkout
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "share")
    return _bootstrap_app_root(Path(base) / "nightfall")


DEFAULT_CONFIG_TEXT = """# 🌙 NIGHTFALL — MovieBox Gateway (anime separated to ../anime-app)
moviebox:
  api_hosts:
    - api6.aoneroom.com
    - i-api.aoneroom.com
  hmac_secret: ""
  signing_version: "2"
  user_agent: "MovieBox/3.0.14"

server:
  host: "0.0.0.0"
  port: 8399
  api_key_file: "data/api.key"

player:
  preferred: "vlc"
  fallback_order: ["vlc", "mpv", "ffplay"]

downloads:
  directory: "downloads"
  filename_template: "{title}/{season}x{episode}_{quality}.mp4"

mode: links
region: US

paths:
  protocol_file: protocol/protocol.yaml
  watch_dir: watch
  device_file: data/device.json
  logs_dir: logs
  backups_dir: protocol/backups
  downloads_dir: downloads

cache_ttl:
  metadata: 3600
  search: 600
  stream: 120

# Distributed caching: memory | file | redis
cache:
  backend: memory          # memory | file | redis
  distributed: false       # true = share across processes / hosts via file or redis
  redis_url: "redis://127.0.0.1:6379/0"
  file_dir: "data/cache"

# Dynamic header & fingerprint spoofing
fingerprint:
  enabled: true
  rotation: per_request     # per_request | per_session | timed
  rotation_interval_seconds: 300
  spoof_headers: true
  # profiles are brand/model/os_version pools — random per rotation
  profiles:
    - {brand: "Google", model: "Pixel 8 Pro", os_version: "14", lang: "en", timezone: "America/New_York"}
    - {brand: "Google", model: "Pixel 7", os_version: "14", lang: "en", timezone: "America/Los_Angeles"}
    - {brand: "Samsung", model: "SM-S928B", os_version: "14", lang: "en", timezone: "America/New_York"}
    - {brand: "Xiaomi", model: "2304FPN6DC", os_version: "13", lang: "en", timezone: "Europe/Berlin"}
    - {brand: "OnePlus", model: "CPH2609", os_version: "14", lang: "en", timezone: "Asia/Kolkata"}

rate_limit_per_minute: 60
upstream_timeout_seconds: 15
log_raw_traffic: true

selfheal:
  auto_scan_on_start: true
  auth_fail_threshold: 3
  verify_probe_on_heal: true
  offline: false

security:
  require_api_key: auto

media_allow_host_suffixes: [aoneroom.com, aliyuncs.com, akamaized.net, cloudcdn.net, hakunaymatata.com]
"""

DEFAULTS: Dict[str, Any] = {
    "moviebox": {
        "api_hosts": ["api6.aoneroom.com", "i-api.aoneroom.com"],
        "hmac_secret": "",
        "signing_version": "2",
        "user_agent": "MovieBox/3.0.14",
    },
    "server": {"host": "0.0.0.0", "port": 8399, "api_key_file": "data/api.key"},
    "player": {"preferred": "vlc", "fallback_order": ["vlc", "mpv", "ffplay"]},
    "downloads": {"directory": "downloads", "filename_template": "{title}/{season}x{episode}_{quality}.mp4"},
    "mode": "links",
    "region": "US",
    "paths": {
        "protocol_file": "protocol/protocol.yaml",
        "watch_dir": "watch",
        "device_file": "data/device.json",
        "logs_dir": "logs",
        "backups_dir": "protocol/backups",
        "downloads_dir": "downloads",
    },
    "cache_ttl": {"metadata": 3600, "search": 600, "stream": 120},
    "cache": {"backend": "memory", "distributed": False, "redis_url": "redis://127.0.0.1:6379/0", "file_dir": "data/cache"},
    "fingerprint": {
        "enabled": True,
        "rotation": "per_request",
        "rotation_interval_seconds": 300,
        "spoof_headers": True,
        "profiles": [
            {"brand": "Google", "model": "Pixel 8 Pro", "os_version": "14", "lang": "en", "timezone": "America/New_York"},
            {"brand": "Google", "model": "Pixel 7", "os_version": "14", "lang": "en", "timezone": "America/Los_Angeles"},
            {"brand": "Samsung", "model": "SM-S928B", "os_version": "14", "lang": "en", "timezone": "America/New_York"},
            {"brand": "Xiaomi", "model": "2304FPN6DC", "os_version": "13", "lang": "en", "timezone": "Europe/Berlin"},
            {"brand": "OnePlus", "model": "CPH2609", "os_version": "14", "lang": "en", "timezone": "Asia/Kolkata"},
        ],
    },
    "rate_limit_per_minute": 60,
    "upstream_timeout_seconds": 15,
    "log_raw_traffic": True,
    "selfheal": {"auto_scan_on_start": True, "auth_fail_threshold": 3, "verify_probe_on_heal": True, "offline": False},
    "security": {"require_api_key": "auto"},
    "media_allow_host_suffixes": ["aoneroom.com", "aliyuncs.com", "akamaized.net", "cloudcdn.net", "hakunaymatata.com"],
}

def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    def __init__(self) -> None:
        self.app_root = _app_root()
        self.config_path = self.app_root / "config.yaml"
        data: Dict[str, Any] = {}
        if self.config_path.exists():
            with open(self.config_path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        merged = _deep_merge(DEFAULTS, data)
        for env_key, env_val in os.environ.items():
            if env_key.startswith("NIGHTFALL_") or env_key.startswith("MBW_"):
                # NIGHTFALL_SERVER__PORT=8399  -> nested
                raw = env_key.split("_", 1)[1]
                path = [x for x in raw.lower().split("__") if x]
                node = merged
                for p in path[:-1]:
                    node = node.setdefault(p, {})
                node[path[-1]] = env_val
        self._d = merged

    def p(self, rel: str) -> Path:
        return (self.app_root / self._d["paths"][rel]).resolve()

    @property
    def protocol_file(self) -> Path:
        return self.p("protocol_file")

    @property
    def watch_dir(self) -> Path:
        return self.p("watch_dir")

    @property
    def device_file(self) -> Path:
        return self.p("device_file")

    @property
    def backups_dir(self) -> Path:
        return self.p("backups_dir")

    @property
    def logs_dir(self) -> Path:
        return self.p("logs_dir")

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._d
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

_settings: Settings | None = None

def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

def reload_settings() -> Settings:
    global _settings
    _settings = Settings()
    return _settings
