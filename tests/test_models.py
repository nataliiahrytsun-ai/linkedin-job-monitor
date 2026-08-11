from __future__ import annotations

import importlib
import os
from typing import Any, cast

import pytest


@pytest.fixture(scope="module", autouse=True)
def migrated_database(tmp_path_factory: pytest.TempPathFactory) -> None:
    database_path = tmp_path_factory.mktemp("model-db") / "models.sqlite3"
    os.environ["DJANGO_SETTINGS_MODULE"] = "job_monitor.settings"
    os.environ["JOB_MONITOR_SQLITE_PATH"] = str(database_path)
    django = importlib.import_module("django")
    django.setup()
    management = importlib.import_module("django.core.management")
    management.call_command("migrate", interactive=False, verbosity=0)


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None) -> None:
    management = importlib.import_module("django.core.management")
    management.call_command("flush", interactive=False, verbosity=0)


def model(name: str) -> Any:
    apps = importlib.import_module("django.apps").apps
    return apps.get_model(name)


def exception(name: str) -> type[BaseException]:
    if name == "ValidationError":
        exception_type = importlib.import_module("django.core.exceptions").ValidationError
        return cast("type[BaseException]", exception_type)
    if name == "ProtectedError":
        exception_type = importlib.import_module("django.db.models.deletion").ProtectedError
        return cast("type[BaseException]", exception_type)
    exception_type = importlib.import_module("django.db").IntegrityError
    return cast("type[BaseException]", exception_type)


def atomic() -> Any:
    return importlib.import_module("django.db").transaction.atomic()


def create_company(**overrides: object) -> Any:
    values: dict[str, object] = {
        "name": "Example Company",
        "source": "customer_feed",
    }
    values.update(overrides)
    return model("companies.Company").objects.create(**values)


def create_company_source(company: Any, **overrides: object) -> Any:
    values: dict[str, object] = {
        "company": company,
        "source": company.source,
        "source_jobs_url": "https://feed.example/jobs",
    }
    values.update(overrides)
    return model("companies.CompanySource").objects.create(**values)


def create_job(company: Any, **overrides: object) -> Any:
    values: dict[str, object] = {
        "company": company,
        "source": company.source,
        "source_job_id": "job-1",
        "content_hash": "a" * 64,
        "dedupe_key": "b" * 64,
    }
    values.update(overrides)
    return model("jobs.JobPosting").objects.create(**values)


def create_terminal_run(company: Any, **overrides: object) -> Any:
    timezone = importlib.import_module("django.utils.timezone")
    values: dict[str, object] = {
        "company": company,
        "status": "success",
        "finished_at": timezone.now(),
        "duration_seconds": "1.250",
    }
    values.update(overrides)
    return model("scrape_runs.ScrapeRun").objects.create(**values)


def test_company_defaults_choices_nullable_url_and_source_normalization() -> None:
    company = create_company(source="  CUSTOMER_FEED  ")

    assert company.source == "customer_feed"
    assert company.source_jobs_url is None
    assert company.company_type == "other"
    assert company.is_active is True
    assert company.last_scraped_at is None
    assert company.last_scrape_status == "never"
    assert {value for value, _label in company.CompanyType.choices} == {
        "client",
        "supplier",
        "other",
    }


def test_company_url_uniqueness_is_conditional() -> None:
    create_company(name="No URL One", source_jobs_url=None)
    create_company(name="No URL Two", source_jobs_url=None)
    create_company(name="Empty URL", source_jobs_url="")
    url = "https://feed.example/jobs"
    create_company(name="URL One", source_jobs_url=url)

    with pytest.raises(exception("IntegrityError")), atomic():
        create_company(name="URL Duplicate", source_jobs_url=url)


def test_company_source_defaults_choices_and_source_normalization() -> None:
    company_source = create_company_source(
        create_company(source="lever"),
        source="  LeVeR  ",
    )

    assert company_source.source == "lever"
    assert company_source.approval_status == "discovered"
    assert company_source.is_active is False
    assert {value for value, _label in company_source.ApprovalStatus.choices} == {
        "discovered",
        "needs_review",
        "approved",
        "blocked",
        "rejected",
    }


def test_active_company_source_requires_approved_status_and_nonempty_key() -> None:
    company = create_company(source="lever")

    with pytest.raises(exception("IntegrityError")), atomic():
        create_company_source(company, approval_status="needs_review", is_active=True)

    with pytest.raises(exception("IntegrityError")), atomic():
        create_company_source(
            company,
            source="",
            source_jobs_url="https://feed.example/blank",
            approval_status="approved",
        )

    approved = create_company_source(
        company,
        source_jobs_url="https://feed.example/approved",
        approval_status="approved",
        is_active=True,
    )
    assert approved.is_active is True


def test_exact_company_source_configuration_is_unique_without_global_locking() -> None:
    first_company = create_company(name="First", source="lever")
    second_company = create_company(name="Second", source="lever")
    url = "https://jobs.lever.co/shared"
    create_company_source(first_company, source_jobs_url=url)

    with pytest.raises(exception("IntegrityError")), atomic():
        create_company_source(first_company, source_jobs_url=url)

    other_owner = create_company_source(second_company, source_jobs_url=url)
    assert other_owner.pk is not None


def test_job_nullable_fields_defaults_and_choices() -> None:
    job = create_job(create_company())

    nullable_fields = (
        "title",
        "country",
        "city",
        "location",
        "workplace_type",
        "employment_type",
        "seniority_level",
        "job_function",
        "industry",
        "published_at",
        "description",
        "source_job_url",
    )
    assert all(getattr(job, field) is None for field in nullable_fields)
    assert job.status == "active"
    assert job.company_source is None
    assert job.consecutive_successful_misses == 0
    assert {value for value, _label in job.WorkplaceType.choices} == {
        "remote",
        "hybrid",
        "onsite",
    }


def test_job_source_is_normalized_and_immutable() -> None:
    job = create_job(create_company(source="api"), source="  API  ")
    assert job.source == "api"

    job.source = "other"
    with pytest.raises(exception("ValidationError"), match="cannot be changed"):
        job.save()


def test_job_requires_source_id_or_url() -> None:
    with pytest.raises(exception("ValidationError"), match="requires"):
        create_job(
            create_company(),
            source_job_id=None,
            source_job_url=None,
        )


def test_job_dedupe_key_is_unique_within_company_and_source() -> None:
    company = create_company()
    create_job(company, source_job_id="one", dedupe_key="c" * 64)

    with pytest.raises(exception("IntegrityError")), atomic():
        create_job(company, source_job_id="two", dedupe_key="c" * 64)

    create_job(
        create_company(name="Other Company"),
        source_job_id="two",
        dedupe_key="c" * 64,
    )


def test_source_job_id_uniqueness_is_conditional() -> None:
    company = create_company()
    create_job(company, source_job_id="duplicate", dedupe_key="d" * 64)

    with pytest.raises(exception("IntegrityError")), atomic():
        create_job(company, source_job_id="duplicate", dedupe_key="e" * 64)

    create_job(
        company,
        source_job_id=None,
        source_job_url="https://feed.example/jobs/one",
        dedupe_key="f" * 64,
    )
    create_job(
        company,
        source_job_id=None,
        source_job_url="https://feed.example/jobs/two",
        dedupe_key="0" * 64,
    )


def test_company_delete_is_protected_by_jobs() -> None:
    company = create_company()
    create_job(company)

    with pytest.raises(exception("ProtectedError")):
        company.delete()


def test_company_delete_is_protected_by_scrape_runs() -> None:
    company = create_company()
    create_terminal_run(company)

    with pytest.raises(exception("ProtectedError")):
        company.delete()


def test_only_one_running_run_is_allowed_per_company() -> None:
    company = create_company()
    model("scrape_runs.ScrapeRun").objects.create(company=company)

    with pytest.raises(exception("IntegrityError")), atomic():
        model("scrape_runs.ScrapeRun").objects.create(company=company)

    create_terminal_run(company)


def test_scrape_run_terminal_fields_are_consistent() -> None:
    company = create_company()
    timezone = importlib.import_module("django.utils.timezone")
    now = timezone.now()

    with pytest.raises(exception("IntegrityError")), atomic():
        model("scrape_runs.ScrapeRun").objects.create(
            company=company,
            status="running",
            finished_at=now,
            duration_seconds="1.000",
        )

    with pytest.raises(exception("IntegrityError")), atomic():
        model("scrape_runs.ScrapeRun").objects.create(
            company=company,
            status="failed",
        )

    run = create_terminal_run(company, status="partial")
    assert run.finished_at is not None
    assert run.duration_seconds is not None


def test_scrape_run_counter_defaults() -> None:
    company = create_company()
    run = model("scrape_runs.ScrapeRun").objects.create(company=company)

    assert run.finished_at is None
    assert run.duration_seconds is None
    assert run.status == "running"
    assert run.jobs_found == 0
    assert run.jobs_created == 0
    assert run.jobs_updated == 0
    assert run.requests_made == 0
    assert run.error_message == ""
    assert run.company_source is None


def test_running_scrape_run_uniqueness_is_scoped_to_company_source() -> None:
    company = create_company()
    first_source = create_company_source(
        company,
        approval_status="approved",
        is_active=True,
    )
    second_source = create_company_source(
        company,
        source_jobs_url="https://feed.example/secondary-jobs",
        approval_status="approved",
        is_active=True,
    )

    model("scrape_runs.ScrapeRun").objects.create(
        company=company,
        company_source=first_source,
    )
    second_run = model("scrape_runs.ScrapeRun").objects.create(
        company=company,
        company_source=second_source,
    )

    assert second_run.status == "running"
    with pytest.raises(exception("IntegrityError")), atomic():
        model("scrape_runs.ScrapeRun").objects.create(
            company=company,
            company_source=first_source,
        )


def test_legacy_running_scrape_run_uniqueness_remains_company_scoped() -> None:
    company = create_company()
    model("scrape_runs.ScrapeRun").objects.create(company=company)

    with pytest.raises(exception("IntegrityError")), atomic():
        model("scrape_runs.ScrapeRun").objects.create(company=company)


def test_initial_migrations_created_all_model_tables() -> None:
    connection = importlib.import_module("django.db").connection
    tables = set(connection.introspection.table_names())

    assert {
        "companies_company",
        "companies_companysource",
        "jobs_jobposting",
        "scrape_runs_scraperun",
    } <= tables
