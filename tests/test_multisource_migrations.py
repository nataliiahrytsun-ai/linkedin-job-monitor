from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_migration_scenario(*, code: str, database_path: Path) -> None:
    environment = os.environ.copy()
    environment["DJANGO_SETTINGS_MODULE"] = "job_monitor.settings"
    environment["JOB_MONITOR_SQLITE_PATH"] = str(database_path)
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_company_source_data_migration_backfills_unambiguous_legacy_provenance(
    tmp_path: Path,
) -> None:
    _run_migration_scenario(
        database_path=tmp_path / "multisource-backfill.sqlite3",
        code="""
            import django
            django.setup()

            from django.db import connection
            from django.db.migrations.executor import MigrationExecutor
            from django.utils import timezone

            old_targets = [
                ("companies", "0001_initial"),
                ("jobs", "0001_initial"),
                ("scrape_runs", "0001_initial"),
            ]
            new_target = [("companies", "0003_backfill_company_sources")]

            executor = MigrationExecutor(connection)
            executor.migrate(old_targets)
            old_apps = executor.loader.project_state(old_targets).apps
            Company = old_apps.get_model("companies", "Company")
            JobPosting = old_apps.get_model("jobs", "JobPosting")
            ScrapeRun = old_apps.get_model("scrape_runs", "ScrapeRun")

            olo = Company.objects.create(
                name="Olo",
                source="lever",
                source_jobs_url="https://jobs.lever.co/olo",
            )
            fixture_company = Company.objects.create(
                name="Acme GmbH",
                source="fixture",
                source_jobs_url="https://jobs.example.test/acme",
            )
            inactive = Company.objects.create(
                name="Inactive Lever",
                source="lever",
                source_jobs_url="https://jobs.lever.co/inactive",
                is_active=False,
            )
            unknown = Company.objects.create(
                name="Unknown",
                source="unreviewed_ats",
                source_jobs_url="https://careers.example.test/jobs",
            )
            job = JobPosting.objects.create(
                company=olo,
                source="lever",
                source_job_id="lever-1",
                content_hash="a" * 64,
                dedupe_key="b" * 64,
            )
            run = ScrapeRun.objects.create(
                company=fixture_company,
                status="success",
                finished_at=timezone.now(),
                duration_seconds="0.050",
            )

            executor = MigrationExecutor(connection)
            executor.migrate(new_target)
            new_apps = executor.loader.project_state(new_target).apps
            Company = new_apps.get_model("companies", "Company")
            CompanySource = new_apps.get_model("companies", "CompanySource")
            JobPosting = new_apps.get_model("jobs", "JobPosting")
            ScrapeRun = new_apps.get_model("scrape_runs", "ScrapeRun")

            assert CompanySource.objects.count() == 4
            olo_source = CompanySource.objects.get(company_id=olo.pk)
            assert olo_source.source == "lever"
            assert olo_source.source_jobs_url == "https://jobs.lever.co/olo"
            assert olo_source.approval_status == "approved"
            assert olo_source.is_active is True

            fixture_source = CompanySource.objects.get(company_id=fixture_company.pk)
            assert fixture_source.source == "fixture"
            assert fixture_source.approval_status == "approved"
            assert fixture_source.is_active is True

            inactive_source = CompanySource.objects.get(company_id=inactive.pk)
            assert Company.objects.get(pk=inactive.pk).is_active is False
            assert inactive_source.approval_status == "approved"
            assert inactive_source.is_active is True

            unknown_source = CompanySource.objects.get(company_id=unknown.pk)
            assert unknown_source.source == "unreviewed_ats"
            assert unknown_source.approval_status == "needs_review"
            assert unknown_source.is_active is False

            stored_job = JobPosting.objects.get(pk=job.pk)
            stored_run = ScrapeRun.objects.get(pk=run.pk)
            assert stored_job.company_source_id == olo_source.pk
            assert stored_run.company_source_id == fixture_source.pk

            stored_olo = Company.objects.get(pk=olo.pk)
            assert stored_olo.source == "lever"
            assert stored_olo.source_jobs_url == "https://jobs.lever.co/olo"
        """,
    )


def test_company_source_data_migration_fails_closed_on_job_source_mismatch(
    tmp_path: Path,
) -> None:
    _run_migration_scenario(
        database_path=tmp_path / "multisource-mismatch.sqlite3",
        code="""
            import django
            django.setup()

            from django.db import connection
            from django.db.migrations.executor import MigrationExecutor

            old_targets = [
                ("companies", "0001_initial"),
                ("jobs", "0001_initial"),
                ("scrape_runs", "0001_initial"),
            ]
            new_target = [("companies", "0003_backfill_company_sources")]

            executor = MigrationExecutor(connection)
            executor.migrate(old_targets)
            old_apps = executor.loader.project_state(old_targets).apps
            Company = old_apps.get_model("companies", "Company")
            JobPosting = old_apps.get_model("jobs", "JobPosting")

            company = Company.objects.create(
                name="Mismatched",
                source="lever",
                source_jobs_url="https://jobs.lever.co/mismatched",
            )
            JobPosting.objects.create(
                company=company,
                source="fixture",
                source_job_id="wrong-source",
                content_hash="c" * 64,
                dedupe_key="d" * 64,
            )

            executor = MigrationExecutor(connection)
            try:
                executor.migrate(new_target)
            except RuntimeError as error:
                assert "JobPosting provenance mismatch" in str(error)
                assert "'fixture'" in str(error)
                assert "'lever'" in str(error)
            else:
                raise AssertionError("migration accepted ambiguous job provenance")

            executor = MigrationExecutor(connection)
            dependency_targets = [
                ("companies", "0002_companysource"),
                ("jobs", "0002_jobposting_company_source"),
                ("scrape_runs", "0002_scraperun_company_source"),
            ]
            dependency_apps = executor.loader.project_state(dependency_targets).apps
            CompanySource = dependency_apps.get_model("companies", "CompanySource")
            JobPosting = dependency_apps.get_model("jobs", "JobPosting")
            assert CompanySource.objects.count() == 0
            assert JobPosting.objects.get().company_source_id is None
        """,
    )
