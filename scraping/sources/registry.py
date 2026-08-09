"""Explicit registry for application vacancy source adapters."""

from __future__ import annotations

from collections.abc import Callable

from scraping.sources.base import SourceAdapter, SourceCompany, SourceError
from scraping.sources.fixture import FixtureSourceAdapter


class UnknownSourceError(SourceError):
    """No application adapter is registered for a company source."""


def normalize_source_key(source: str) -> str:
    """Normalize a configured source for stable registry lookup."""
    return source.strip().casefold()


_ADAPTER_FACTORIES: dict[str, Callable[[], SourceAdapter]] = {
    "fixture": FixtureSourceAdapter,
}


def get_source_adapter(company: SourceCompany) -> SourceAdapter:
    """Return the registered adapter for ``company.source``."""
    source_key = normalize_source_key(company.source)
    try:
        factory = _ADAPTER_FACTORIES[source_key]
    except KeyError as error:
        raise UnknownSourceError(
            f"no adapter is registered for source {source_key or '<empty>'}"
        ) from error
    return factory()
