"""Explicit registry for application vacancy source adapters."""

from __future__ import annotations

from collections.abc import Callable

from scraping.sources.base import SourceAdapter, SourceConfiguration, SourceError
from scraping.sources.darwinbox import DarwinboxSourceAdapter
from scraping.sources.dreamjobs import DreamJobsSourceAdapter
from scraping.sources.fixture import FixtureSourceAdapter
from scraping.sources.jazzhr import JazzHRSourceAdapter
from scraping.sources.lever import LeverSourceAdapter


class UnknownSourceError(SourceError):
    """No application adapter is registered for a company source."""


def normalize_source_key(source: str) -> str:
    """Normalize a configured source for stable registry lookup."""
    return source.strip().casefold()


_ADAPTER_FACTORIES: dict[str, Callable[[], SourceAdapter]] = {
    "darwinbox": DarwinboxSourceAdapter,
    "dreamjobs": DreamJobsSourceAdapter,
    "fixture": FixtureSourceAdapter,
    "jazzhr": JazzHRSourceAdapter,
    "lever": LeverSourceAdapter,
}
_USER_VISIBLE_SOURCE_KEYS = frozenset({"darwinbox", "dreamjobs", "jazzhr", "lever", "linkedin"})
_USER_SELECTABLE_SOURCE_KEYS = frozenset({"darwinbox", "dreamjobs", "jazzhr", "lever"})
_EXECUTION_BLOCKED_SOURCE_KEYS: frozenset[str] = frozenset()
_SOURCE_UNAVAILABILITY_MESSAGES: dict[str, str] = {
    "linkedin": (
        "Technical adapter ready · Production disabled · "
        "Requires approved LinkedIn access"
    ),
}


def registered_source_keys() -> tuple[str, ...]:
    """Return the immutable, stable set of application-supported source keys."""
    return tuple(sorted(_ADAPTER_FACTORIES))


def user_selectable_source_keys() -> tuple[str, ...]:
    """Return registered sources intended for user-managed company records."""
    return tuple(
        source_key
        for source_key in registered_source_keys()
        if source_key in _USER_SELECTABLE_SOURCE_KEYS
    )


def user_visible_source_keys() -> tuple[str, ...]:
    """Return executable and catalog-only sources explained in source management."""
    return tuple(sorted(_USER_VISIBLE_SOURCE_KEYS))


def executable_source_keys() -> tuple[str, ...]:
    """Return registered sources currently permitted for application execution."""
    return tuple(
        source_key
        for source_key in registered_source_keys()
        if source_key not in _EXECUTION_BLOCKED_SOURCE_KEYS
    )


def source_unavailability_message(source: str) -> str | None:
    """Return a safe UI explanation when a visible source is not selectable."""
    return _SOURCE_UNAVAILABILITY_MESSAGES.get(normalize_source_key(source))


def get_source_adapter(source_configuration: SourceConfiguration) -> SourceAdapter:
    """Return the registered adapter for a persisted source configuration."""
    source_key = normalize_source_key(source_configuration.source)
    try:
        factory = _ADAPTER_FACTORIES[source_key]
    except KeyError as error:
        raise UnknownSourceError(
            f"no adapter is registered for source {source_key or '<empty>'}"
        ) from error
    return factory()
