"""Read ordered source-neutral job records from a local JSON fixture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from django.conf import settings  # type: ignore[import-untyped]

from scraping.sources.base import SourceBatch, SourceCompany, SourceError, SourceRecord

type FixtureRecord = SourceRecord


class FixtureSourceError(SourceError):
    """Base error for local fixture loading failures."""


class FixtureFileNotFoundError(FixtureSourceError):
    """The requested local fixture does not exist."""


class FixtureFormatError(FixtureSourceError, ValueError):
    """The local fixture is not a JSON array of record objects."""


def load_fixture_records(fixture_path: Path) -> tuple[FixtureRecord, ...]:
    """Load a JSON array from disk while preserving its record order."""
    try:
        serialized = fixture_path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise FixtureFileNotFoundError("fixture file does not exist") from error
    except OSError as error:
        raise FixtureSourceError("fixture file could not be read") from error

    try:
        document = cast(object, json.loads(serialized))
    except json.JSONDecodeError as error:
        raise FixtureFormatError("fixture must contain valid JSON") from error

    if not isinstance(document, list):
        raise FixtureFormatError("fixture root must be a JSON array")

    records: list[FixtureRecord] = []
    for index, value in enumerate(document):
        if not isinstance(value, dict) or any(
            not isinstance(key, str) for key in value
        ):
            raise FixtureFormatError(
                f"fixture record {index} must be a JSON object with string keys"
            )
        records.append(dict(cast(dict[str, object], value)))
    return tuple(records)


class FixtureSourceAdapter:
    """Read the configured local fixture without making network requests."""

    def __init__(self, fixture_path: Path | None = None) -> None:
        self._fixture_path = fixture_path

    def fetch(self, *, company: SourceCompany) -> SourceBatch:
        del company
        fixture_path = self._fixture_path
        if fixture_path is None:
            fixture_path = cast(Path, settings.JOB_MONITOR_FIXTURE_PATH)
        return SourceBatch(
            records=load_fixture_records(fixture_path),
            requests_made=0,
        )
