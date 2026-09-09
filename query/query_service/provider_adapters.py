"""Pluggable provider automation adapters selected by resolved URL hostname."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class ProviderAdapter:
    host: str
    name: str


GENERIC_ADAPTER = ProviderAdapter("*", "generic-dutch-then-english-form")
_ADAPTERS: dict[str, ProviderAdapter] = {}


def adapter_for(url: str) -> ProviderAdapter:
    """Return a site-specific adapter when registered, otherwise generic."""
    return _ADAPTERS.get((urlparse(url).hostname or "").lower(), GENERIC_ADAPTER)


def register(adapter: ProviderAdapter) -> None:
    """Register a dedicated provider adapter without changing generic flow."""
    _ADAPTERS[adapter.host.lower()] = adapter
