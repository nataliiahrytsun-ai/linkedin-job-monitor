from __future__ import annotations

import importlib
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scraping.pipeline import run_source_pipeline
from scraping.sources.dreamjobs import DreamJobsSourceAdapter
from scraping.sources.registry import get_source_adapter

FIXTURES = Path(__file__).parent / "fixtures" / "dreamjobs"


@dataclass
class FakeHttpRequest:
    responses: Iterator[str]
    calls: list[tuple[str, str]] = field(default_factory=list)

    def __call__(
        self,
        method: str,
        url: str,
        _headers: Mapping[str, str],
        _payload: Mapping[str, object] | None,
        _timeout: float,
    ) -> str:
        self.calls.append((method, url))
        return next(self.responses)


@pytest.fixture(scope="module", autouse=True)
def migrated_database(tmp_path_factory: pytest.TempPathFactory) -> None:
    apps = importlib.import_module("django.apps").apps
    if not apps.ready:
        database_path = tmp_path_factory.mktemp("dreamjobs-pipeline-db") / "pipeline.sqlite3"
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


def test_registered_dreamjobs_adapter_runs_through_source_owned_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company = model("companies.Company").objects.create(name="Data Sentics")
    dreamjobs_source = company.sources.create(
        source="dreamjobs",
        source_jobs_url="https://careers.datasentics.com/jobs",
        approval_status="approved",
        is_active=True,
    )
    other_source = company.sources.create(
        source="lever",
        source_jobs_url="https://jobs.lever.co/other",
        approval_status="approved",
        is_active=True,
    )
    http = FakeHttpRequest(
        iter((fixture("listing.html"), fixture("detail_25520.json"), fixture("detail_25319.json")))
    )
    registry = importlib.import_module("scraping.sources.registry")
    monkeypatch.setitem(
        registry._ADAPTER_FACTORIES, "dreamjobs", lambda: DreamJobsSourceAdapter(http_request=http)
    )

    first = run_source_pipeline(
        company_source=dreamjobs_source,
        adapter=get_source_adapter(dreamjobs_source),
        started_at=datetime(2026, 8, 13, 8, tzinfo=UTC),
        finished_at=datetime(2026, 8, 13, 8, 1, tzinfo=UTC),
    )

    assert first.scrape_run.status == "success"
    assert first.scrape_run.requests_made == 3
    assert first.jobs_created == 2
    jobs = model("jobs.JobPosting").objects.filter(company_source=dreamjobs_source)
    assert set(jobs.values_list("source_job_id", flat=True)) == {"25520", "25319"}
    assert not model("jobs.JobPosting").objects.filter(company_source=other_source).exists()

    second_http = FakeHttpRequest(
        iter((fixture("listing.html"), fixture("detail_25520.json"), fixture("detail_25319.json")))
    )
    second = run_source_pipeline(
        company_source=dreamjobs_source,
        adapter=DreamJobsSourceAdapter(http_request=second_http),
        started_at=datetime(2026, 8, 13, 9, tzinfo=UTC),
        finished_at=datetime(2026, 8, 13, 9, 1, tzinfo=UTC),
    )
    assert second.jobs_created == 0
    assert model("jobs.JobPosting").objects.filter(company_source=dreamjobs_source).count() == 2
