from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_review_hash_migration_backfills_existing_jobs_but_not_new_jobs(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "job-review-backfill.sqlite3"
    environment = os.environ.copy()
    environment["DJANGO_SETTINGS_MODULE"] = "job_monitor.settings"
    environment["JOB_MONITOR_SQLITE_PATH"] = str(database_path)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                """
                import django
                django.setup()

                from django.db import connection
                from django.db.migrations.executor import MigrationExecutor

                old_target = [("jobs", "0003_remove_jobposting_uniq_job_dedupe_key_and_more")]
                new_target = [("jobs", "0004_jobposting_last_reviewed_content_hash")]

                executor = MigrationExecutor(connection)
                executor.migrate(old_target)
                old_apps = executor.loader.project_state(old_target).apps
                Company = old_apps.get_model("companies", "Company")
                CompanySource = old_apps.get_model("companies", "CompanySource")
                JobPosting = old_apps.get_model("jobs", "JobPosting")

                company = Company.objects.create(name="Existing Review State")
                source = CompanySource.objects.create(
                    company=company,
                    source="fixture",
                    source_jobs_url="https://jobs.example.test/existing",
                    approval_status="approved",
                    is_active=True,
                )
                existing = JobPosting.objects.create(
                    company=company,
                    company_source=source,
                    source="fixture",
                    source_job_id="existing-job",
                    content_hash="a" * 64,
                    dedupe_key="b" * 64,
                )

                executor = MigrationExecutor(connection)
                executor.migrate(new_target)
                new_apps = executor.loader.project_state(new_target).apps
                JobPosting = new_apps.get_model("jobs", "JobPosting")

                migrated = JobPosting.objects.get(pk=existing.pk)
                assert migrated.last_reviewed_content_hash == migrated.content_hash

                new_job = JobPosting.objects.create(
                    company_id=company.pk,
                    company_source_id=source.pk,
                    source="fixture",
                    source_job_id="new-job",
                    content_hash="c" * 64,
                    dedupe_key="d" * 64,
                )
                assert new_job.last_reviewed_content_hash is None
                """
            ),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
