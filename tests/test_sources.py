from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path

import pytest

from scraping.sources.base import SourceBatch
from scraping.sources.darwinbox import DarwinboxSourceAdapter
from scraping.sources.fixture import FixtureSourceAdapter
from scraping.sources.jazzhr import JazzHRSourceAdapter
from scraping.sources.lever import LeverSourceAdapter
from scraping.sources.registry import (
    UnknownSourceError,
    executable_source_keys,
    get_source_adapter,
    registered_source_keys,
    source_unavailability_message,
    user_selectable_source_keys,
    user_visible_source_keys,
)


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


def test_registry_normalizes_source_and_selects_lever_adapter() -> None:
    adapter = get_source_adapter(
        CompanyStub(
            source="  LeVeR  ",
            source_jobs_url="https://jobs.lever.co/olo",
        )
    )

    assert isinstance(adapter, LeverSourceAdapter)


def test_registry_keeps_darwinbox_registered_visible_selectable_and_executable() -> None:
    adapter = get_source_adapter(
        CompanyStub(
            source="  DaRwInBoX  ",
            source_jobs_url="https://tenant.darwinbox.com/ms/candidate/careers",
        )
    )

    assert isinstance(adapter, DarwinboxSourceAdapter)
    assert "darwinbox" in registered_source_keys()
    assert "darwinbox" in user_visible_source_keys()
    assert "darwinbox" in user_selectable_source_keys()
    assert "darwinbox" in executable_source_keys()
    assert source_unavailability_message(" DaRwInBoX ") is None


def test_registry_keeps_jazzhr_registered_visible_selectable_and_executable() -> None:
    adapter = get_source_adapter(
        CompanyStub(
            source="  JaZzHr  ",
            source_jobs_url="https://example.applytojob.com/apply",
        )
    )

    assert isinstance(adapter, JazzHRSourceAdapter)
    assert "jazzhr" in registered_source_keys()
    assert "jazzhr" in user_visible_source_keys()
    assert "jazzhr" in user_selectable_source_keys()
    assert "jazzhr" in executable_source_keys()
    assert source_unavailability_message(" JaZzHr ") is None


def test_registry_distinguishes_registered_and_user_selectable_sources() -> None:
    registered_keys = registered_source_keys()
    visible_keys = user_visible_source_keys()
    selectable_keys = user_selectable_source_keys()
    executable_keys = executable_source_keys()

    assert isinstance(registered_keys, tuple)
    assert isinstance(visible_keys, tuple)
    assert isinstance(selectable_keys, tuple)
    assert isinstance(executable_keys, tuple)
    assert "fixture" in registered_keys
    assert "fixture" in executable_keys
    assert "fixture" not in visible_keys
    assert "fixture" not in selectable_keys
    assert "darwinbox" in registered_keys
    assert "darwinbox" in visible_keys
    assert "darwinbox" in selectable_keys
    assert "darwinbox" in executable_keys
    assert "lever" in registered_keys
    assert "lever" in visible_keys
    assert "lever" in selectable_keys
    assert "lever" in executable_keys
    assert "jazzhr" in registered_keys
    assert "jazzhr" in visible_keys
    assert "jazzhr" in selectable_keys
    assert "jazzhr" in executable_keys
    assert set(selectable_keys) <= set(registered_keys)
    assert set(executable_keys) <= set(registered_keys)


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
