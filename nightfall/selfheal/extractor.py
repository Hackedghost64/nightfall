"""APK -> secrets extraction.

Two input modes:
- raw .apk file (zip): parses binary AndroidManifest.xml with axml.py
- decoded tree / manifest path (apktool output): reads text XML

Returns a normalized dict:
{ "app": {package, version_name, version_code}, "secrets": {...}, "all_meta": {...} }
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from ..axml import AxmlDocument, parse as axml_parse

ANDROID_NS = "{http://schemas.android.com/apk/res/android}"

# meta-data android:name  ->  protocol secrets key
KEY_MAP = {
    "gateway_secret_online": "gateway_secret_online",
    "gateway_secret_test": "gateway_secret_test",
}


@dataclass
class Extraction:
    source: str
    package: str = ""
    version_name: str = ""
    version_code: str = ""
    secrets: Dict[str, str] = field(default_factory=dict)
    all_meta: Dict[str, str] = field(default_factory=dict)

    def to_protocol_facts(self) -> Dict:
        return {
            "app": {
                "package": self.package,
                "version_name": self.version_name,
                "version_code": int(self.version_code) if str(self.version_code).isdigit()
                                else self.version_code,
            },
            "secrets": dict(self.secrets),
        }


def extract(source: str | Path) -> Extraction:
    p = Path(source)
    if not p.exists():
        raise FileNotFoundError(f"extraction source not found: {p}")
    if p.suffix.lower() == ".apk" or zipfile.is_zipfile(p):
        return _extract_from_apk(p)
    return _extract_from_tree(p)


def _extract_from_apk(apk_path: Path) -> Extraction:
    with zipfile.ZipFile(apk_path) as zf:
        raw = zf.read("AndroidManifest.xml")
    doc = axml_parse(raw)
    return _from_axml_doc(doc, str(apk_path))


def _extract_from_tree(path: Path) -> Extraction:
    manifest = path if path.is_file() and path.name == "AndroidManifest.xml" \
        else path / "AndroidManifest.xml"
    if not manifest.exists():
        raise FileNotFoundError(f"AndroidManifest.xml not found under {path}")
    root = ET.parse(manifest).getroot()
    ex = Extraction(source=str(manifest))
    ex.package = root.get("package", "")
    ex.version_name = root.get("{http://schemas.android.com/apk/res/android}versionName", "") \
        or root.get("versionName", "")
    vc = root.get("{http://schemas.android.com/apk/res/android}versionCode") \
        or root.get("versionCode") or ""
    ex.version_code = vc
    for md in root.iter("meta-data"):
        name = md.get(f"{ANDROID_NS}name") or md.get("name") or ""
        value = md.get(f"{ANDROID_NS}value") or md.get("value") or ""
        if name:
            ex.all_meta[name] = value
    _map_secrets(ex)
    return ex


def _from_axml_doc(doc: AxmlDocument, source: str) -> Extraction:
    ex = Extraction(source=source)
    manifest = doc.find_first("manifest")
    if manifest is not None:
        ex.package = manifest.attrs.get("package", "")
        ex.version_name = manifest.attrs.get("versionName", "")
        ex.version_code = manifest.attrs.get("versionCode", "")
    for md in doc.find_all("meta-data"):
        name = md.attrs.get("name", "")
        value = md.attrs.get("value", "")
        if name:
            ex.all_meta[name] = value
    _map_secrets(ex)
    return ex


def _map_secrets(ex: Extraction) -> None:
    for meta_name, secret_key in KEY_MAP.items():
        val = ex.all_meta.get(meta_name)
        if val and not val.startswith("@"):
            ex.secrets[secret_key] = val


def newest_apk_in(watch_dir: Path) -> Optional[Path]:
    if not watch_dir.exists():
        return None
    apks = sorted(watch_dir.glob("*.apk"), key=lambda p: p.stat().st_mtime, reverse=True)
    return apks[0] if apks else None


def looks_like_version_bump(extracted: Extraction, current_app: dict) -> bool:
    return bool(extracted.version_code) and str(extracted.version_code) != str(current_app.get("version_code"))


_MANIFEST_NAME_RE = re.compile(r"android:name=\"([^\"]+)\"")
