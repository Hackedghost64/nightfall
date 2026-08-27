"""Endpoint registry read from protocol.yaml `endpoints` section.

If upstream renames a path, edit protocol.yaml - not code.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class Endpoint:
    name: str
    method: str
    path: str
    needs_host_param: bool = False


class Endpoints:
    def __init__(self, table: Dict[str, dict]):
        self._t = {k: Endpoint(k, v["method"].upper(), v["path"],
                               bool(v.get("needs_host_param", False)))
                   for k, v in table.items()}

    def get(self, name: str) -> Endpoint:
        if name not in self._t:
            raise KeyError(
                f"endpoint '{name}' not in protocol.yaml. Known: {sorted(self._t)}")
        return self._t[name]

    def __contains__(self, name: str) -> bool:
        return name in self._t

    def names(self):
        return sorted(self._t)
