from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scraping.pipeline import run_source_pipeline
from scraping.sources.jazzhr import JazzHRSourceAdapter
from scraping.sources.registry import get_source_adapter

FIXTURES = Path(__file__).parent / "fixtures" / "jazzhr"


@dataclass
class FakeHttpGet:
    responses: Iterator[str]
    calls: list[tuple[str, float]] = field(default_factory=list)

    def __call__(self, url: str, timeout_seconds: float) -> str:
        self.calls.append((url, timeout_seconds))
        return next(self.responses)


@pytest.fixture(scope="module", autouse=True)
def migrated_database(tmp_path_factory: pytest.TempPathFactory) -> None:
    apps = importlib.import_module("django.apps").apps
    if not apps.ready:
        database_path = tmp_path_factory.mktemp("jazzhr-pipeline-db") / "pipeline.sqlite3"
        os.environ["DJANGO_SETTINGS_MODULE"] = "job_monitor.settings"
        os.environ["JOB_MONITOR_SQLITE_PATH"] = str(database_path)
        importlib.import_module("django").setup()
    importlib.import_module("django.core.management").call_command(
        "migrate", interactive=False, verbosity=0
    )


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None) -> None:
    importlib.import_module("django.core.management").call_command(
        "flush", interactive=False, verbosity=0
    )


def model(name: str) -> Any:
    return importlib.import_module("django.apps").apps.get_model(name)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_registered_jazzhr_adapter_runs_through_source_owned_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company = model("companies.Company").objects.create(name="JazzHR Pipeline")
    jazzhr_source = company.sources.create(
        source="jazzhr",
        source_jobs_url="https://example.applytojob.com/apply/jobs",
        approval_status="approved",
        is_active=True,
    )
    other_source = company.sources.create(
        source="lever",
        source_jobs_url="https://jobs.lever.co/other",
        approval_status="approved",
        is_active=True,
    )
    http_get = FakeHttpGet(
        iter(
            (
                fixture("listing.html"),
                fixture("detail_data_lead.html"),
                fixture("detail_platform.html"),
            )
        )
    )
    registry = importlib.import_module("scraping.sources.registry")
    monkeypatch.setitem(
        registry._ADAPTER_FACTORIES,
        "jazzhr",
        lambda: JazzHRSourceAdapter(http_get=http_get),
    )

    result = run_source_pipeline(
        company_source=jazzhr_source,
        adapter=get_source_adapter(jazzhr_source),
        started_at=datetime(2026, 8, 12, 8, tzinfo=UTC),
        finished_at=datetime(2026, 8, 12, 8, 1, tzinfo=UTC),
    )

    assert result.scrape_run.status == "success"
    assert result.scrape_run.company_source_id == jazzhr_source.pk
    assert result.scrape_run.requests_made == 3
    assert result.jobs_found == 2
    assert result.jobs_created == 2
    jazzhr_jobs = model("jobs.JobPosting").objects.filter(
        company_source=jazzhr_source
    )
    assert set(jazzhr_jobs.values_list("source_job_id", flat=True)) == {
        "AbC123xyZ9",
        "QwE987rtY6",
    }
    assert not model("jobs.JobPosting").objects.filter(
        company_source=other_source
    ).exists()
    assert len(http_get.calls) == 3
