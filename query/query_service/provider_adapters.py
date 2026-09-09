"""Pluggable provider automation adapters selected by resolved URL hostname."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from .config import AccountCredentials, ApplicantProfile


@dataclass(frozen=True)
class ProviderAdapter:
    host: str
    name: str


GENERIC_ADAPTER = ProviderAdapter("*", "generic-dutch-then-english-form")
_ADAPTERS: dict[str, ProviderAdapter] = {}
ProviderFlow = Callable[[Any, ApplicantProfile, AccountCredentials | None], list[str]]
_FLOWS: dict[str, ProviderFlow] = {}


def adapter_for(url: str) -> ProviderAdapter:
    """Return a site-specific adapter when registered, otherwise generic."""
    return _ADAPTERS.get((urlparse(url).hostname or "").lower(), GENERIC_ADAPTER)


def register(adapter: ProviderAdapter) -> None:
    """Register a dedicated provider adapter without changing generic flow."""
    _ADAPTERS[adapter.host.lower()] = adapter


def register_flow(host: str, flow: ProviderFlow) -> None:
    """Register a deterministic provider Playwright flow before Gemini fallback."""
    _FLOWS[host.lower()] = flow


def flow_for(url: str) -> ProviderFlow | None:
    """Return a provider-specific flow, never a generic/LLM substitute."""
    host = (urlparse(url).hostname or "").lower()
    return _FLOWS.get(host) or _FLOWS.get(host.removeprefix("www."))
