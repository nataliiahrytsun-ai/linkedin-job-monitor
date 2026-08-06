"""Local and future permitted source adapters for backend pipelines."""

from scraping.sources.fixture import (
    FixtureFileNotFoundError,
    FixtureFormatError,
    FixtureRecord,
    FixtureSourceError,
    load_fixture_records,
)

__all__ = [
    "FixtureFileNotFoundError",
    "FixtureFormatError",
    "FixtureRecord",
    "FixtureSourceError",
    "load_fixture_records",
]
