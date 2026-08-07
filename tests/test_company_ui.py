from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest


@pytest.fixture(scope="module", autouse=True)
def migrated_database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    apps = importlib.import_module("django.apps").apps
    if not apps.ready:
        database_path = tmp_path_factory.mktemp("company-ui-db") / "companies.sqlite3"
        os.environ["DJANGO_SETTINGS_MODULE"] = "job_monitor.settings"
        os.environ["JOB_MONITOR_SQLITE_PATH"] = str(database_path)
        importlib.import_module("django").setup()
    management = importlib.import_module("django.core.management")
    management.call_command("migrate", interactive=False, verbosity=0)
    test_utils = importlib.import_module("django.test.utils")
    test_utils.setup_test_environment()
    yield
    test_utils.teardown_test_environment()


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None) -> None:
    importlib.import_module("django.core.management").call_command(
        "flush", interactive=False, verbosity=0
    )


def model(name: str) -> Any:
    return importlib.import_module("django.apps").apps.get_model(name)


def client() -> Any:
    return importlib.import_module("django.test").Client()


def reverse(name: str, *, args: tuple[object, ...] = ()) -> str:
    return str(importlib.import_module("django.urls").reverse(name, args=args))


def create_company(**overrides: object) -> Any:
    values: dict[str, object] = {
        "name": "Example Company",
        "source": "fixture",
        "source_jobs_url": "https://jobs.example.test/example/openings",
    }
    values.update(overrides)
    return model("companies.Company").objects.create(**values)


def create_job(company: Any, **overrides: object) -> Any:
    sequence = model("jobs.JobPosting").objects.count() + 1
    values: dict[str, object] = {
        "company": company,
        "source": company.source,
        "source_job_id": f"job-{sequence}",
        "title": f"Role {sequence}",
        "content_hash": f"{sequence:064x}",
        "dedupe_key": f"{sequence + 100:064x}",
    }
    values.update(overrides)
    return model("jobs.JobPosting").objects.create(**values)


def valid_company_data(**overrides: str) -> dict[str, str]:
    values = {
        "name": "New Company",
        "company_type": "client",
        "source": "fixture",
        "source_jobs_url": "https://jobs.example.test/new/openings",
        "is_active": "on",
    }
    values.update(overrides)
    return values


def test_company_list_empty_state_navigation_and_template_contract() -> None:
    response = client().get(reverse("companies:list"))
    html = response.content.decode()

    assert response.status_code == 200
    assert "companies/company_list.html" in [
        template.name for template in response.templates if template.name
    ]
    assert "No companies yet" in html
    assert "Add company" in html
    assert html.count(f'href="{reverse("companies:create")}"') == 1
    assert "Add first company" not in html
    assert f'href="{reverse("companies:list")}"' in html
    assert 'href="/jobs/' not in html
    assert 'href="/scrape-runs/' not in html
    assert '<meta name="viewport"' in html
    assert '<link rel="stylesheet" href="/static/css/app.css">' in html
    assert html.count("<h1") == 1


def test_company_list_shows_records_statuses_and_stable_name_order() -> None:
    zulu = create_company(name="Zulu", is_active=False, last_scrape_status="failed")
    beta = create_company(
        name="Beta",
        company_type="client",
        source_jobs_url="https://jobs.example.test/beta/openings",
    )
    alpha = create_company(
        name="Alpha",
        source_jobs_url="https://jobs.example.test/alpha/openings",
        company_type="supplier",
    )

    html = client().get(reverse("companies:list")).content.decode()

    assert html.index("Alpha") < html.index("Zulu")
    assert html.index("Alpha") < html.index("Beta") < html.index("Zulu")
    assert "Customer" in html
    assert "Supplier" in html
    assert "Other" in html
    assert "Kunde" not in html
    assert "Sonstige" not in html
    assert "ACTIVE" in html
    assert "INACTIVE" in html
    assert "Failed" in html
    assert "Add company" in html
    assert html.count(f'href="{reverse("companies:create")}"') == 1
    assert f'href="{reverse("companies:detail", args=(alpha.pk,))}"' in html
    assert f'href="{reverse("companies:detail", args=(beta.pk,))}"' in html
    assert f'href="{reverse("companies:detail", args=(zulu.pk,))}"' in html
    assert "Edit" in html
    assert "Activate" in html
    assert "Deactivate" in html


def test_company_detail_renders_information_navigation_and_empty_states() -> None:
    company = create_company(
        name="Acuity Analytics",
        company_type="client",
        last_scrape_status="never",
    )

    response = client().get(reverse("companies:detail", args=(company.pk,)))
    html = response.content.decode()

    assert response.status_code == 200
    assert "companies/company_detail.html" in [
        template.name for template in response.templates if template.name
    ]
    assert html.count("<h1") == 1
    assert "Acuity Analytics" in html
    assert html.count("Customer") == 1
    assert "Kunde" not in html
    assert "Sonstige" not in html
    assert "fixture" in html
    assert "ACTIVE" in html
    assert "Not run yet" in html
    assert "Never" in html
    assert "No jobs found yet" in html
    assert "Update jobs" in html
    assert "Jobs aktualisieren" not in html
    assert 'type="button" disabled' in html
    assert 'class="page-heading detail-header"' in html
    assert 'class="company-meta"' in html
    assert "Company type" not in html
    assert "Monitoring status" not in html
    for label in (
        "Vacancy source",
        "Source jobs URL",
        "Last run status",
        "Last run time",
        "Active jobs",
    ):
        assert label in html
    assert f'href="{reverse("companies:list")}"' in html
    assert f'href="{reverse("companies:edit", args=(company.pk,))}"' in html
    assert f'action="{reverse("companies:toggle_active", args=(company.pk,))}"' in html
    assert 'method="post"' in html
    assert '<meta name="viewport"' in html
    assert 'class="table-scroll"' not in html


def test_company_detail_shows_safe_source_link_and_missing_url_fallback() -> None:
    configured = create_company(name="Configured")
    missing = create_company(
        name="Missing URL",
        source="another-fixture",
        source_jobs_url=None,
        is_active=False,
    )

    configured_html = client().get(
        reverse("companies:detail", args=(configured.pk,))
    ).content.decode()
    missing_html = client().get(
        reverse("companies:detail", args=(missing.pk,))
    ).content.decode()

    assert 'href="https://jobs.example.test/example/openings"' in configured_html
    assert 'target="_blank"' in configured_html
    assert 'rel="noopener noreferrer"' in configured_html
    assert ">https://jobs.example.test/example/openings</a>" in configured_html
    assert "Not configured" in missing_html
    assert "INACTIVE" in missing_html
    assert "Other" in missing_html
    assert "Sonstige" not in missing_html


def test_company_detail_scopes_jobs_counts_only_active_and_renders_fields() -> None:
    company = create_company(name="Visible Company")
    other_company = create_company(
        name="Other Company",
        source_jobs_url="https://jobs.example.test/other/openings",
    )
    older = create_job(
        company,
        source_job_id="active-job",
        title="Current Analyst",
        location="Vienna",
        country="Austria",
        published_at=datetime(2026, 8, 1, 9, tzinfo=UTC),
        last_seen_at=datetime(2026, 8, 2, 9, tzinfo=UTC),
        source_job_url="https://jobs.example.test/jobs/active",
        status="active",
    )
    newer = create_job(
        company,
        source_job_id="missing-job",
        title="Former Engineer",
        location=None,
        country=None,
        published_at=None,
        last_seen_at=datetime(2026, 8, 3, 9, tzinfo=UTC),
        source_job_url=None,
        status="not_found",
    )
    create_job(company, source_job_id="closed-job", title="Closed Role", status="closed")
    create_job(other_company, source_job_id="hidden-job", title="Hidden Other Job")

    response = client().get(reverse("companies:detail", args=(company.pk,)))
    html = response.content.decode()

    assert response.status_code == 200
    assert response.context["active_job_count"] == 1
    assert response.context["jobs"].count() == 3
    assert html.index(newer.title) < html.index(older.title)
    assert "Current Analyst" in html
    assert "Vienna" in html
    assert "Austria" in html
    assert "2026-08-01" in html
    assert "ACTIVE" in html
    assert "NOT_FOUND" in html
    assert "CLOSED" in html
    assert 'href="https://jobs.example.test/jobs/active"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html
    assert "Hidden Other Job" not in html
    assert older.source_job_id not in html
    assert older.content_hash not in html
    assert older.dedupe_key not in html
    assert "None" not in html
    assert 'class="table-scroll"' in html


def test_company_detail_get_is_read_only_and_starts_no_execution() -> None:
    company = create_company(
        last_scrape_status="success",
        last_scraped_at=datetime(2026, 8, 6, 12, tzinfo=UTC),
    )
    job = create_job(company)
    company_before = model("companies.Company").objects.values().get(pk=company.pk)
    job_before = model("jobs.JobPosting").objects.values().get(pk=job.pk)

    with (
        patch("scraping.pipeline.run_fixture_pipeline") as pipeline,
        patch(
            "scraping.background.ControlledBackgroundExecutor.submit_fixture_pipeline"
        ) as background_submit,
    ):
        response = client().get(reverse("companies:detail", args=(company.pk,)))

    assert response.status_code == 200
    assert "SUCCESS" in response.content.decode()
    assert "2026-08-06 12:00" in response.content.decode()
    assert model("companies.Company").objects.values().get(pk=company.pk) == company_before
    assert model("jobs.JobPosting").objects.values().get(pk=job.pk) == job_before
    assert model("scrape_runs.ScrapeRun").objects.count() == 0
    pipeline.assert_not_called()
    background_submit.assert_not_called()


def test_unknown_company_detail_returns_not_found() -> None:
    response = client().get(reverse("companies:detail", args=(999_999,)))

    assert response.status_code == 404


def test_company_list_get_does_not_change_database() -> None:
    company = create_company()
    before = model("companies.Company").objects.values().get(pk=company.pk)

    response = client().get(reverse("companies:list"))

    assert response.status_code == 200
    assert model("companies.Company").objects.values().get(pk=company.pk) == before


def test_create_get_has_csrf_labels_and_only_user_managed_fields() -> None:
    response = client().get(reverse("companies:create"))
    html = response.content.decode()

    assert response.status_code == 200
    assert 'name="csrfmiddlewaretoken"' in html
    for label in (
        "Company name",
        "Company type",
        "Vacancy source",
        "Source jobs URL",
        "Monitoring active",
    ):
        assert label in html
    for excluded in ("last_scrape_status", "last_scraped_at", "created_at", "updated_at"):
        assert f'name="{excluded}"' not in html
    assert '<option value="client">Customer</option>' in html
    assert '<option value="supplier">Supplier</option>' in html
    assert '<option value="other" selected>Other</option>' in html
    assert "Kunde" not in html
    assert "Sonstige" not in html
    assert html.count("<h1") == 1
    assert model("companies.Company").objects.count() == 0


def test_valid_create_uses_redirect_normalizes_source_and_refresh_does_not_duplicate() -> None:
    browser = client()
    response = browser.post(
        reverse("companies:create"),
        valid_company_data(source="  FIXTURE  "),
    )

    assert response.status_code == 302
    assert response.url == reverse("companies:list")
    company = model("companies.Company").objects.get()
    assert company.source == "fixture"
    assert company.company_type == "client"

    redirected = browser.get(response.url)
    assert b"was added" in redirected.content
    assert model("companies.Company").objects.count() == 1


def test_invalid_create_preserves_values_shows_errors_and_creates_nothing() -> None:
    response = client().post(
        reverse("companies:create"),
        valid_company_data(name="", source="fixture-preserved"),
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert "This field is required" in html
    assert 'value="fixture-preserved"' in html
    assert model("companies.Company").objects.count() == 0


def test_duplicate_constraint_is_displayed_as_form_error() -> None:
    create_company()

    response = client().post(
        reverse("companies:create"),
        valid_company_data(
            source="  FIXTURE  ",
            source_jobs_url="https://jobs.example.test/example/openings",
        ),
    )

    assert response.status_code == 200
    assert response.context["form"].errors
    assert model("companies.Company").objects.count() == 1


def test_edit_get_shows_current_values_without_changing_company() -> None:
    company = create_company(name="Current Name", company_type="supplier")
    before = model("companies.Company").objects.values().get(pk=company.pk)

    response = client().get(reverse("companies:edit", args=(company.pk,)))
    html = response.content.decode()

    assert response.status_code == 200
    assert "Current Name" in html
    assert 'value="supplier" selected' in html
    assert model("companies.Company").objects.values().get(pk=company.pk) == before


def test_valid_edit_updates_one_company_and_shows_success() -> None:
    company = create_company()
    browser = client()

    response = browser.post(
        reverse("companies:edit", args=(company.pk,)),
        valid_company_data(
            name="Updated Company",
            company_type="supplier",
            source_jobs_url="https://jobs.example.test/updated/openings",
        ),
    )

    assert response.status_code == 302
    assert model("companies.Company").objects.count() == 1
    company.refresh_from_db()
    assert company.name == "Updated Company"
    assert company.company_type == "supplier"
    assert b"was updated" in browser.get(response.url).content


def test_invalid_edit_does_not_save_partial_changes() -> None:
    company = create_company(name="Original Name", source="original-source")

    response = client().post(
        reverse("companies:edit", args=(company.pk,)),
        valid_company_data(name="", source="changed-source"),
    )

    assert response.status_code == 200
    assert 'value="changed-source"' in response.content.decode()
    company.refresh_from_db()
    assert company.name == "Original Name"
    assert company.source == "original-source"


def test_unknown_company_edit_returns_not_found() -> None:
    response = client().get(reverse("companies:edit", args=(999_999,)))

    assert response.status_code == 404


def test_toggle_requires_post_and_changes_only_active_state_and_timestamp() -> None:
    checked_at = datetime(2026, 8, 6, 12, tzinfo=UTC)
    company = create_company(last_scraped_at=checked_at, last_scrape_status="success")
    toggle_url = reverse("companies:toggle_active", args=(company.pk,))
    original_name = company.name
    original_source = company.source
    browser = client()

    get_response = browser.get(toggle_url)
    company.refresh_from_db()
    assert get_response.status_code == 405
    assert company.is_active is True

    first = browser.post(toggle_url)
    company.refresh_from_db()
    assert first.status_code == 302
    assert company.is_active is False
    assert company.name == original_name
    assert company.source == original_source
    assert company.last_scraped_at == checked_at
    assert company.last_scrape_status == "success"
    assert b"was deactivated" in browser.get(first.url).content

    second = browser.post(toggle_url)
    company.refresh_from_db()
    assert second.status_code == 302
    assert company.is_active is True
    assert b"was activated" in browser.get(second.url).content


def test_toggle_preserves_company_jobs_and_run_history() -> None:
    company = create_company()
    model("jobs.JobPosting").objects.create(
        company=company,
        source=company.source,
        source_job_id="job-1",
        content_hash="a" * 64,
        dedupe_key="b" * 64,
    )
    model("scrape_runs.ScrapeRun").objects.create(company=company)

    response = client().post(reverse("companies:toggle_active", args=(company.pk,)))

    assert response.status_code == 302
    assert model("companies.Company").objects.filter(pk=company.pk).exists()
    assert model("jobs.JobPosting").objects.filter(company=company).count() == 1
    assert model("scrape_runs.ScrapeRun").objects.filter(company=company).count() == 1


def test_create_and_edit_do_not_start_pipeline_or_background_executor() -> None:
    with (
        patch("scraping.pipeline.run_fixture_pipeline") as pipeline,
        patch(
            "scraping.background.ControlledBackgroundExecutor.submit_fixture_pipeline"
        ) as background_submit,
    ):
        created = client().post(reverse("companies:create"), valid_company_data())
        company = model("companies.Company").objects.get()
        edited = client().post(
            reverse("companies:edit", args=(company.pk,)),
            valid_company_data(name="Edited Safely"),
        )

    assert created.status_code == 302
    assert edited.status_code == 302
    pipeline.assert_not_called()
    background_submit.assert_not_called()


def test_unknown_company_url_returns_not_found() -> None:
    response = client().get("/companies/not-a-real-page/")

    assert response.status_code == 404
