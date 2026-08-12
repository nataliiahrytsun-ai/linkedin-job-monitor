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
def django_template_test_environment(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    database_path = Path(tmp_path_factory.mktemp("ui-database")) / "ui.sqlite3"
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


def company(*, name: str, is_active: bool = True) -> Any:
    slug = name.casefold().replace(" ", "-")
    company_record = model("companies.Company").objects.create(
        name=name,
        source="fixture",
        source_jobs_url=f"https://jobs.example.test/{slug}/openings",
        is_active=is_active,
    )
    model("companies.CompanySource").objects.create(
        company=company_record,
        source=company_record.source,
        source_jobs_url=company_record.source_jobs_url,
        approval_status="approved",
        is_active=True,
    )
    return company_record


def company_submission(
    company_record: Any,
    *,
    submitted: bool = True,
    already_running: bool = False,
    failed: bool = False,
) -> Any:
    source_id = company_record.sources.get().pk
    return type(
        "Submission",
        (),
        {
            "submitted_source_ids": (source_id,) if submitted else (),
            "already_running_source_ids": (source_id,) if already_running else (),
            "failed_source_ids": (source_id,) if failed else (),
        },
    )()


def job(company_record: Any, *, sequence: int, status: str) -> Any:
    return model("jobs.JobPosting").objects.create(
        company=company_record,
        company_source=company_record.sources.get(),
        source="fixture",
        source_job_id=f"dashboard-job-{sequence}",
        title=f"Dashboard job {sequence}",
        content_hash=f"{sequence:064x}",
        dedupe_key=f"{sequence + 100:064x}",
        status=status,
    )


def scrape_run(
    company_record: Any,
    *,
    status: str,
    started_at: datetime,
    jobs_created: int = 0,
    finished_at: datetime | None = None,
    company_source: Any | None = None,
) -> Any:
    terminal = status != "running"
    resolved_finished_at = finished_at or (started_at + timedelta(minutes=1) if terminal else None)
    return model("scrape_runs.ScrapeRun").objects.create(
        company=company_record,
        company_source=company_source or company_record.sources.get(),
        status=status,
        started_at=started_at,
        finished_at=resolved_finished_at,
        duration_seconds=Decimal("60.000") if terminal else None,
        jobs_found=jobs_created,
        jobs_created=jobs_created,
        error_message="" if status == "success" or status == "running" else "Run issue",
    )


@override_settings(ALLOWED_HOSTS=["testserver"])
class HomePageTests(TestCase):  # type: ignore[misc]
    def test_dashboard_renders_expected_template_metrics_and_foundation(self) -> None:
        response = self.client.get("/")
        html = response.content.decode()

        assert response.status_code == 200
        self.assertTemplateUsed(response, "home.html")
        self.assertTemplateUsed(response, "base.html")
        assert html.count("<h1") == 1
        assert ">Dashboard</h1>" in html
        for label in (
            "Monitored companies",
            "Active jobs",
            "New &amp; updated",
            "Latest successful run",
            "Running now",
            "Failed runs",
        ):
            assert label in html
        assert "Coming next" not in html

    def test_home_preserves_navigation_responsive_foundation_and_real_links(self) -> None:
        html = self.client.get("/").content.decode()

        assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html
        assert '<nav aria-label="Primary">' in html
        assert '<main id="main-content"' in html
        assert '<link rel="stylesheet" href="/static/css/app.css">' in html
        assert 'class="skip-link" href="#main-content"' in html
        assert 'href="/" aria-current="page">Dashboard</a>' in html
        assert 'href="/companies/">Companies</a>' in html
        assert 'href="/jobs/">Jobs</a>' in html
        assert (
            'class="card dashboard-primary-metric dashboard-metric-link" href="/companies/"' in html
        )
        assert 'class="card dashboard-primary-metric dashboard-metric-link" href="/jobs/"' in html
        assert (
            'class="card dashboard-primary-metric dashboard-metric-link" '
            'href="/jobs/?review_state=unreviewed"' in html
        )
        assert "Companies →" not in html
        assert "Jobs →" not in html
        assert html.count("dashboard-metric-link") == 4
        assert html.count("dashboard-metric-arrow") == 4
        assert html.count(">\u203a</span>") == 4
        assert ">→</span>" not in html
        assert html.count('class="card dashboard-run-metric') == 3
        assert 'class="card dashboard-run-card"' not in html
        assert '<h2 class="dashboard-metric-label">New &amp; updated</h2>' in html
        assert '<p class="dashboard-review-breakdown">0 new · 0 updated</p>' in html
        assert f'action="{reverse("update_all")}" method="post"' in html
        assert 'type="submit">Update all</button>' in html
        assert 'href="/scrape-runs/">Scrape runs</a>' in html
        assert (
            'class="card dashboard-run-metric dashboard-metric-link" '
            'href="/scrape-runs/"' in html
        )
        css = Path("static/css/app.css").read_text(encoding="utf-8")
        assert ".dashboard-metric-link .dashboard-metric-value {" in css
        assert "gap: 0.45rem;" in css
        assert ".dashboard-metric-arrow {\n  display: inline-flex;" in css
        assert (
            ".dashboard-metric-link .dashboard-metric-value {\n"
            "    min-width: 3rem;" in css
        )
        assert "nav-placeholder" not in html

    def test_dashboard_messages_use_dashboard_content_width(self) -> None:
        company(name="Message Company")
        with patch(
            "job_monitor.views.background_executor.submit_company",
            side_effect=lambda *, company: company_submission(company),
        ):
            response = self.client.post(reverse("update_all"))

        html = self.client.get(response.url).content.decode()

        assert 'class="messages dashboard-shell"' in html
        assert "Monitoring started for 1 source." in html

    def test_unknown_url_still_returns_not_found(self) -> None:
        response = self.client.get("/not-a-real-page/")

        assert response.status_code == 404

    def test_empty_dashboard_uses_six_queries_zero_counts_and_never(self) -> None:
        with (
            self.assertNumQueries(6),
            patch("scraping.pipeline.run_fixture_pipeline") as pipeline,
            patch(
                "scraping.background.ControlledBackgroundExecutor.submit_fixture_pipeline"
            ) as background_submit,
        ):
            response = self.client.get("/")
        html = response.content.decode()

        assert response.status_code == 200
        assert response.context["monitored_companies"] == 0
        assert response.context["active_jobs"] == 0
        assert response.context["new_jobs"] == 0
        assert response.context["updated_jobs"] == 0
        assert response.context["unreviewed_jobs"] == 0
        assert response.context["latest_successful_run"] is None
        assert response.context["running_runs"] == 0
        assert response.context["failed_runs"] == 0
        assert "Never" in html
        pipeline.assert_not_called()
        background_submit.assert_not_called()

    def test_dashboard_counts_only_matching_company_job_and_run_statuses(self) -> None:
        active_company = company(name="Active Company")
        company(name="Inactive Company", is_active=False)
        running_company = company(name="Running Company")
        job(active_company, sequence=1, status="active")
        job(active_company, sequence=2, status="not_found")
        job(active_company, sequence=3, status="closed")
        started_at = datetime(2026, 8, 9, 10, tzinfo=UTC)
        scrape_run(active_company, status="running", started_at=started_at)
        scrape_run(running_company, status="running", started_at=started_at)
        scrape_run(active_company, status="failed", started_at=started_at - timedelta(days=1))
        scrape_run(active_company, status="failed", started_at=started_at - timedelta(days=2))
        scrape_run(active_company, status="partial", started_at=started_at - timedelta(days=3))
        scrape_run(active_company, status="success", started_at=started_at - timedelta(days=4))

        response = self.client.get("/")

        assert response.context["monitored_companies"] == 2
        assert response.context["active_jobs"] == 1
        assert response.context["running_runs"] == 2
        assert response.context["failed_runs"] == 2

    def test_failed_runs_link_acknowledges_snapshot_without_changing_history(
        self,
    ) -> None:
        employer = company(name="Unread Failures")
        base_time = datetime(2026, 8, 9, 10, tzinfo=UTC)
        older = scrape_run(employer, status="failed", started_at=base_time)
        newer = scrape_run(
            employer,
            status="failed",
            started_at=base_time + timedelta(hours=1),
        )
        scrape_run(
            employer,
            status="success",
            started_at=base_time + timedelta(hours=2),
        )

        dashboard = self.client.get(reverse("home"))
        expected_link = (
            f'{reverse("scrape_runs:list")}?acknowledge_failed_through={newer.pk}'
        )

        assert dashboard.context["failed_runs"] == 2
        assert f'href="{expected_link}"' in dashboard.content.decode()

        history = self.client.get(expected_link)
        after_acknowledgement = self.client.get(reverse("home"))

        assert history.status_code == 200
        assert history.content.decode().count(">FAILED</span>") == 4
        assert after_acknowledgement.context["failed_runs"] == 0
        older.refresh_from_db()
        newer.refresh_from_db()
        assert older.status == "failed"
        assert newer.status == "failed"

        scrape_run(
            employer,
            status="success",
            started_at=base_time + timedelta(hours=3),
        )
        assert self.client.get(reverse("home")).context["failed_runs"] == 0

        scrape_run(
            employer,
            status="failed",
            started_at=base_time + timedelta(hours=4),
        )
        assert self.client.get(reverse("home")).context["failed_runs"] == 1

    def test_direct_history_navigation_does_not_acknowledge_failed_runs(self) -> None:
        employer = company(name="Direct History")
        scrape_run(
            employer,
            status="failed",
            started_at=datetime(2026, 8, 9, 10, tzinfo=UTC),
        )
        success = scrape_run(
            employer,
            status="success",
            started_at=datetime(2026, 8, 9, 11, tzinfo=UTC),
        )

        self.client.get(reverse("scrape_runs:list"))
        self.client.get(
            reverse("scrape_runs:list"),
            {"acknowledge_failed_through": success.pk},
        )

        assert self.client.get(reverse("home")).context["failed_runs"] == 1

    def test_running_run_that_fails_after_acknowledgement_is_unread(self) -> None:
        employer = company(name="Late Failure")
        base_time = datetime(2026, 8, 9, 10, tzinfo=UTC)
        running = scrape_run(employer, status="running", started_at=base_time)
        acknowledged = scrape_run(
            employer,
            status="failed",
            started_at=base_time + timedelta(minutes=1),
        )
        self.client.get(
            reverse("scrape_runs:list"),
            {"acknowledge_failed_through": acknowledged.pk},
        )

        running.status = "failed"
        running.finished_at = base_time + timedelta(hours=1)
        running.duration_seconds = Decimal("3600.000")
        running.error_message = "Failed after the history acknowledgement"
        running.save(
            update_fields=(
                "status",
                "finished_at",
                "duration_seconds",
                "error_message",
            )
        )

        response = self.client.get(reverse("home"))

        assert response.context["running_runs"] == 0
        assert response.context["failed_runs"] == 1

    def test_dashboard_polls_existing_running_runs_until_terminal(self) -> None:
        employer = company(name="Polling Company")
        running = scrape_run(
            employer,
            status="running",
            started_at=datetime(2026, 8, 9, 10, tzinfo=UTC),
        )

        running_response = self.client.get("/")
        running_html = running_response.content.decode()

        assert running_response.context["running_runs"] == 1
        assert running_response.context["running_run_ids"] == [running.pk]
        assert 'id="scrape-run-polling"' in running_html
        assert 'data-status-url="/scrape-runs/status/"' in running_html
        assert 'data-interval-ms="5000"' in running_html
        assert 'src="/static/js/scrape_run_polling.js"' in running_html

        running.status = "success"
        running.finished_at = datetime(2026, 8, 9, 10, 1, tzinfo=UTC)
        running.duration_seconds = Decimal("60.000")
        running.save(update_fields=("status", "finished_at", "duration_seconds"))

        terminal_response = self.client.get("/")
        terminal_html = terminal_response.content.decode()

        assert terminal_response.context["running_runs"] == 0
        assert terminal_response.context["running_run_ids"] == []
        assert 'id="scrape-run-polling"' not in terminal_html
        assert 'src="/static/js/scrape_run_polling.js"' not in terminal_html

    def test_new_and_updated_card_counts_current_unreviewed_jobs_and_links_filter(
        self,
    ) -> None:
        employer = company(name="Review Dashboard")
        new_job = job(employer, sequence=1, status="active")
        updated_job = job(employer, sequence=2, status="active")
        updated_job.last_reviewed_content_hash = "f" * 64
        updated_job.save(update_fields=("last_reviewed_content_hash",))
        reviewed_job = job(employer, sequence=3, status="active")
        reviewed_job.last_reviewed_content_hash = reviewed_job.content_hash
        reviewed_job.save(update_fields=("last_reviewed_content_hash",))

        response = self.client.get("/")
        html = response.content.decode()

        assert response.context["new_jobs"] == 1
        assert response.context["updated_jobs"] == 1
        assert response.context["unreviewed_jobs"] == 2
        assert "New &amp; updated" in html
        assert '<p class="dashboard-review-breakdown">1 new · 1 updated</p>' in html
        assert 'href="/jobs/?review_state=unreviewed"' in html
        filtered = self.client.get(
            reverse("jobs:list"),
            {"review_state": "unreviewed"},
        )
        assert {stored.pk for stored in filtered.context["jobs"]} == {
            new_job.pk,
            updated_job.pk,
        }

    def test_latest_success_ignores_newer_partial_failed_and_running_runs(self) -> None:
        successful_company = company(name="Successful Company")
        running_company = company(name="Newer Running Company")
        base_time = datetime(2026, 8, 9, 20, 14, tzinfo=UTC)
        success = scrape_run(
            successful_company,
            status="success",
            started_at=base_time,
            finished_at=base_time + timedelta(minutes=1),
        )
        scrape_run(
            successful_company,
            status="partial",
            started_at=base_time + timedelta(hours=1),
        )
        scrape_run(
            successful_company,
            status="failed",
            started_at=base_time + timedelta(hours=2),
        )
        scrape_run(
            running_company,
            status="running",
            started_at=base_time + timedelta(hours=3),
        )

        response = self.client.get("/")
        html = response.content.decode()

        assert response.context["latest_successful_run"] == success.finished_at
        assert 'class="dashboard-latest-time"' in html
        assert "<span>9 Aug 2026</span>" in html
        assert "<span>22:15</span>" in html
        assert "<span>22:15:00</span>" not in html


@override_settings(ALLOWED_HOSTS=["testserver"])
class DashboardUpdateAllTests(TestCase):  # type: ignore[misc]
    def test_monitoring_banner_persists_while_submitted_batch_is_queued_or_running(
        self,
    ) -> None:
        company_record = company(name="Persistent Batch")
        source = company_record.sources.get()
        with patch(
            "job_monitor.views.background_executor.submit_company",
            return_value=company_submission(company_record),
        ):
            response = self.client.post(reverse("update_all"))

        queued_dashboard = self.client.get(response.url)
        repeated_queued_dashboard = self.client.get(response.url)
        running = scrape_run(
            company_record,
            company_source=source,
            status="running",
            started_at=datetime(2026, 8, 12, 9, tzinfo=UTC),
        )
        running_dashboard = self.client.get(response.url)

        for dashboard in (
            queued_dashboard,
            repeated_queued_dashboard,
            running_dashboard,
        ):
            assert b"Monitoring started for 1 source." in dashboard.content
            assert 'class="message message-success"' in dashboard.content.decode()
            assert 'id="scrape-run-polling"' in dashboard.content.decode()
        assert running_dashboard.context["running_run_ids"] == [running.pk]

    def test_monitoring_banner_persists_across_multiple_concurrent_runs(self) -> None:
        company_record = company(name="Concurrent Batch")
        first_source = company_record.sources.get()
        second_source = model("companies.CompanySource").objects.create(
            company=company_record,
            source="fixture",
            source_jobs_url="https://jobs.example.test/concurrent-batch/secondary",
            approval_status="approved",
            is_active=True,
        )
        submission = type(
            "Submission",
            (),
            {
                "submitted_source_ids": (first_source.pk, second_source.pk),
                "already_running_source_ids": (),
                "failed_source_ids": (),
            },
        )()
        with patch(
            "job_monitor.views.background_executor.submit_company",
            return_value=submission,
        ):
            response = self.client.post(reverse("update_all"))

        started_at = datetime(2026, 8, 12, 10, tzinfo=UTC)
        first_run = scrape_run(
            company_record,
            company_source=first_source,
            status="running",
            started_at=started_at,
        )
        scrape_run(
            company_record,
            company_source=second_source,
            status="running",
            started_at=started_at,
        )
        both_running = self.client.get(response.url)

        first_run.status = "success"
        first_run.finished_at = started_at + timedelta(minutes=1)
        first_run.duration_seconds = Decimal("60.000")
        first_run.save(update_fields=("status", "finished_at", "duration_seconds"))
        one_running = self.client.get(response.url)

        assert both_running.context["running_runs"] == 2
        assert one_running.context["running_runs"] == 1
        assert b"Monitoring started for 2 sources." in both_running.content
        assert b"Monitoring started for 2 sources." in one_running.content

    def test_monitoring_banner_disappears_after_every_batch_run_is_terminal(
        self,
    ) -> None:
        company_record = company(name="Completed Batch")
        with patch(
            "job_monitor.views.background_executor.submit_company",
            return_value=company_submission(company_record),
        ):
            response = self.client.post(reverse("update_all"))

        scrape_run(
            company_record,
            status="success",
            started_at=datetime(2026, 8, 12, 11, tzinfo=UTC),
        )
        completed_dashboard = self.client.get(response.url)
        refreshed_dashboard = self.client.get(response.url)

        assert b"Monitoring started for 1 source." not in completed_dashboard.content
        assert b"Monitoring started for 1 source." not in refreshed_dashboard.content
        assert "monitoring_batch_banner" not in self.client.session

    def test_unrelated_dashboard_refresh_does_not_create_monitoring_banner(self) -> None:
        company_record = company(name="Unrelated Running")
        scrape_run(
            company_record,
            status="running",
            started_at=datetime(2026, 8, 12, 12, tzinfo=UTC),
        )

        dashboard = self.client.get(reverse("home"))

        assert dashboard.context["running_runs"] == 1
        assert dashboard.context["monitoring_batch_banner"] is None
        assert b"Monitoring started for" not in dashboard.content

    def test_update_all_requires_post_and_get_submits_nothing(self) -> None:
        company(name="GET Company")

        with patch("job_monitor.views.background_executor.submit_company") as submit:
            response = self.client.get(reverse("update_all"))

        assert response.status_code == 405
        submit.assert_not_called()

    def test_update_all_submits_each_active_company_and_skips_inactive(self) -> None:
        first = company(name="First Active")
        inactive = company(name="Inactive", is_active=False)
        second = company(name="Second Active")

        with patch(
            "job_monitor.views.background_executor.submit_company",
            side_effect=lambda *, company: company_submission(company),
        ) as submit:
            response = self.client.post(reverse("update_all"))

        assert response.status_code == 302
        assert response.url == reverse("home")
        assert [call.kwargs["company"].pk for call in submit.call_args_list] == [
            first.pk,
            second.pk,
        ]
        assert inactive.pk not in {call.kwargs["company"].pk for call in submit.call_args_list}
        assert b"Monitoring started for 2 sources." in self.client.get(response.url).content

    def test_update_all_counts_every_submitted_source_for_one_company(self) -> None:
        company_record = company(name="Multi Source")
        first_source = company_record.sources.get()
        second_source = model("companies.CompanySource").objects.create(
            company=company_record,
            source="fixture",
            source_jobs_url="https://jobs.example.test/multi-source/secondary",
            approval_status="approved",
            is_active=True,
        )
        submission = type(
            "Submission",
            (),
            {
                "submitted_source_ids": (first_source.pk, second_source.pk),
                "already_running_source_ids": (),
                "failed_source_ids": (),
            },
        )()

        with patch(
            "job_monitor.views.background_executor.submit_company",
            return_value=submission,
        ) as submit:
            response = self.client.post(reverse("update_all"))

        submit.assert_called_once_with(company=company_record)
        assert b"Monitoring started for 2 sources." in self.client.get(response.url).content

    def test_update_all_submits_active_darwinbox_source_without_network(self) -> None:
        company_record = company(name="Darwinbox Update All")
        company_record.sources.all().delete()
        darwinbox_source = model("companies.CompanySource").objects.create(
            company=company_record,
            source="darwinbox",
            source_jobs_url="https://tenant.darwinbox.com/ms/candidate/careers",
            approval_status="approved",
            is_active=True,
        )

        submission = type(
            "Submission",
            (),
            {
                "submitted_source_ids": (darwinbox_source.pk,),
                "already_running_source_ids": (),
                "failed_source_ids": (),
            },
        )()
        with patch(
            "job_monitor.views.background_executor.submit_company",
            return_value=submission,
        ) as submit:
            response = self.client.post(reverse("update_all"))

        assert response.status_code == 302
        assert b"Monitoring started for 1 source." in self.client.get(response.url).content
        submit.assert_called_once_with(company=company_record)
        assert model("scrape_runs.ScrapeRun").objects.count() == 0
        darwinbox_source.refresh_from_db()
        assert darwinbox_source.is_active is True

    def test_running_company_is_skipped_without_blocking_other_companies(self) -> None:
        running_company = company(name="Already Running")
        eligible_company = company(name="Eligible")
        scrape_run(
            running_company,
            status="running",
            started_at=datetime(2026, 8, 9, 10, tzinfo=UTC),
        )

        def submit_company(*, company: Any) -> Any:
            return company_submission(
                company,
                submitted=company.pk == eligible_company.pk,
                already_running=company.pk == running_company.pk,
            )

        with patch(
            "job_monitor.views.background_executor.submit_company",
            side_effect=submit_company,
        ) as submit:
            response = self.client.post(reverse("update_all"))

        assert [call.kwargs["company"].pk for call in submit.call_args_list] == [
            running_company.pk,
            eligible_company.pk,
        ]
        html = self.client.get(response.url).content
        assert b"Monitoring started for 1 source. 1 already running." in html
        assert (
            model("scrape_runs.ScrapeRun")
            .objects.filter(
                company=running_company,
                status="running",
            )
            .count()
            == 1
        )

    def test_executor_duplicate_is_skipped_and_next_company_is_submitted(self) -> None:
        duplicate_company = company(name="Executor Duplicate")
        eligible_company = company(name="Executor Eligible")

        def submit_company(*, company: Any) -> Any:
            return company_submission(
                company,
                submitted=company.pk == eligible_company.pk,
                already_running=company.pk == duplicate_company.pk,
            )

        with patch(
            "job_monitor.views.background_executor.submit_company",
            side_effect=submit_company,
        ) as submit:
            response = self.client.post(reverse("update_all"))

        assert [call.kwargs["company"].pk for call in submit.call_args_list] == [
            duplicate_company.pk,
            eligible_company.pk,
        ]
        assert (
            b"Monitoring started for 1 source. 1 already running."
            in self.client.get(response.url).content
        )

    def test_controlled_source_error_does_not_block_next_company(self) -> None:
        unsupported_company = company(name="Unsupported")
        eligible_company = company(name="Supported")
        source_error = importlib.import_module("scraping.background").BackgroundSourceError

        def submit_company(*, company: Any) -> Any:
            if company.pk == unsupported_company.pk:
                raise source_error("unknown source")
            return company_submission(company)

        with patch(
            "job_monitor.views.background_executor.submit_company",
            side_effect=submit_company,
        ) as submit:
            response = self.client.post(reverse("update_all"))

        assert response.status_code == 302
        assert response.url == reverse("home")
        assert [call.kwargs["company"].pk for call in submit.call_args_list] == [
            unsupported_company.pk,
            eligible_company.pk,
        ]
        assert b"Monitoring started for 1 source. 1 could not be started." in (
            self.client.get(response.url).content
        )

    def test_update_all_handles_no_active_companies(self) -> None:
        company(name="Inactive Only", is_active=False)

        with patch("job_monitor.views.background_executor.submit_company") as submit:
            response = self.client.post(reverse("update_all"))

        assert response.status_code == 302
        assert response.url == reverse("home")
        submit.assert_not_called()
        assert b"No active companies to update." in self.client.get(response.url).content
