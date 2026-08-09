from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path

import pytest

from scraping.sources.base import SourceBatch
from scraping.sources.fixture import FixtureSourceAdapter
from scraping.sources.registry import UnknownSourceError, get_source_adapter


@dataclass
class CompanyStub:
    source: str
    source_jobs_url: str | None = None


def test_source_batch_is_immutable_and_validates_request_count() -> None:
    batch = SourceBatch(records=({"source": "fixture"},), requests_made=0)

    assert batch.records == ({"source": "fixture"},)
    assert batch.requests_made == 0
    with pytest.raises(FrozenInstanceError):
        batch.requests_made = 1  # type: ignore[misc]
    with pytest.raises(ValueError, match="non-negative int"):
        SourceBatch(records=(), requests_made=-1)
    with pytest.raises(ValueError, match="excluding bool"):
        SourceBatch(records=(), requests_made=True)


def test_registry_normalizes_source_and_selects_fixture_adapter() -> None:
    adapter = get_source_adapter(CompanyStub(source="  FiXtUrE  "))

    assert isinstance(adapter, FixtureSourceAdapter)


def test_registry_rejects_unknown_source_safely() -> None:
    with pytest.raises(UnknownSourceError, match="not-permitted") as caught:
        get_source_adapter(CompanyStub(source=" Not-Permitted "))

    assert caught.value.requests_made == 0


def test_fixture_adapter_returns_offline_source_batch(tmp_path: Path) -> None:
    fixture_path = tmp_path / "jobs.json"
    fixture_path.write_text(
        '[{"source": "fixture", "title": "Analyst"}]', encoding="utf-8"
    )

    batch = FixtureSourceAdapter(fixture_path).fetch(
        company=CompanyStub(
            source="fixture",
            source_jobs_url="https://unused.example.test/jobs",
        )
    )

    assert batch.records == ({"source": "fixture", "title": "Analyst"},)
    assert batch.requests_made == 0
