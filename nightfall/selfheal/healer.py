"""The healer: watch-folder scan -> extract -> diff -> verify -> commit.

Flow (POST /heal or auto-scan on startup):
 1. find newest *.apk in watch/
 2. extract secrets/app facts from its manifest
 3. diff against live protocol.yaml
    - no changes            -> report "already current"
    - value-only drift      -> build candidate protocol
    - missing required keys -> structural change, needs manual RE (report)
 4. live-verify the candidate with a signed probe request (config-gated)
 5. on success: backup + write protocol.yaml, hot-reload store, reset detector
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ..config import settings
from ..protocol_store import ProtocolStore, apply_diff, diff_extracted
from ..upstream.identity import DeviceIdentity
from ..upstream.signers import derive_secret_bytes
from . import extractor as ext


@dataclass
class HealReport:
    status: str                     # healed | current | structural_change | failed | no_apk | probe_failed | probe_error
    source_apk: Optional[str] = None
    message: str = ""
    diff: Dict[str, Any] = field(default_factory=dict)
    new_version: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "source_apk": self.source_apk,
            "message": self.message,
            "new_protocol_version": self.new_version,
            "diff": self.diff,
        }


class Healer:
    def __init__(self, store: ProtocolStore, identity: DeviceIdentity):
        self.store = store
        self.identity = identity

    def scan_and_heal(self, detector=None, client_factory=None,
                      probe_endpoint_name: str = "search_suggest") -> HealReport:
        cfg = settings()
        watch = cfg.watch_dir
        apk = ext.newest_apk_in(watch)
        if apk is None:
            return HealReport(
                status="no_apk",
                message=f"No APK found in {watch}. Download the latest MovieBox APK "
                        f"and place it there, then retry.")

        try:
            extracted = ext.extract(apk)
        except Exception as exc:
            return HealReport(status="failed", source_apk=apk.name,
                              message=f"extraction failed: {exc}",
                              diff={"traceback": traceback.format_exc(limit=3)})

        required = self.store.required_keys()
        diff = diff_extracted(self.store.data, extracted.to_protocol_facts(), required)

        if not diff.has_changes:
            bump = ext.looks_like_version_bump(extracted, self.store.data["app"])
            msg = ("APK protocol facts identical to current protocol.yaml"
                   + (" (version code matches too)" if not bump else
                      f"; version_code differs ({extracted.version_code}) but no secret drift"))
            if bump:
                candidate = dict(self.store.data)
                candidate.setdefault("app", {})
                candidate["app"]["version_code"] = extracted.version_code
                candidate["app"]["version_name"] = extracted.version_name
                return self._commit(candidate, diff, apk.name, detector, msg)
            return HealReport(status="current", source_apk=apk.name, message=msg,
                              diff=diff.summary())

        if not diff.healable:
            return HealReport(
                status="structural_change",
                source_apk=apk.name,
                message="Structural manifest drift detected (required signing keys "
                        "missing/renamed in the new APK). Automatic healing is not safe "
                        "here; manual RE required. See diff for exactly what moved.",
                diff=diff.summary())

        candidate = apply_diff(self.store.data, diff, apk.name)

        if cfg.get("selfheal.verify_probe_on_heal") and not cfg.get("selfheal.offline"):
            ok, detail = self._probe(candidate, probe_endpoint_name, client_factory)
            if not ok:
                return HealReport(
                    status="probe_failed",
                    source_apk=apk.name,
                    message=f"Candidate secrets still rejected upstream; keeping old "
                            f"protocol. Probe detail: {detail}",
                    diff=diff.summary())

        msg = (f"Auto-healed from {apk.name}: "
               f"{len(diff.changed_secrets)} secret(s) rotated"
               + (f", app fields updated: {sorted(diff.changed_app)}" if diff.changed_app else ""))
        return self._commit(candidate, diff, apk.name, detector, msg)

    # -------------------------------------------------------------- internals

    def _probe(self, candidate: dict, ep_name: str, client_factory) -> tuple[bool, str]:
        """Sign a cheap request with the candidate protocol and see if upstream accepts."""
        try:
            tmp_store = _ShadowStore(candidate)
            tmp_identity = _ShadowIdentity(self.identity, candidate)
            if client_factory is None:
                from ..upstream.client import UpstreamClient
                client = UpstreamClient(tmp_store, tmp_identity)
            else:
                client = client_factory(tmp_store, tmp_identity)
            ep = client.store_ep(ep_name) if hasattr(client, "store_ep") else None
            if ep is None:
                from ..upstream.endpoints import Endpoints
                ep = Endpoints(candidate["endpoints"]).get(ep_name)
            client.request(ep, params={"q": "a", "page": 1, "perPage": 1})
            return True, ""
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    def _commit(self, candidate, diff, apk_name, detector, message) -> HealReport:
        self.store.save(candidate, backup_dir=settings().backups_dir)
        if detector is not None:
            detector.reset()
        return HealReport(status="healed" if diff.changed_secrets else "current",
                          source_apk=apk_name, message=message,
                          diff=diff.summary(),
                          new_version=candidate.get("version"))


class _ShadowStore:
    """ProtocolStore duck-type over a candidate dict (no disk IO)."""
    def __init__(self, data: dict):
        self._data = data

    @property
    def data(self) -> dict:
        return self._data


class _ShadowIdentity(DeviceIdentity):
    def __init__(self, real: DeviceIdentity, proto: dict):
        self.path = real.path
        self.protocol = proto
        self.region = real.region
        self._lock = real._lock
        self._data = real._data
