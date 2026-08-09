"""Source-neutral adapter contracts and the local fixture implementation."""

from scraping.sources.base import (
    SourceAdapter,
    SourceBatch,
    SourceCompany,
    SourceError,
    SourceRecord,
)
from scraping.sources.fixture import (
    FixtureFileNotFoundError,
    FixtureFormatError,
    FixtureRecord,
    FixtureSourceAdapter,
    FixtureSourceError,
    load_fixture_records,
)

__all__ = [
    "FixtureFileNotFoundError",
    "FixtureFormatError",
    "FixtureRecord",
    "FixtureSourceAdapter",
    "FixtureSourceError",
    "SourceAdapter",
    "SourceBatch",
    "SourceCompany",
    "SourceError",
    "SourceRecord",
    "load_fixture_records",
]
