"""protocol.yaml store: load, hot-reload, diff, patch.

The protocol file is the single source of truth for every volatile upstream
fact (secrets, hosts, endpoints, signing profile). Fixing the wrapper after
an app update means editing this file - or letting selfheal.healer do it.
"""
from __future__ import annotations

import copy
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

REQUIRED_TOP = ("app", "secrets", "hosts", "signature", "endpoints")
REQUIRED_SECRETS = ("gateway_secret_online",)


class ProtocolError(RuntimeError):
    pass


def load_protocol(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    validate_protocol(data)
    return data


def validate_protocol(data: Dict[str, Any]) -> None:
    missing = [k for k in REQUIRED_TOP if k not in data]
    if missing:
        raise ProtocolError(f"protocol.yaml missing sections: {missing}")
    for key in REQUIRED_SECRETS:
        if not data["secrets"].get(key):
            raise ProtocolError(f"protocol.yaml secrets.{key} is empty/missing")


def mask_secrets(data: Dict[str, Any], keep: int = 6) -> Dict[str, Any]:
    masked = copy.deepcopy(data)
    secrets = masked.get("secrets", {})
    for k, v in secrets.items():
        if isinstance(v, str) and len(v) > keep:
            secrets[k] = v[:keep] + "…(" + str(len(v)) + " chars)"
    return masked


class ProtocolDiff:
    def __init__(self) -> None:
        self.changed_secrets: Dict[str, Dict[str, Optional[str]]] = {}
        self.changed_app: Dict[str, Dict[str, Any]] = {}
        self.missing_required: List[str] = []
        self.added_manifest_keys: List[str] = []

    @property
    def healable(self) -> bool:
        """Value-only drift (rotated secrets / version bump) -> auto-fixable."""
        return not self.missing_required

    @property
    def has_changes(self) -> bool:
        return bool(self.changed_secrets or self.changed_app or self.missing_required)

    def summary(self) -> Dict[str, Any]:
        return {
            "healable": self.healable,
            "changed_secrets": {k: {"old": _tail(v.get("old")), "new": _tail(v.get("new"))}
                                for k, v in self.changed_secrets.items()},
            "changed_app": self.changed_app,
            "missing_required_keys": self.missing_required,
            "added_manifest_keys": self.added_manifest_keys,
        }


def _tail(v: Any) -> Optional[str]:
    return None if v is None else str(v)[-4:]


def diff_extracted(current: Dict[str, Any], extracted: Dict[str, Any],
                   required: List[str]) -> ProtocolDiff:
    """Compare freshly-extracted APK facts against the live protocol dict."""
    d = ProtocolDiff()
    new_secrets: Dict[str, str] = {
        k: v for k, v in (extracted.get("secrets") or {}).items() if v
    }
    cur_secrets: Dict[str, Any] = current.get("secrets", {})
    for k, old_v in cur_secrets.items():
        new_v = new_secrets.get(k)
        if new_v and new_v != old_v:
            d.changed_secrets[k] = {"old": old_v, "new": new_v}
    for k, new_v in new_secrets.items():
        if k not in cur_secrets:
            d.added_manifest_keys.append(f"secrets.{k}")

    for req in required:
        if not new_secrets.get(req):
            d.missing_required.append(req)

    ex_app = extracted.get("app") or {}
    for field in ("package", "version_name", "version_code"):
        nv = ex_app.get(field)
        if nv and str(nv) != str(current.get("app", {}).get(field, "")):
            d.changed_app[f"app.{field}"] = {"old": current.get("app", {}).get(field), "new": nv}
    return d


def apply_diff(current: Dict[str, Any], diff: ProtocolDiff,
               source_apk: str) -> Dict[str, Any]:
    """Return a NEW protocol dict with value-only changes applied."""
    out = copy.deepcopy(current)
    for k, change in diff.changed_secrets.items():
        out.setdefault("secrets", {})[k] = change["new"]
    for field_path, change in diff.changed_app.items():
        section, field = field_path.split(".", 1)
        out.setdefault(section, {})[field] = change["new"]
    out["version"] = int(out.get("version", 1)) + 1
    out["updated"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    out["source_apk"] = source_apk
    return out


class ProtocolStore:
    """Thread-safe holder; swap_protocol() hot-reloads without restart."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data = load_protocol(path)

    @property
    def data(self) -> Dict[str, Any]:
        with self._lock:
            # return deep copy to prevent caller mutation without lock and stale refs
            return copy.deepcopy(self._data)

    def required_keys(self) -> List[str]:
        return list(self.data.get("required_manifest_keys", REQUIRED_SECRETS))

    def swap(self, new_data: Dict[str, Any]) -> None:
        validate_protocol(new_data)
        with self._lock:
            self._data = new_data

    def save(self, new_data: Dict[str, Any], backup_dir: Path | None = None) -> None:
        with self._lock:
            if backup_dir is not None:
                backup_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                backup = backup_dir / f"protocol-{stamp}-v{self._data.get('version', '?')}.yaml"
                backup.write_text(yaml.safe_dump(self._data, sort_keys=False), encoding="utf-8")
            tmp = self.path.with_suffix(".yaml.tmp")
            tmp.write_text(yaml.safe_dump(new_data, sort_keys=False), encoding="utf-8")
            tmp.replace(self.path)
            self._data = new_data
