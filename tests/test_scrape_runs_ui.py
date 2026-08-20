from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

import django  # type: ignore[import-untyped]
import pytest
from django.test import TestCase, override_settings  # type: ignore[import-untyped]
from django.test.utils import (  # type: ignore[import-untyped]
    setup_test_environment,
    teardown_test_environment,
)
from django.urls import reverse  # type: ignore[import-untyped]

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "job_monitor.settings")


@pytest.fixture(scope="module", autouse=True)
def django_template_test_environment(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    database_path = Path(tmp_path_factory.mktemp("scrape-runs-ui-db")) / "ui.sqlite3"
    os.environ["JOB_MONITOR_SQLITE_PATH"] = str(database_path)
    django.setup()
    management = importlib.import_module("django.core.management")
    management.call_command("migrate", interactive=False, verbosity=0)
    management.call_command("flush", interactive=False, verbosity=0)
    setup_test_environment()
    yield
    teardown_test_environment()


def model(name: str) -> Any:
    return importlib.import_module("django.apps").apps.get_model(name)


def company(*, name: str) -> Any:
    return model("companies.Company").objects.create(
        name=name,
        source="fixture",
        source_jobs_url=f"https://jobs.example.test/{name.casefold().replace(' ', '-')}",
    )


def scrape_run(
    company_record: Any,
    *,
    status: str,
    started_at: datetime,
    jobs_found: int = 3,
    jobs_created: int = 1,
    jobs_updated: int = 1,
    requests_made: int = 2,
    error_message: str = "",
) -> Any:
    terminal = status != "running"
    return model("scrape_runs.ScrapeRun").objects.create(
        company=company_record,
        status=status,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=12) if terminal else None,
        duration_seconds=Decimal("12.000") if terminal else None,
        jobs_found=jobs_found,
        jobs_created=jobs_created,
        jobs_updated=jobs_updated,
        requests_made=requests_made,
        error_message=error_message,
    )


@override_settings(ALLOWED_HOSTS=["testserver"])
class ScrapeRunHistoryTests(TestCase):  # type: ignore[misc]
    def test_history_renders_vienna_cet_and_cest_offsets(self) -> None:
        employer = company(name="Vienna Time Company")
        scrape_run(
            employer,
            status="success",
            started_at=datetime(2026, 1, 15, 12, tzinfo=UTC),
        )
        scrape_run(
            employer,
            status="success",
            started_at=datetime(2026, 7, 15, 12, tzinfo=UTC),
        )

        html = self.client.get(reverse("scrape_runs:list")).content.decode()

        assert "15 Jan 2026, 13:00" in html
        assert "15 Jul 2026, 14:00" in html
        assert 'datetime="2026-01-15T13:00:00+01:00"' in html
        assert 'datetime="2026-07-15T14:00:00+02:00"' in html

    def test_history_is_newest_first_and_displays_real_fields(self) -> None:
        employer = company(name="Northwind")
        older = scrape_run(
            employer,
            status="success",
            started_at=datetime(2026, 8, 10, 8, tzinfo=UTC),
            jobs_found=8,
            jobs_created=3,
            jobs_updated=2,
            requests_made=4,
        )
        newer = scrape_run(
            employer,
            status="partial",
            started_at=datetime(2026, 8, 11, 9, tzinfo=UTC),
            error_message="One vacancy detail could not be processed.",
        )

        response = self.client.get(reverse("scrape_runs:list"))
        html = response.content.decode()

        assert response.status_code == 200
        self.assertTemplateUsed(response, "scrape_runs/scrape_run_list.html")
        assert list(response.context["page_obj"].object_list) == [newer, older]
        assert "Northwind" in html
        assert 'class="status-badge status-success">SUCCESS</span>' in html
        assert 'class="status-badge status-partial">PARTIAL</span>' in html
        for label in ("Found", "Created", "Updated", "Requests", "Duration"):
            assert label in html
        assert ">8</dd>" in html
        assert ">3</dd>" in html
        assert ">2</dd>" in html
        assert 'href="/companies/' in html

    def test_failed_error_is_visible_and_success_has_no_fake_error(self) -> None:
        failed_company = company(name="Failed Co")
        success_company = company(name="Clean Co")
        failure_text = "Source timed out while reading the public vacancy feed."
        scrape_run(
            failed_company,
            status="failed",
            started_at=datetime(2026, 8, 11, 10, tzinfo=UTC),
            jobs_found=0,
            jobs_created=0,
            jobs_updated=0,
            error_message=failure_text,
        )
        scrape_run(
            success_company,
            status="success",
            started_at=datetime(2026, 8, 11, 9, tzinfo=UTC),
        )

        html = self.client.get(reverse("scrape_runs:list")).content.decode()

        assert failure_text in html
        assert html.count("<details>") == 3
        assert "fake error" not in html.casefold()

    def test_history_has_compact_desktop_columns_and_separate_responsive_cards(
        self,
    ) -> None:
        employer = company(name="Responsive Company")
        scrape_run(
            employer,
            status="success",
            started_at=datetime(2026, 8, 11, 10, 2, tzinfo=UTC),
            jobs_found=3,
            jobs_created=0,
            jobs_updated=0,
            requests_made=0,
        )

        html = self.client.get(reverse("scrape_runs:list")).content.decode()
        css = Path("static/css/app.css").read_text(encoding="utf-8")

        for heading in (
            "Company",
            "Status",
            "Started",
            "Finished",
            "Found",
            "Created",
            "Updated",
            "Requests",
            "Duration",
            "Error",
        ):
            assert f'<th scope="col">{heading}</th>' in html
        assert html.count('<th scope="col">') == 10
        assert html.count('class="scrape-run-number"') == 4
        assert 'class="scrape-run-duration"' in html
        assert 'class="scrape-run-cards"' in html
        assert 'class="card scrape-run-history-card"' in html
        for card_group in (
            "scrape-run-card-time",
            "scrape-run-card-results",
            "scrape-run-card-performance",
            "scrape-run-card-error",
        ):
            assert card_group in html
        assert "@media (max-width: 64rem)" in css
        assert ".scrape-run-table-container {\n    display: none;" in css
        assert ".scrape-run-cards {\n    display: grid;" in css
        assert "@media (max-width: 40rem)" in css

    def test_running_activity_and_polling_include_initial_database_state(self) -> None:
        active_company = company(name="Active Run Co")
        running = scrape_run(
            active_company,
            status="running",
            started_at=datetime(2026, 8, 11, 11, 30, tzinfo=UTC),
            jobs_found=0,
            jobs_created=0,
            jobs_updated=0,
            requests_made=0,
        )

        html = self.client.get(reverse("scrape_runs:list")).content.decode()

        assert "Current activity" in html
        assert "Active Run Co" in html
        assert "Started" in html
        assert f"[{running.pk}]" in html
        assert f'"id": {running.pk}' in html
        assert 'data-interval-ms="5000"' in html
        assert 'src="/static/js/scrape_run_polling.js"' in html

    def test_idle_and_no_history_empty_states_are_compact_and_clear(self) -> None:
        html = self.client.get(reverse("scrape_runs:list")).content.decode()

        assert "No monitoring runs are active." in html
        assert "No scrape runs yet" in html
        assert "[]" in html
        assert '<script id="latest-run-state" type="application/json">null</script>' in html

    def test_idle_page_still_loads_page_presence_polling_contract(self) -> None:
        html = self.client.get(reverse("scrape_runs:list")).content.decode()
        javascript = Path("static/js/scrape_run_polling.js").read_text(encoding="utf-8")

        assert 'data-interval-ms="5000"' in html
        assert 'id="running-run-ids"' in html
        assert 'id="latest-run-state"' in html
        assert "runIds.length === 0" in javascript
        assert 'statusUrl.searchParams.set("ids", runIds.join(","))' in javascript
        assert javascript.index("runIds.length === 0") < javascript.index(
            'statusUrl.searchParams.set("ids", runIds.join(","))'
        )
        assert "document.hidden" in javascript
        assert "latestSignature(payload.latest_run)" in javascript

    def test_company_relation_is_eager_loaded_without_n_plus_one(self) -> None:
        base_time = datetime(2026, 8, 11, 8, tzinfo=UTC)
        for index in range(5):
            scrape_run(
                company(name=f"Company {index}"),
                status="success",
                started_at=base_time - timedelta(minutes=index),
            )

        with self.assertNumQueries(3):
            response = self.client.get(reverse("scrape_runs:list"))

        assert response.status_code == 200
        for index in range(5):
            assert f"Company {index}" in response.content.decode()

    def test_history_uses_standard_twenty_five_item_pagination(self) -> None:
        employer = company(name="Paginated Co")
        base_time = datetime(2026, 8, 11, 12, tzinfo=UTC)
        for index in range(26):
            scrape_run(
                employer,
                status="success",
                started_at=base_time - timedelta(minutes=index),
            )

        first = self.client.get(reverse("scrape_runs:list"))
        second = self.client.get(reverse("scrape_runs:list"), {"page": 2})

        assert len(first.context["page_obj"].object_list) == 25
        assert len(second.context["page_obj"].object_list) == 1
        assert "Page 1 of 2" in first.content.decode()

    def test_navigation_links_to_working_page_and_marks_it_current(self) -> None:
        html = self.client.get(reverse("scrape_runs:list")).content.decode()

        assert 'href="/scrape-runs/" aria-current="page">Scrape runs</a>' in html
        assert "nav-placeholder" not in html


@override_settings(ALLOWED_HOSTS=["testserver"])
class ScrapeRunPollingEndpointTests(TestCase):  # type: ignore[misc]
    def test_endpoint_detects_run_created_after_initial_idle_page_load(self) -> None:
        initial_page = self.client.get(reverse("scrape_runs:list"))
        assert initial_page.context["running_run_ids"] == []
        assert initial_page.context["latest_run_state"] is None

        new_run = scrape_run(
            company(name="Started Later"),
            status="success",
            started_at=datetime(2026, 8, 11, 12, tzinfo=UTC),
        )

        response = self.client.get(reverse("scrape_runs:status"), {"ids": ""})
        payload = response.json()

        assert response.status_code == 200
        assert payload["latest_run"]["id"] == new_run.pk
        assert payload["latest_run"]["status"] == "success"
        assert payload["runs"] == []

    def test_endpoint_reports_new_running_run_without_requested_ids(self) -> None:
        new_run = scrape_run(
            company(name="New Running"),
            status="running",
            started_at=datetime(2026, 8, 11, 12, 30, tzinfo=UTC),
            jobs_found=0,
            jobs_created=0,
            jobs_updated=0,
            requests_made=0,
        )

        response = self.client.get(reverse("scrape_runs:status"), {"ids": ""})
        payload = response.json()

        assert payload["latest_run"]["id"] == new_run.pk
        assert [run["id"] for run in payload["runs"]] == [new_run.pk]
        assert payload["runs"][0]["is_terminal"] is False

    def test_endpoint_returns_running_and_terminal_database_state(self) -> None:
        running = scrape_run(
            company(name="Polling Running"),
            status="running",
            started_at=datetime(2026, 8, 11, 11, tzinfo=UTC),
            jobs_found=0,
            jobs_created=0,
            jobs_updated=0,
            requests_made=0,
        )
        terminal = scrape_run(
            company(name="Polling Failed"),
            status="failed",
            started_at=datetime(2026, 8, 11, 10, tzinfo=UTC),
            jobs_found=0,
            jobs_created=0,
            jobs_updated=0,
            error_message="Public source unavailable",
        )

        response = self.client.get(
            reverse("scrape_runs:status"),
            {"ids": f"{running.pk},{terminal.pk}"},
        )
        by_id = {item["id"]: item for item in response.json()["runs"]}

        assert response.status_code == 200
        assert by_id[running.pk]["status"] == "running"
        assert by_id[running.pk]["is_terminal"] is False
        assert by_id[terminal.pk]["status"] == "failed"
        assert by_id[terminal.pk]["is_terminal"] is True
        assert by_id[terminal.pk]["error_message"] == "Public source unavailable"

    def test_endpoint_is_read_only_and_does_not_start_scraping(self) -> None:
        run = scrape_run(
            company(name="Read Only Co"),
            status="success",
            started_at=datetime(2026, 8, 11, 9, tzinfo=UTC),
        )
        before = list(
            model("scrape_runs.ScrapeRun").objects.values_list(
                "pk", "status", "finished_at", "jobs_found", "error_message"
            )
        )

        with (
            patch("scraping.background.ControlledBackgroundExecutor.submit_pipeline") as submit,
            patch("scraping.pipeline.run_source_pipeline") as pipeline,
        ):
            response = self.client.get(
                reverse("scrape_runs:status"),
                {"ids": str(run.pk)},
            )

        after = list(
            model("scrape_runs.ScrapeRun").objects.values_list(
                "pk", "status", "finished_at", "jobs_found", "error_message"
            )
        )
        assert response.status_code == 200
        assert after == before
        submit.assert_not_called()
        pipeline.assert_not_called()

    def test_endpoint_ignores_invalid_ids_without_mutating_database(self) -> None:
        response = self.client.get(
            reverse("scrape_runs:status"),
            {"ids": "bad,-2,0"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "latest_run": None,
            "company_latest_run": None,
            "runs": [],
            "expected_source_runs": [],
            "submission_complete": False,
        }
        assert model("scrape_runs.ScrapeRun").objects.count() == 0
