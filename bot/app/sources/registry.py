from __future__ import annotations

from collections.abc import Mapping

from app.sources.base import ListingsSource


class SourceRegistry:
    def __init__(self, sources: list[ListingsSource]):
        self._by_key: dict[str, ListingsSource] = {s.key: s for s in sources}

    @property
    def keys(self) -> list[str]:
        return sorted(self._by_key.keys())

    def get(self, key: str) -> ListingsSource:
        if key not in self._by_key:
            raise KeyError(f"Unknown source: {key}")
        return self._by_key[key]

    def as_mapping(self) -> Mapping[str, ListingsSource]:
        return dict(self._by_key)

