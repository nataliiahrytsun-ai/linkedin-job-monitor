from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
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
    company_record = model("companies.Company").objects.create(**values)
    model("companies.CompanySource").objects.create(
        company=company_record,
        source=company_record.source,
        source_jobs_url=company_record.source_jobs_url,
        approval_status="approved",
        is_active=True,
    )
    return company_record


def company_submission(
    company: Any,
    *,
    submitted: tuple[int, ...] = (),
    already_running: tuple[int, ...] = (),
    failed: tuple[int, ...] = (),
) -> Any:
    result_type = importlib.import_module("scraping.background").CompanySubmissionResult
    handles = tuple(
        type("Handle", (), {"company_source_id": source_id})() for source_id in submitted
    )
    return result_type(
        company_id=company.pk,
        submitted=handles,
        already_running_source_ids=already_running,
        skipped_source_ids=(),
        failed_source_ids=failed,
    )


def create_job(company: Any, **overrides: object) -> Any:
    sequence = model("jobs.JobPosting").objects.count() + 1
    values: dict[str, object] = {
        "company": company,
        "company_source": company.sources.get(),
        "source": company.source,
        "source_job_id": f"job-{sequence}",
        "title": f"Role {sequence}",
        "content_hash": f"{sequence:064x}",
        "dedupe_key": f"{sequence + 100:064x}",
    }
    values.update(overrides)
    return model("jobs.JobPosting").objects.create(**values)


def create_scrape_run(
    company: Any,
    *,
    status: str,
    started_at: datetime,
    company_source: Any | None = None,
    error_message: str = "",
) -> Any:
    terminal = status != "running"
    return model("scrape_runs.ScrapeRun").objects.create(
        company=company,
        company_source=company_source or company.sources.get(),
        status=status,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=1) if terminal else None,
        duration_seconds=Decimal("1.000") if terminal else None,
        jobs_found=1 if terminal else 0,
        jobs_created=1 if terminal else 0,
        error_message=error_message,
    )


def create_filterable_jobs(company: Any) -> dict[str, Any]:
    return {
        "remote": create_job(
            company,
            source_job_id="remote-analyst",
            title="Remote Analyst",
            location="Vienna",
            country="Austria",
            workplace_type="remote",
            published_at=datetime(2026, 8, 10, 9, tzinfo=UTC),
            status="active",
        ),
        "onsite": create_job(
            company,
            source_job_id="onsite-engineer",
            title="Onsite Engineer",
            location="Berlin",
            country="Germany",
            workplace_type="onsite",
            published_at=datetime(2026, 8, 1, 9, tzinfo=UTC),
            status="not_found",
        ),
        "hybrid": create_job(
            company,
            source_job_id="hybrid-analyst",
            title="Hybrid Analyst",
            location="Graz",
            country="Austria",
            workplace_type="hybrid",
            published_at=datetime(2026, 8, 5, 9, tzinfo=UTC),
            status="closed",
        ),
    }


def valid_company_data(**overrides: str) -> dict[str, str]:
    values = {
        "name": "New Company",
        "company_type": "client",
        "source": "lever",
        "source_jobs_url": "https://jobs.example.test/new/openings",
        "is_active": "on",
    }
    values.update(overrides)
    return values


def valid_source_data(**overrides: str) -> dict[str, str]:
    values = {
        "source": "lever",
        "source_jobs_url": "https://jobs.lever.co/example",
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
    assert 'href="/jobs/"' in html
    assert 'href="/scrape-runs/"' in html
    assert '<meta name="viewport"' in html
    assert '<link rel="stylesheet" href="/static/css/app.css?v=' in html
    assert html.count("<h1") == 1


def test_company_list_shows_records_statuses_and_stable_name_order() -> None:
    zulu = create_company(
        name="Zulu International Company With A Long Name",
        is_active=False,
        last_scrape_status="failed",
        last_scraped_at=datetime(2026, 8, 7, 14, 33, tzinfo=UTC),
    )
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
    assert "2026-08-07 16:33" in html
    assert "Add company" in html
    assert html.count(f'href="{reverse("companies:create")}"') == 1
    assert f'href="{reverse("companies:detail", args=(alpha.pk,))}"' in html
    assert f'href="{reverse("companies:detail", args=(beta.pk,))}"' in html
    assert f'href="{reverse("companies:detail", args=(zulu.pk,))}"' in html
    assert "Activate" in html
    assert "Deactivate" in html
    assert '<table class="company-table">' in html
    assert html.count('<th scope="col">') == 6

    table_start = html.index('<table class="company-table">')
    table_end = html.index("</table>", table_start)
    table_html = html[table_start:table_end]
    assert "fixture" not in table_html
    assert "Source" not in table_html
    assert "ACTIVE" in table_html
    assert "INACTIVE" in table_html
    assert "Failed" in table_html
    assert "Customer" in table_html
    assert "Supplier" in table_html
    assert "Other" in table_html
    assert table_html.count('<span class="responsive-field-label">Last status</span>') == 3
    assert table_html.count('<span class="responsive-field-label">Last checked</span>') == 3
    assert f'href="{reverse("companies:detail", args=(alpha.pk,))}"' in table_html
    assert f'href="{reverse("companies:edit", args=(alpha.pk,))}"' not in table_html
    assert f'action="{reverse("companies:toggle_active", args=(alpha.pk,))}"' in table_html
    assert 'method="post"' in table_html
    assert table_html.count("company-toggle-button") == 3


def test_company_list_responsive_layout_contract() -> None:
    css = (Path(__file__).resolve().parents[1] / "static" / "css" / "app.css").read_text(
        encoding="utf-8"
    )
    tablet_start = css.index("@media (max-width: 70rem)")
    mobile_start = css.index("@media (max-width: 48rem)")
    desktop_css = css[:tablet_start]
    tablet_css = css[tablet_start:mobile_start]
    mobile_css = css[mobile_start:]

    assert ".company-table" in desktop_css
    assert "border-collapse: collapse" in desktop_css
    assert "table-layout: auto" in desktop_css
    assert ".company-table thead th" in desktop_css
    assert ".company-toggle-button" in desktop_css
    toggle_button_start = desktop_css.index(".company-toggle-button")
    toggle_button_end = desktop_css.index("}", toggle_button_start)
    assert "min-width: 7.75rem" in desktop_css[toggle_button_start:toggle_button_end]
    assert ".visually-hidden" in desktop_css
    visually_hidden_start = desktop_css.index(".visually-hidden")
    visually_hidden_end = desktop_css.index("}", visually_hidden_start)
    visually_hidden_css = desktop_css[visually_hidden_start:visually_hidden_end]
    assert "position: absolute" in visually_hidden_css
    assert "clip: rect(0, 0, 0, 0)" in visually_hidden_css
    assert ".company-meta-compact," in desktop_css
    assert ".responsive-field-label" in desktop_css
    labels_start = desktop_css.index(".company-meta-compact,")
    labels_end = desktop_css.index("}", labels_start)
    assert "display: none" in desktop_css[labels_start:labels_end]
    assert "grid-template-areas" not in desktop_css[desktop_css.index(".company-table") :]
    assert ".company-table-container" in desktop_css
    container_start = desktop_css.index(".company-table-container")
    container_end = desktop_css.index("}", container_start)
    assert "min-width" not in desktop_css[container_start:container_end]
    assert ".company-table thead" in tablet_css
    assert ".company-type-cell" in tablet_css
    assert ".company-monitoring-cell" in tablet_css
    assert ".company-table tbody tr" in tablet_css
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in tablet_css
    assert '"company status actions"' in tablet_css
    assert '"company checked actions"' in tablet_css
    assert ".company-meta-compact" in tablet_css
    assert ".responsive-field-label" in tablet_css
    assert "white-space: nowrap" in tablet_css
    assert "@media (min-width: 48.01rem) and (max-width: 70rem)" in tablet_css
    tablet_alignment_start = tablet_css.index("@media (min-width: 48.01rem) and (max-width: 70rem)")
    tablet_alignment_css = tablet_css[tablet_alignment_start:]
    assert "grid-template-rows: 1fr 1fr" in tablet_alignment_css
    assert "row-gap: 0.35rem" in tablet_alignment_css
    assert "margin-top: 0" in tablet_alignment_css
    assert "width: 8.5rem" in tablet_alignment_css
    assert "justify-self: end" in tablet_alignment_css
    assert ".company-table tbody tr" in mobile_css
    assert '"company monitoring"' in mobile_css
    assert '"status status"' in mobile_css
    assert '"checked checked"' in mobile_css
    assert '"actions actions"' in mobile_css
    assert ".company-actions-cell .row-actions" in mobile_css
    assert "justify-content: space-between" in mobile_css


def test_jobs_table_responsive_layout_contract() -> None:
    css = (Path(__file__).resolve().parents[1] / "static" / "css" / "app.css").read_text(
        encoding="utf-8"
    )
    mobile_start = css.index("@media (max-width: 40rem)")
    desktop_tablet_css = css[:mobile_start]
    mobile_css = css[mobile_start:]

    jobs_table_start = desktop_tablet_css.index(".jobs-table {")
    jobs_table_end = desktop_tablet_css.index("}", jobs_table_start)
    assert "min-width: 0" in desktop_tablet_css[jobs_table_start:jobs_table_end]
    assert ".jobs-table-container" in mobile_css
    assert "overflow: visible" in mobile_css
    company_header_start = mobile_css.index(".jobs-table thead {")
    company_header_end = mobile_css.index("}", company_header_start)
    company_header_css = mobile_css[company_header_start:company_header_end]
    assert "display: block" in company_header_css
    assert ".jobs-table thead tr" in mobile_css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in mobile_css
    assert '"position status"' in mobile_css
    assert '"location location"' in mobile_css
    assert '"country published"' in mobile_css


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
    assert "Sources" in html
    assert "Manage sources" in html
    assert '<dialog class="source-dialog" id="job-sources-dialog"' in html
    assert '<script src="/static/js/company_sources_dialog.js" defer></script>' in html
    assert "Fixture" in html
    assert "ACTIVE" in html
    assert "Not run yet" in html
    assert "Never" in html
    assert "No jobs found yet" in html
    assert "Update jobs" in html
    assert "Jobs aktualisieren" not in html
    assert "Available in the next step" not in html
    assert 'type="submit">Update jobs</button>' in html
    assert 'class="page-heading detail-header"' in html
    assert 'class="company-meta"' in html
    assert "Company type" not in html
    assert "Monitoring status" not in html
    for label in (
        "Last run status",
        "Last run time",
        "Active jobs",
    ):
        assert label in html
    assert "Source jobs URL" not in html
    assert 'class="detail-row detail-monitoring-row"' in html
    assert html.index("Last run status") < html.index("Last run time")
    assert html.index("Last run time") < html.index("Active jobs")
    assert html.index("Active jobs") < html.index("Sources")
    assert html.index('id="sources-summary-heading"') < html.index('id="jobs-heading"')
    assert f'href="{reverse("companies:list")}"' in html
    edit_url = reverse("companies:edit", args=(company.pk,))
    delete_url = reverse("companies:delete", args=(company.pk,))
    assert f'href="{edit_url}"' in html
    assert f'href="{delete_url}"' in html
    detail_header_start = html.index('<div class="page-heading detail-header">')
    detail_header = html[detail_header_start : html.index("<section", detail_header_start)]
    assert f'href="{edit_url}"' in detail_header
    assert (
        f'class="button button-danger-outline button-compact" href="{delete_url}"'
        in detail_header
    )
    assert (
        f'class="button button-secondary button-compact" href="{edit_url}"'
        in detail_header
    )
    assert "Update jobs" not in detail_header
    assert f'action="{reverse("companies:toggle_active", args=(company.pk,))}"' not in detail_header
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

    configured_html = (
        client().get(reverse("companies:detail", args=(configured.pk,))).content.decode()
    )
    missing_html = client().get(reverse("companies:detail", args=(missing.pk,))).content.decode()

    assert 'href="https://jobs.example.test/example/openings"' in configured_html
    assert 'target="_blank"' in configured_html
    assert 'rel="noopener noreferrer"' in configured_html
    assert 'title="https://jobs.example.test/example/openings"' in configured_html
    assert ">jobs.example.test/example/openings</a>" in configured_html
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
    closed = create_job(company, source_job_id="closed-job", title="Closed Role", status="closed")
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
    assert older.source_job_url not in html
    assert "Hidden Other Job" not in html
    assert older.source_job_id not in html
    assert older.content_hash not in html
    assert older.dedupe_key not in html
    assert "None" not in html
    assert 'class="table-scroll jobs-table-container"' in html
    jobs_table_start = html.index('<table class="data-table jobs-table">')
    jobs_table_end = html.index("</table>", jobs_table_start)
    jobs_html = html[jobs_table_start:jobs_table_end]
    assert '<colgroup class="company-jobs-columns">' in jobs_html
    for column in ("position", "location", "country", "published", "status"):
        assert f'class="company-job-col-{column}"' in jobs_html
    assert 'class="job-location-cell" title="Vienna">Vienna</td>' in jobs_html
    assert jobs_html.count('<th scope="col">') == 5
    assert "Original job link" not in jobs_html
    assert ">Open</a>" not in jobs_html
    for posting in (newer, older, closed):
        assert (
            f'<a href="{reverse("jobs:detail", args=(posting.pk,))}">{posting.title}</a>'
        ) in jobs_html
    assert 'class="status-badge status-active">ACTIVE</span>' in jobs_html
    assert 'class="status-badge">NOT_FOUND</span>' in jobs_html
    assert 'class="status-badge">CLOSED</span>' in jobs_html
    assert 'class="status-badge status-active">NOT_FOUND</span>' not in jobs_html
    assert 'class="status-badge status-active">CLOSED</span>' not in jobs_html


def test_company_jobs_table_has_stable_desktop_column_proportions() -> None:
    stylesheet = (
        Path(__file__).resolve().parents[1] / "static" / "css" / "app.css"
    ).read_text(encoding="utf-8")

    assert """.company-job-col-position {
    width: 31%;
  }""" in stylesheet
    assert """.company-job-col-location {
    width: 23%;
  }""" in stylesheet
    assert """.company-job-col-country,
  .company-job-col-published {
    width: 16%;
  }""" in stylesheet
    assert """.company-job-col-status {
    width: 14%;
  }""" in stylesheet


@pytest.mark.parametrize(
    ("params", "expected_key"),
    [
        ({"q": "Remote"}, "remote"),
        ({"location": "Berlin"}, "onsite"),
        ({"workplace_type": "hybrid"}, "hybrid"),
        ({"country": "Germany"}, "onsite"),
        ({"published_from": "2026-08-06"}, "remote"),
        ({"published_to": "2026-08-02"}, "onsite"),
        ({"status": "closed"}, "hybrid"),
    ],
)
def test_company_detail_filters_jobs_by_supported_fields(
    params: dict[str, str], expected_key: str
) -> None:
    company = create_company(name="Filtered Company")
    postings = create_filterable_jobs(company)

    response = client().get(reverse("companies:detail", args=(company.pk,)), params)

    assert response.status_code == 200
    assert list(response.context["jobs"]) == [postings[expected_key]]


def test_company_detail_shows_review_badges_and_filters_review_state() -> None:
    company = create_company(name="Company Review State")
    new_job = create_job(company, title="Company New")
    updated_job = create_job(
        company,
        title="Company Updated",
        last_reviewed_content_hash="f" * 64,
    )
    reviewed_job = create_job(company, title="Company Reviewed")
    reviewed_job.last_reviewed_content_hash = reviewed_job.content_hash
    reviewed_job.save(update_fields=("last_reviewed_content_hash",))

    all_html = client().get(
        reverse("companies:detail", args=(company.pk,))
    ).content.decode()
    updated_html = client().get(
        reverse("companies:detail", args=(company.pk,)),
        {"review_state": "updated"},
    ).content.decode()

    assert '<span class="review-badge review-new">NEW</span>' in all_html
    assert '<span class="review-badge review-updated">UPDATED</span>' in all_html
    assert "Company New" in all_html
    assert "Company Updated" in updated_html
    assert "Company New" not in updated_html
    assert "Company Reviewed" not in updated_html
    assert new_job.last_reviewed_content_hash is None
    assert updated_job.last_reviewed_content_hash != updated_job.content_hash


def test_company_detail_combines_filters_without_leaking_other_company_jobs() -> None:
    company = create_company(name="Scoped Filters")
    postings = create_filterable_jobs(company)
    other_company = create_company(
        name="Other Filter Scope",
        source_jobs_url="https://jobs.example.test/other-filter-scope/openings",
    )
    other = create_job(
        other_company,
        source_job_id="other-hybrid-analyst",
        title="Hybrid Analyst",
        location="Graz",
        country="Austria",
        workplace_type="hybrid",
        published_at=datetime(2026, 8, 5, 9, tzinfo=UTC),
        status="closed",
    )

    response = client().get(
        reverse("companies:detail", args=(company.pk,)),
        {
            "q": "Analyst",
            "location": "Graz",
            "workplace_type": "hybrid",
            "country": "Austria",
            "published_from": "2026-08-04",
            "published_to": "2026-08-06",
            "status": "closed",
            "company": str(other_company.pk),
            "company_type": other_company.company_type,
            "first_seen_from": "2099-01-01",
        },
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert list(response.context["jobs"]) == [postings["hybrid"]]
    assert f'href="{reverse("jobs:detail", args=(postings["hybrid"].pk,))}"' in html
    assert f'href="{reverse("jobs:detail", args=(other.pk,))}"' not in html


def test_company_detail_filter_ui_reuses_column_popovers_and_clean_company_url() -> None:
    company = create_company(name="Filter UI")
    create_job(
        company,
        title="Vienna Role",
        country="Austria",
        location="Vienna",
    )
    other_company = create_company(
        name="Other Countries",
        source_jobs_url="https://jobs.example.test/other-countries/openings",
    )
    create_job(other_company, title="Paris Role", country="France", location="Paris")

    response = client().get(
        reverse("companies:detail", args=(company.pk,)),
        {"q": "Vienna", "country": "Austria"},
    )
    html = response.content.decode()
    form_start = html.index('<form class="jobs-table-filter-form"')
    filter_form = html[form_start : html.index("</form>", form_start)]

    assert f'action="{reverse("companies:detail", args=(company.pk,))}"' in filter_form
    clear_filters = (
        f'href="{reverse("companies:detail", args=(company.pk,))}">Clear filters</a>'
    )
    assert clear_filters in html
    assert html.index(clear_filters) < form_start
    assert filter_form.count('<details class="column-filter') == 5
    for label in (
        "Filter position",
        "Filter location",
        "Filter country",
        "Filter published date",
        "Filter status",
    ):
        assert f'aria-label="{label}"' in filter_form
    for field_name in (
        "q",
        "location",
        "workplace_type",
        "country",
        "published_from",
        "published_to",
        "status",
        "review_state",
    ):
        assert filter_form.count(f'name="{field_name}"') == 1
    for excluded_name in (
        "company",
        "company_type",
        "first_seen_from",
        "first_seen_to",
    ):
        assert f'name="{excluded_name}"' not in filter_form
    assert filter_form.count("column-filter-active") == 2
    assert '<option value="Austria" selected>Austria</option>' in filter_form
    assert '<option value="France">France</option>' not in filter_form
    assert '<script src="/static/js/job_filters.js" defer></script>' in html


def test_company_detail_invalid_empty_and_no_match_filters_are_safe() -> None:
    company = create_company(name="Safe Company Filters")
    postings = create_filterable_jobs(company)
    clean_url = reverse("companies:detail", args=(company.pk,))

    invalid = client().get(
        clean_url,
        {"status": "bogus", "published_from": "not-a-date"},
    )
    empty = client().get(
        clean_url,
        {"q": "", "location": "", "country": "", "status": ""},
    )
    no_match = client().get(clean_url, {"q": "No such vacancy"})
    invalid_html = invalid.content.decode()
    no_match_html = no_match.content.decode()

    assert invalid.status_code == 200
    assert set(invalid.context["jobs"]) == set(postings.values())
    assert "Some invalid filter values were ignored." in invalid_html
    assert empty.status_code == 200
    assert set(empty.context["jobs"]) == set(postings.values())
    assert no_match.status_code == 200
    assert not list(no_match.context["jobs"])
    assert "No jobs match the selected filters." in no_match_html
    assert "No jobs found yet" not in no_match_html


def test_company_detail_get_is_read_only_and_starts_no_execution() -> None:
    company = create_company(
        last_scrape_status="success",
        last_scraped_at=datetime(2026, 8, 6, 12, tzinfo=UTC),
    )
    job = create_job(
        company,
        published_at=datetime(2026, 1, 31, 23, 30, tzinfo=UTC),
    )
    company_before = model("companies.Company").objects.values().get(pk=company.pk)
    job_before = model("jobs.JobPosting").objects.values().get(pk=job.pk)

    with (
        patch("scraping.pipeline.run_fixture_pipeline") as pipeline,
        patch(
            "scraping.background.ControlledBackgroundExecutor.submit_pipeline"
        ) as background_submit,
    ):
        response = client().get(reverse("companies:detail", args=(company.pk,)))

    assert response.status_code == 200
    assert "SUCCESS" in response.content.decode()
    assert "2026-08-06 14:00" in response.content.decode()
    assert "2026-02-01" in response.content.decode()
    assert model("companies.Company").objects.values().get(pk=company.pk) == company_before
    assert model("jobs.JobPosting").objects.values().get(pk=job.pk) == job_before
    assert model("scrape_runs.ScrapeRun").objects.count() == 0
    pipeline.assert_not_called()
    background_submit.assert_not_called()


def test_company_delete_confirmation_is_read_only_and_shows_exact_counts() -> None:
    company = create_company(name="Delete Confirmation")
    source = company.sources.get()
    first_job = create_job(company, company_source=source)
    second_job = create_job(
        company,
        company_source=source,
        source_job_id="confirmation-job-two",
    )
    model("companies.CompanySource").objects.create(
        company=company,
        source="fixture-two",
        source_jobs_url="https://jobs.example.test/delete-confirmation/other",
        approval_status="approved",
        is_active=True,
    )
    first_run = create_scrape_run(
        company,
        status="success",
        started_at=datetime(2026, 8, 12, 9, tzinfo=UTC),
        company_source=source,
    )
    second_run = create_scrape_run(
        company,
        status="failed",
        started_at=datetime(2026, 8, 12, 10, tzinfo=UTC),
        company_source=source,
        error_message="safe test failure",
    )

    response = client().get(reverse("companies:delete", args=(company.pk,)))
    html = response.content.decode()

    assert response.status_code == 200
    assert "companies/company_confirm_delete.html" in [
        template.name for template in response.templates if template.name
    ]
    assert response.context["source_count"] == 2
    assert response.context["job_count"] == 2
    assert response.context["run_count"] == 2
    assert response.context["deletion_blocked"] is False
    assert "Delete Delete Confirmation?" in html
    assert "Configured sources" in html
    assert "Saved jobs" in html
    assert "Scrape runs" in html
    assert "run history including errors" in html
    assert "This action cannot be undone." in html
    assert '<button class="button button-danger" type="submit">Delete company</button>' in html
    assert 'name="csrfmiddlewaretoken"' in html
    assert "window.confirm" not in html
    assert '<div class="company-delete-page">' in html
    assert model("companies.Company").objects.filter(pk=company.pk).exists()
    assert model("jobs.JobPosting").objects.filter(
        pk__in=(first_job.pk, second_job.pk)
    ).count() == 2
    assert model("scrape_runs.ScrapeRun").objects.filter(
        pk__in=(first_run.pk, second_run.pk)
    ).count() == 2


def test_company_delete_confirmation_has_compact_responsive_layout() -> None:
    stylesheet = (
        Path(__file__).resolve().parents[1] / "static" / "css" / "app.css"
    ).read_text(encoding="utf-8")
    mobile_start = stylesheet.index("@media (max-width: 40rem)")
    desktop_css = stylesheet[:mobile_start]
    mobile_css = stylesheet[mobile_start:]

    assert """.company-delete-page {
  width: min(100%, 43rem);
  margin-inline: auto;
  padding-top: clamp(1rem, 5vh, 3rem);
}""" in desktop_css
    assert """.company-delete-confirmation h1 {
  margin-bottom: 0.85rem;
  font-size: clamp(1.55rem, 3vw, 2rem);""" in desktop_css
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in desktop_css
    assert """.company-delete-counts div {
  padding: 0.45rem 0.55rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
""" in desktop_css
    assert "min-height: calc(100vh" not in desktop_css
    assert """.company-delete-counts {
    grid-template-columns: 1fr;
  }""" in mobile_css
    assert """.company-delete-page {
    margin-top: -1.5rem;
    padding-top: 0;
  }""" in mobile_css


def test_company_delete_post_removes_terminal_graph_and_redirects_with_message() -> None:
    company = create_company(name="Terminal Delete")
    posting = create_job(company)
    run = create_scrape_run(
        company,
        status="success",
        started_at=datetime(2026, 8, 12, 9, tzinfo=UTC),
    )
    company_id = company.pk
    source_ids = tuple(company.sources.values_list("pk", flat=True))
    browser = client()

    response = browser.post(reverse("companies:delete", args=(company_id,)))

    assert response.status_code == 302
    assert response.url == reverse("companies:list")
    assert not model("companies.Company").objects.filter(pk=company_id).exists()
    assert not model("companies.CompanySource").objects.filter(pk__in=source_ids).exists()
    assert not model("jobs.JobPosting").objects.filter(pk=posting.pk).exists()
    assert not model("scrape_runs.ScrapeRun").objects.filter(pk=run.pk).exists()
    assert b"and all related data were permanently deleted" in browser.get(
        response.url
    ).content


def test_running_company_delete_page_blocks_action_and_crafted_post() -> None:
    company = create_company(name="Running Delete")
    posting = create_job(company)
    running = create_scrape_run(
        company,
        status="running",
        started_at=datetime(2026, 8, 12, 9, tzinfo=UTC),
    )
    delete_url = reverse("companies:delete", args=(company.pk,))

    get_response = client().get(delete_url)
    get_html = get_response.content.decode()
    crafted_post = client().post(delete_url)

    assert get_response.status_code == 200
    assert get_response.context["deletion_blocked"] is True
    assert "cannot be deleted while one or more source runs are RUNNING" in get_html
    assert "Delete company</button>" not in get_html
    assert crafted_post.status_code == 409
    assert "cannot be deleted while one or more source runs are RUNNING" in (
        crafted_post.content.decode()
    )
    assert model("companies.Company").objects.filter(pk=company.pk).exists()
    assert model("jobs.JobPosting").objects.filter(pk=posting.pk).exists()
    assert model("scrape_runs.ScrapeRun").objects.filter(pk=running.pk).exists()


def test_company_delete_requires_csrf_and_accepts_only_get_or_post() -> None:
    company = create_company(name="Protected Delete")
    delete_url = reverse("companies:delete", args=(company.pk,))
    csrf_client = importlib.import_module("django.test").Client(enforce_csrf_checks=True)

    csrf_response = csrf_client.post(delete_url)
    put_response = client().put(delete_url)
    delete_response = client().delete(delete_url)

    assert csrf_response.status_code == 403
    assert put_response.status_code == 405
    assert delete_response.status_code == 405
    assert model("companies.Company").objects.filter(pk=company.pk).exists()


@pytest.mark.parametrize("method", ["get", "post"])
def test_unknown_company_delete_returns_not_found(method: str) -> None:
    response = getattr(client(), method)(reverse("companies:delete", args=(999_999,)))

    assert response.status_code == 404


def test_company_detail_update_jobs_action_is_csrf_protected_post_form() -> None:
    company = create_company()
    update_url = reverse("companies:update_jobs", args=(company.pk,))

    html = client().get(reverse("companies:detail", args=(company.pk,))).content.decode()

    heading_start = html.index('<div class="company-jobs-heading">')
    filter_form_start = html.index('<form class="jobs-table-filter-form"', heading_start)
    heading_html = html[heading_start:filter_form_start]
    assert f'action="{update_url}"' in html
    assert f'action="{update_url}"' in heading_html
    assert "Clear filters" in heading_html
    assert "Saved vacancies for this company." in heading_html
    assert heading_html.index("Jobs") < heading_html.index("Update jobs")
    assert heading_html.index("Update jobs") < heading_html.index("Clear filters")
    assert '<div class="company-jobs-actions">' in heading_html
    action_index = heading_html.index(f'action="{update_url}"')
    update_form = heading_html[heading_html.rfind("<form", 0, action_index) :]
    update_form = update_form[: update_form.index("</form>")]
    assert 'class="company-jobs-update"' in update_form
    assert 'method="post"' in update_form
    assert 'name="csrfmiddlewaretoken"' in update_form
    assert "Update jobs" in update_form
    assert "disabled" not in update_form


def test_company_detail_action_layout_has_desktop_and_mobile_contracts() -> None:
    stylesheet = (
        Path(__file__).resolve().parents[1] / "static" / "css" / "app.css"
    ).read_text(encoding="utf-8")
    mobile_start = stylesheet.index("@media (max-width: 40rem)")
    desktop_css = stylesheet[:mobile_start]
    mobile_css = stylesheet[mobile_start:]

    assert ".button-danger-outline {\n  background: #ffffff;" in desktop_css
    assert "border-color: #d92d20;\n  color: #b42318;" in desktop_css
    assert """.company-jobs-heading {
  margin-bottom: 1rem;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}""" in desktop_css
    assert """.company-jobs-actions {
  display: flex;
  align-items: flex-end;
  flex: none;
  flex-direction: column;
  gap: 0.3rem;
}""" in desktop_css
    assert """.company-jobs-heading {
    align-items: stretch;
    flex-direction: column;
    gap: 0.75rem;
  }""" in mobile_css
    assert """.company-jobs-actions {
    align-items: center;
    flex-direction: row;
    gap: 0.75rem;
  }""" in mobile_css


def test_runtime_fixture_setting_uses_tracked_demo_data_not_test_fixtures() -> None:
    settings = importlib.import_module("django.conf").settings
    expected = Path(settings.BASE_DIR) / "data" / "fixtures" / "demo_jobs.json"
    test_fixture = Path(__file__).parent / "fixtures" / "backend" / "run_1.json"
    view_source = (Path(__file__).resolve().parents[1] / "companies" / "views.py").read_text(
        encoding="utf-8"
    )

    assert isinstance(settings.JOB_MONITOR_FIXTURE_PATH, Path)
    assert expected == settings.JOB_MONITOR_FIXTURE_PATH
    assert expected.is_file()
    assert expected.read_text(encoding="utf-8") == test_fixture.read_text(encoding="utf-8")
    assert '"tests" / "fixtures"' not in view_source


def test_update_jobs_requires_post_and_get_starts_nothing() -> None:
    company = create_company()
    update_url = reverse("companies:update_jobs", args=(company.pk,))

    with patch("companies.views.background_executor.submit_company") as submit:
        response = client().get(update_url)

    assert response.status_code == 405
    submit.assert_not_called()
    assert model("scrape_runs.ScrapeRun").objects.count() == 0


def test_update_jobs_submits_source_neutral_background_task_and_redirects() -> None:
    company = create_company()
    browser = client()
    update_url = reverse("companies:update_jobs", args=(company.pk,))
    source_id = company.sources.get().pk
    with patch(
        "companies.views.background_executor.submit_company",
        return_value=company_submission(company, submitted=(source_id,)),
    ) as submit:
        response = browser.post(update_url)

    assert response.status_code == 302
    assert response.url == (
        f"{reverse('companies:detail', args=(company.pk,))}?watch_after=0&watch_sources={source_id}"
    )
    submit.assert_called_once()
    assert submit.call_args.kwargs["company"].pk == company.pk
    assert set(submit.call_args.kwargs) == {"company"}
    detail_html = browser.get(response.url).content.decode()
    assert "Job update started." in detail_html
    assert 'id="company-run-polling"' in detail_html
    assert 'data-baseline-run-id="0"' in detail_html
    assert 'data-mode="submission"' in detail_html
    assert f'data-expected-source-ids="{source_id}"' in detail_html
    assert 'src="/static/js/company_run_polling.js"' in detail_html
    javascript = Path("static/js/company_run_polling.js").read_text(encoding="utf-8")
    assert "maxSubmissionChecks = 24" in javascript
    assert "remainingSubmissionChecks <= 0" in javascript


def test_update_jobs_view_does_not_inspect_configured_fixture_path(
    tmp_path: Path,
) -> None:
    company = create_company()
    browser = client()
    missing_fixture = tmp_path / "missing-demo.json"
    override_settings = importlib.import_module("django.test").override_settings

    with (
        override_settings(JOB_MONITOR_FIXTURE_PATH=missing_fixture),
        patch(
            "companies.views.background_executor.submit_company",
            return_value=company_submission(
                company,
                submitted=(company.sources.get().pk,),
            ),
        ) as submit,
    ):
        response = browser.post(reverse("companies:update_jobs", args=(company.pk,)))

    assert response.status_code == 302
    assert response.url.endswith(f"?watch_after=0&watch_sources={company.sources.get().pk}")
    submit.assert_called_once_with(company=company)
    assert b"Job update started." in browser.get(response.url).content


def test_company_polling_detects_fast_terminal_run_created_after_baseline() -> None:
    company = create_company()
    old_run = create_scrape_run(
        company,
        status="success",
        started_at=datetime(2026, 8, 11, 9, tzinfo=UTC),
    )
    watched_page = client().get(
        reverse("companies:detail", args=(company.pk,)),
        {
            "watch_after": old_run.pk,
            "watch_sources": company.sources.get().pk,
        },
    )
    assert 'data-mode="submission"' in watched_page.content.decode()

    new_run = create_scrape_run(
        company,
        status="success",
        started_at=datetime(2026, 8, 11, 9, 1, tzinfo=UTC),
    )
    response = client().get(
        reverse("scrape_runs:status"),
        {
            "company_id": company.pk,
            "company_source_ids": company.sources.get().pk,
            "after_id": old_run.pk,
        },
    )
    payload = response.json()

    assert payload["company_latest_run"]["id"] == new_run.pk
    assert payload["company_latest_run"]["status"] == "success"
    assert payload["company_latest_run"]["is_terminal"] is True
    assert payload["submission_complete"] is True


def test_company_polling_waits_for_every_expected_source_after_baseline() -> None:
    company = create_company()
    first_source = company.sources.get()
    second_source = model("companies.CompanySource").objects.create(
        company=company,
        source="fixture",
        source_jobs_url="https://jobs.example.test/example/secondary",
        approval_status="approved",
        is_active=True,
    )
    baseline = create_scrape_run(
        company,
        company_source=first_source,
        status="success",
        started_at=datetime(2026, 8, 11, 8, tzinfo=UTC),
    )
    first_terminal = create_scrape_run(
        company,
        company_source=first_source,
        status="success",
        started_at=datetime(2026, 8, 11, 9, tzinfo=UTC),
    )
    query = {
        "company_id": company.pk,
        "company_source_ids": f"{first_source.pk},{second_source.pk}",
        "after_id": baseline.pk,
    }

    missing_payload = client().get(reverse("scrape_runs:status"), query).json()
    assert missing_payload["submission_complete"] is False
    assert missing_payload["expected_source_runs"][0]["id"] == first_terminal.pk
    assert missing_payload["expected_source_runs"][0]["company_source_id"] == (first_source.pk)
    assert missing_payload["expected_source_runs"][1] is None

    second_running = create_scrape_run(
        company,
        company_source=second_source,
        status="running",
        started_at=datetime(2026, 8, 11, 9, 1, tzinfo=UTC),
    )
    running_payload = client().get(reverse("scrape_runs:status"), query).json()
    assert running_payload["submission_complete"] is False
    assert running_payload["expected_source_runs"][1]["id"] == second_running.pk
    assert running_payload["expected_source_runs"][1]["is_terminal"] is False

    second_running.status = "failed"
    second_running.finished_at = datetime(2026, 8, 11, 9, 2, tzinfo=UTC)
    second_running.duration_seconds = Decimal("60.000")
    second_running.error_message = "source failed"
    second_running.save()
    terminal_payload = client().get(reverse("scrape_runs:status"), query).json()
    assert terminal_payload["submission_complete"] is True
    assert [run["status"] for run in terminal_payload["expected_source_runs"]] == [
        "success",
        "failed",
    ]


def test_unrelated_newer_run_does_not_complete_expected_source_submission() -> None:
    company = create_company(name="Expected Company")
    expected_source = company.sources.get()
    unrelated = create_company(
        name="Unrelated Company",
        source_jobs_url="https://jobs.example.test/unrelated/openings",
    )
    baseline = create_scrape_run(
        company,
        status="success",
        started_at=datetime(2026, 8, 11, 8, tzinfo=UTC),
    )
    create_scrape_run(
        unrelated,
        status="success",
        started_at=datetime(2026, 8, 11, 9, tzinfo=UTC),
    )

    payload = (
        client()
        .get(
            reverse("scrape_runs:status"),
            {
                "company_id": company.pk,
                "company_source_ids": expected_source.pk,
                "after_id": baseline.pk,
            },
        )
        .json()
    )

    assert payload["expected_source_runs"] == [None]
    assert payload["submission_complete"] is False


def test_company_polling_tracks_running_to_terminal_then_stops() -> None:
    company = create_company()
    running = create_scrape_run(
        company,
        status="running",
        started_at=datetime(2026, 8, 11, 10, tzinfo=UTC),
    )

    running_html = client().get(reverse("companies:detail", args=(company.pk,))).content.decode()
    assert 'data-mode="running"' in running_html
    assert 'data-baseline-run-id="0"' in running_html
    assert f'data-expected-run-ids="{running.pk}"' in running_html

    finish_scrape_run = importlib.import_module("scraping.run_lifecycle").finish_scrape_run
    finish_scrape_run(
        scrape_run=running,
        status="success",
        finished_at=datetime(2026, 8, 11, 10, 0, 1, tzinfo=UTC),
        jobs_found=1,
        jobs_created=1,
        jobs_updated=0,
        requests_made=0,
    )
    endpoint_payload = (
        client()
        .get(
            reverse("scrape_runs:status"),
            {"company_id": company.pk, "ids": running.pk},
        )
        .json()
    )
    terminal_html = client().get(reverse("companies:detail", args=(company.pk,))).content.decode()

    assert endpoint_payload["company_latest_run"]["is_terminal"] is True
    assert 'id="company-run-polling"' not in terminal_html
    javascript = Path("static/js/company_run_polling.js").read_text(encoding="utf-8")
    assert 'mode === "running"' in javascript
    assert "expectedRunIds.every" in javascript
    assert "window.location.reload()" in javascript


def test_other_company_run_does_not_change_company_specific_status() -> None:
    watched_company = create_company(
        name="Watched Company",
        source_jobs_url="https://jobs.example.test/watched/openings",
    )
    other_company = create_company(
        name="Other Company",
        source_jobs_url="https://jobs.example.test/other/openings",
    )
    watched_run = create_scrape_run(
        watched_company,
        status="success",
        started_at=datetime(2026, 8, 11, 8, tzinfo=UTC),
    )
    other_run = create_scrape_run(
        other_company,
        status="running",
        started_at=datetime(2026, 8, 11, 11, tzinfo=UTC),
    )

    payload = (
        client()
        .get(
            reverse("scrape_runs:status"),
            {"company_id": watched_company.pk, "ids": watched_run.pk},
        )
        .json()
    )

    assert payload["latest_run"]["id"] == other_run.pk
    assert payload["company_latest_run"]["id"] == watched_run.pk
    assert [run["id"] for run in payload["runs"]] == [watched_run.pk]


def test_company_status_polling_endpoint_is_read_only_and_starts_no_work() -> None:
    company = create_company()
    run = create_scrape_run(
        company,
        status="success",
        started_at=datetime(2026, 8, 11, 9, tzinfo=UTC),
    )
    before_company = model("companies.Company").objects.values().get(pk=company.pk)
    before_run = model("scrape_runs.ScrapeRun").objects.values().get(pk=run.pk)

    with (
        patch("companies.views.background_executor.submit_company") as submit,
        patch("scraping.pipeline.run_source_pipeline") as pipeline,
    ):
        response = client().get(
            reverse("scrape_runs:status"),
            {"company_id": company.pk, "ids": run.pk},
        )

    assert response.status_code == 200
    assert model("companies.Company").objects.values().get(pk=company.pk) == before_company
    assert model("scrape_runs.ScrapeRun").objects.values().get(pk=run.pk) == before_run
    submit.assert_not_called()
    pipeline.assert_not_called()


def test_update_jobs_rejects_inactive_company_without_submission() -> None:
    company = create_company(is_active=False)
    browser = client()

    with patch("companies.views.background_executor.submit_company") as submit:
        response = browser.post(reverse("companies:update_jobs", args=(company.pk,)))

    assert response.status_code == 302
    submit.assert_not_called()
    company.refresh_from_db()
    assert company.is_active is False
    assert b"Activate this company before updating jobs." in browser.get(response.url).content


def test_update_jobs_without_source_guides_to_discovery_without_starting_it() -> None:
    company = model("companies.Company").objects.create(name="Needs Initial Discovery")
    browser = client()
    no_sources_error = importlib.import_module(
        "scraping.background"
    ).BackgroundNoExecutableSourcesError

    with (
        patch(
            "companies.views.background_executor.submit_company",
            side_effect=no_sources_error("no source"),
        ) as submit,
        patch("companies.views.background_executor.submit_discovery") as discover,
    ):
        response = browser.post(reverse("companies:update_jobs", args=(company.pk,)))

    assert response.status_code == 302
    assert response.url.endswith("manage_sources=1&source_tab=discovered")
    submit.assert_called_once_with(company=company)
    discover.assert_not_called()
    html = browser.get(response.url).content
    assert b"Discover or add a source first" in html
    assert b"Discover sources" in html


def test_update_jobs_reports_controlled_unknown_source_submission_error() -> None:
    company = create_company(source="not-permitted")
    browser = client()

    source_error = importlib.import_module("scraping.background").BackgroundSourceError
    with patch(
        "companies.views.background_executor.submit_company",
        side_effect=source_error("unknown source"),
    ) as submit:
        response = browser.post(reverse("companies:update_jobs", args=(company.pk,)))

    assert response.status_code == 302
    submit.assert_called_once_with(company=company)
    assert b"Job update could not be started." in browser.get(response.url).content


def test_update_jobs_reports_existing_running_run_without_submission() -> None:
    company = create_company()
    source = company.sources.get()
    model("scrape_runs.ScrapeRun").objects.create(
        company=company,
        company_source=source,
    )
    browser = client()

    with patch(
        "companies.views.background_executor.submit_company",
        return_value=company_submission(company, already_running=(source.pk,)),
    ) as submit:
        response = browser.post(reverse("companies:update_jobs", args=(company.pk,)))

    assert response.status_code == 302
    submit.assert_called_once_with(company=company)
    assert model("scrape_runs.ScrapeRun").objects.filter(company=company).count() == 1
    assert b"A job update is already running." in browser.get(response.url).content


def test_update_jobs_reports_executor_duplicate_without_second_run() -> None:
    company = create_company()
    browser = client()

    with patch(
        "companies.views.background_executor.submit_company",
        return_value=company_submission(
            company,
            already_running=(company.sources.get().pk,),
        ),
    ) as submit:
        response = browser.post(reverse("companies:update_jobs", args=(company.pk,)))

    assert response.status_code == 302
    submit.assert_called_once()
    assert model("scrape_runs.ScrapeRun").objects.count() == 0
    assert b"A job update is already running." in browser.get(response.url).content


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
        "Monitoring active",
    ):
        assert label in html
    for excluded in ("last_scrape_status", "last_scraped_at", "created_at", "updated_at"):
        assert f'name="{excluded}"' not in html
    assert '<option value="client">Customer</option>' in html
    assert '<option value="supplier">Supplier</option>' in html
    assert '<option value="other" selected>Other</option>' in html
    assert 'name="source"' not in html
    assert 'name="source_jobs_url"' not in html
    assert "Kunde" not in html
    assert "Sonstige" not in html
    assert html.count("<h1") == 1
    assert model("companies.Company").objects.count() == 0


def test_create_source_options_come_from_user_selectable_registry_api() -> None:
    company = create_company()
    response = client().get(reverse("companies:source_create", args=(company.pk,)))
    source_field = response.context["form"].fields["source"]
    rendered_values = tuple(value for value, _label in source_field.choices)
    selectable_values = importlib.import_module(
        "scraping.sources.registry"
    ).user_selectable_source_keys()

    assert rendered_values == selectable_values
    assert ("lever", "Lever") in tuple(source_field.choices)
    assert ("darwinbox", "Darwinbox") in tuple(source_field.choices)
    assert ("jazzhr", "JazzHR") in tuple(source_field.choices)
    assert ("dreamjobs", "DreamJobs") in tuple(source_field.choices)
    assert all(value != "fixture" for value, _label in source_field.choices)
    assert response.context["form"].unavailable_source_choices == (
        (
            "LinkedIn",
            "Technical adapter ready · Production disabled · "
            "Requires approved LinkedIn access",
        ),
    )
    assert "is_active" not in response.context["form"].fields


def test_valid_create_uses_redirect_normalizes_source_and_refresh_does_not_duplicate() -> None:
    browser = client()
    response = browser.post(
        reverse("companies:create"),
        valid_company_data(source="  LEVER  "),
    )

    assert response.status_code == 302
    company = model("companies.Company").objects.get()
    assert response.url == reverse("companies:detail", args=(company.pk,))
    assert company.source == ""
    assert company.source_jobs_url is None
    assert company.sources.count() == 0
    assert company.company_type == "client"

    redirected = browser.get(response.url)
    assert b"was added" in redirected.content
    assert model("companies.Company").objects.count() == 1


def test_invalid_create_preserves_values_shows_errors_and_creates_nothing() -> None:
    response = client().post(
        reverse("companies:create"),
        valid_company_data(name="", source="lever"),
    )
    html = response.content.decode()

    assert response.status_code == 200
    assert "This field is required" in html
    assert 'name="source"' not in html
    assert model("companies.Company").objects.count() == 0


def test_create_rejects_unsupported_source_submitted_directly() -> None:
    response = client().post(
        reverse("companies:create"),
        valid_company_data(source="https://jobs.lever.co/olo"),
    )

    assert response.status_code == 302
    company = model("companies.Company").objects.get()
    assert company.source == ""
    assert company.sources.count() == 0


def test_create_rejects_internal_fixture_source_submitted_directly() -> None:
    response = client().post(
        reverse("companies:create"),
        valid_company_data(source="fixture"),
    )

    assert response.status_code == 302
    company = model("companies.Company").objects.get()
    assert company.source == ""
    assert company.sources.count() == 0


def test_duplicate_constraint_is_displayed_as_form_error() -> None:
    create_company(source="lever")

    response = client().post(
        reverse("companies:create"),
        valid_company_data(
            source="  LEVER  ",
            source_jobs_url="https://jobs.example.test/example/openings",
        ),
    )

    assert response.status_code == 302
    assert model("companies.Company").objects.count() == 2


def test_edit_get_shows_current_values_without_changing_company() -> None:
    company = create_company(
        name="Current Name",
        company_type="supplier",
        source="lever",
        source_jobs_url="https://jobs.lever.co/olo",
    )
    before = model("companies.Company").objects.values().get(pk=company.pk)

    response = client().get(reverse("companies:edit", args=(company.pk,)))
    html = response.content.decode()

    assert response.status_code == 200
    assert "Current Name" in html
    assert 'value="supplier" selected' in html
    assert 'name="source"' not in html
    assert 'name="source_jobs_url"' not in html
    assert model("companies.Company").objects.values().get(pk=company.pk) == before


def test_edit_get_shows_existing_internal_source_without_selecting_lever() -> None:
    company = create_company(name="Acme GmbH", source="fixture")

    response = client().get(reverse("companies:edit", args=(company.pk,)))
    html = response.content.decode()

    assert response.status_code == 200
    assert 'name="source"' not in html
    assert company.source == "fixture"


def test_valid_edit_of_internal_company_preserves_source_and_shows_success() -> None:
    company = create_company()
    browser = client()

    response = browser.post(
        reverse("companies:edit", args=(company.pk,)),
        valid_company_data(
            name="Updated Company",
            company_type="supplier",
            source="fixture",
            source_jobs_url="https://jobs.example.test/updated/openings",
        ),
    )

    assert response.status_code == 302
    assert model("companies.Company").objects.count() == 1
    company.refresh_from_db()
    assert company.name == "Updated Company"
    assert company.company_type == "supplier"
    assert company.source == "fixture"
    assert company.source_jobs_url == "https://jobs.example.test/example/openings"
    assert b"was updated" in browser.get(response.url).content


def test_edit_ignores_crafted_internal_fixture_source_fields() -> None:
    company = create_company(
        source="lever",
        source_jobs_url="https://jobs.lever.co/example",
    )

    response = client().post(
        reverse("companies:edit", args=(company.pk,)),
        valid_company_data(
            source="fixture",
            source_jobs_url="https://jobs.example.test/updated/openings",
        ),
    )

    assert response.status_code == 302
    company.refresh_from_db()
    assert company.source == "lever"
    assert company.source_jobs_url == "https://jobs.lever.co/example"


def test_invalid_edit_does_not_save_partial_changes() -> None:
    company = create_company(name="Original Name", source="fixture")

    response = client().post(
        reverse("companies:edit", args=(company.pk,)),
        valid_company_data(name="", source="lever"),
    )

    assert response.status_code == 200
    assert 'name="source"' not in response.content.decode()
    company.refresh_from_db()
    assert company.name == "Original Name"
    assert company.source == "fixture"


def test_edit_ignores_unsupported_source_submitted_directly() -> None:
    company = create_company(source="lever", source_jobs_url="https://jobs.lever.co/olo")

    response = client().post(
        reverse("companies:edit", args=(company.pk,)),
        valid_company_data(source="not-registered"),
    )

    assert response.status_code == 302
    company.refresh_from_db()
    assert company.source == "lever"
    assert company.source_jobs_url == "https://jobs.lever.co/olo"


def test_unknown_company_edit_returns_not_found() -> None:
    response = client().get(reverse("companies:edit", args=(999_999,)))

    assert response.status_code == 404


def test_company_without_sources_renders_source_empty_state_and_add_action() -> None:
    company = model("companies.Company").objects.create(name="No Sources")

    response = client().get(reverse("companies:detail", args=(company.pk,)))
    html = response.content.decode()

    assert "No sources configured" in html
    assert "Manage sources" in html
    assert '<dialog class="source-dialog" id="job-sources-dialog"' in html
    dialog_html = html[html.index('<dialog class="source-dialog"') :]
    assert "No job sources configured." in dialog_html
    assert reverse("companies:source_create", args=(company.pk,)) in dialog_html
    assert "Discover sources" in dialog_html
    assert "Discover again" not in dialog_html
    assert 'class="source-row"' not in html
    assert company.sources.count() == 0


def test_company_detail_connected_tab_add_form_is_compact_and_registry_driven() -> None:
    company = model("companies.Company").objects.create(name="Dialog Add")

    html = client().get(reverse("companies:detail", args=(company.pk,))).content.decode()
    add_dialog = html[html.index('id="add-source-form"') :]

    assert "Add source" in add_dialog
    assert "Darwinbox" in add_dialog
    assert "Live access unavailable" not in add_dialog
    assert '<option value="darwinbox">Darwinbox</option>' in add_dialog
    assert '<option value="jazzhr">JazzHR</option>' in add_dialog
    assert '<option value="dreamjobs">DreamJobs</option>' in add_dialog
    assert '<option value="lever">Lever</option>' in add_dialog
    assert (
        '<option value="linkedin" disabled>LinkedIn — Production disabled</option>'
        in add_dialog
    )
    assert "Fixture" not in add_dialog
    assert "JazzHR" in add_dialog
    assert "LinkedIn" in add_dialog
    assert "Technical adapter ready" in add_dialog
    assert "Production disabled" in add_dialog
    assert "Requires approved LinkedIn access" in add_dialog
    assert "Unsupported" not in add_dialog
    assert 'class="help-text source-option-disabled" aria-disabled="true"' in add_dialog
    assert 'name="source"' in add_dialog
    assert 'name="source_jobs_url"' in add_dialog
    assert "Jobs URL" in add_dialog
    assert 'name="is_active"' not in add_dialog
    assert html.count("<dialog") == 1


def test_company_detail_inactive_source_summary_and_inline_edit_form() -> None:
    company = model("companies.Company").objects.create(name="Dialog Edit")
    source = company.sources.create(
        source="lever",
        source_jobs_url="https://jobs.lever.co/inactive",
        approval_status="approved",
        is_active=False,
    )

    html = client().get(reverse("companies:detail", args=(company.pk,))).content.decode()
    summary_html = html[: html.index('<dialog class="source-dialog"')]
    edit_dialog = html[html.index(f'id="edit-source-dialog-{source.pk}"') :]

    assert "0 active" in summary_html
    assert "1 configured" in summary_html
    assert "Edit source" in edit_dialog
    assert 'class="source-readonly-value">Lever</span>' in edit_dialog
    assert 'name="source_jobs_url"' in edit_dialog
    assert '<select name="source"' not in edit_dialog
    assert 'name="is_active"' not in edit_dialog
    assert ">Save</button>" in edit_dialog
    assert "Discover sources" in html


def test_company_source_dialog_script_supports_open_close_and_escape() -> None:
    script = (Path(__file__).parents[1] / "static/js/company_sources_dialog.js").read_text(
        encoding="utf-8"
    )

    assert "dialog.showModal()" in script
    assert 'event.key === "Escape"' in script
    assert "dialog.close()" in script
    assert "[data-dialog-target]" in script
    assert "ArrowRight" in script
    assert "opener.focus()" in script
    assert "scheduleDiscoveryRefresh(dialog)" in script
    assert "window.clearTimeout(refreshTimer)" in script
    assert "[data-connected-source-link]" in script
    assert 'data-source-tab="connected"' in script


def test_add_source_uses_registry_choices_and_creates_approved_active_lever() -> None:
    company = model("companies.Company").objects.create(name="Source Company")
    add_url = reverse("companies:source_create", args=(company.pk,))
    get_response = client().get(add_url)
    html = get_response.content.decode()

    assert '<option value="lever">Lever</option>' in html
    assert '<option value="darwinbox">Darwinbox</option>' in html
    assert '<option value="jazzhr">JazzHR</option>' in html
    assert '<option value="dreamjobs">DreamJobs</option>' in html
    assert (
        '<option value="linkedin" disabled>LinkedIn — Production disabled</option>'
        in html
    )
    assert "Darwinbox" in html
    assert "Live access unavailable" not in html
    assert "Fixture" not in html
    assert "JazzHR" in html
    assert "LinkedIn" in html
    assert "Technical adapter ready" in html
    assert "Production disabled" in html
    assert "Requires approved LinkedIn access" in html
    assert "Unsupported" not in html
    assert 'class="help-text source-option-disabled" aria-disabled="true"' in html
    response = client().post(add_url, valid_source_data())

    assert response.status_code == 302
    assert response.url.endswith("?manage_sources=1&source_tab=connected")
    source = company.sources.get()
    assert source.source == "lever"
    assert source.source_jobs_url == "https://jobs.lever.co/example"
    assert source.approval_status == "approved"
    assert source.is_active is True
    detail = client().get(response.url).content.decode()
    summary_html = detail[: detail.index('<dialog class="source-dialog"')]
    assert "1 active" in summary_html
    assert "1 configured" in summary_html
    assert 'class="source-row"' not in summary_html
    assert "Lever" in detail
    assert "APPROVED" in detail
    assert "ACTIVE" in detail


def test_add_source_creates_approved_active_darwinbox() -> None:
    company = model("companies.Company").objects.create(name="Darwinbox Company")
    add_url = reverse("companies:source_create", args=(company.pk,))

    response = client().post(
        add_url,
        valid_source_data(
            source="darwinbox",
            source_jobs_url="https://tenant.darwinbox.com/ms/candidate/careers",
        ),
    )

    assert response.status_code == 302
    source = company.sources.get()
    assert source.source == "darwinbox"
    assert source.approval_status == "approved"
    assert source.is_active is True
    assert "Darwinbox" in client().get(response.url).content.decode()


def test_add_source_creates_approved_active_jazzhr() -> None:
    company = model("companies.Company").objects.create(name="JazzHR Company")
    add_url = reverse("companies:source_create", args=(company.pk,))

    response = client().post(
        add_url,
        valid_source_data(
            source="jazzhr",
            source_jobs_url="https://example.applytojob.com/apply/jobs/",
        ),
    )

    assert response.status_code == 302
    source = company.sources.get()
    assert source.source == "jazzhr"
    assert source.source_jobs_url == "https://example.applytojob.com/apply/jobs/"
    assert source.approval_status == "approved"
    assert source.is_active is True
    assert "JazzHR" in client().get(response.url).content.decode()


def test_add_source_creates_approved_active_dreamjobs_custom_domain() -> None:
    company = model("companies.Company").objects.create(name="Data Sentics")

    response = client().post(
        reverse("companies:source_create", args=(company.pk,)),
        valid_source_data(
            source="dreamjobs",
            source_jobs_url="https://careers.datasentics.com/jobs",
        ),
    )

    assert response.status_code == 302
    source = company.sources.get()
    assert source.source == "dreamjobs"
    assert source.source_jobs_url == "https://careers.datasentics.com/jobs"
    assert source.approval_status == "approved"
    assert source.is_active is True
    assert "DreamJobs" in client().get(response.url).content.decode()


def test_add_source_creates_approved_active_zoho_recruit_custom_domain() -> None:
    company = model("companies.Company").objects.create(name="BGTS")
    jobs_url = "https://jobs.bgts.com/jobs/Careers"

    response = client().post(
        reverse("companies:source_create", args=(company.pk,)),
        valid_source_data(source="zoho_recruit", source_jobs_url=jobs_url),
    )

    assert response.status_code == 302
    source = company.sources.get()
    assert source.source == "zoho_recruit"
    assert source.source_jobs_url == jobs_url
    assert source.approval_status == "approved"
    assert source.is_active is True
    assert "Zoho Recruit" in client().get(response.url).content.decode()


def test_existing_darwinbox_source_is_visible_and_presented_as_active() -> None:
    company = model("companies.Company").objects.create(name="Darwinbox Existing")
    source = company.sources.create(
        source="darwinbox",
        source_jobs_url="https://tenant.darwinbox.com/ms/candidate/careers",
        approval_status="approved",
        is_active=True,
    )
    response = client().get(reverse("companies:detail", args=(company.pk,)))
    html = response.content.decode()
    summary_html, dialog_html = html.split('<dialog class="source-dialog"', 1)

    assert response.status_code == 200
    assert "1 active" in summary_html
    assert "1 configured" in summary_html
    assert "Darwinbox" in dialog_html
    assert "Live access unavailable" not in dialog_html
    assert f'id="edit-source-dialog-{source.pk}"' in html
    source.refresh_from_db()
    assert source.approval_status == "approved"
    assert source.is_active is True


def test_update_jobs_submits_active_darwinbox_source_without_network() -> None:
    company = model("companies.Company").objects.create(name="Darwinbox Update")
    source = company.sources.create(
        source="darwinbox",
        source_jobs_url="https://tenant.darwinbox.com/ms/candidate/careers",
        approval_status="approved",
        is_active=True,
    )
    browser = client()

    with patch(
        "companies.views.background_executor.submit_company",
        return_value=company_submission(company, submitted=(source.pk,)),
    ) as submit:
        response = browser.post(reverse("companies:update_jobs", args=(company.pk,)))

    assert response.status_code == 302
    assert response.url.endswith(f"watch_sources={source.pk}")
    submit.assert_called_once_with(company=company)
    assert b"Job update started." in browser.get(response.url).content


@pytest.mark.parametrize(
    ("source", "url"),
    [
        ("fixture", "https://jobs.example.test/internal"),
        ("darwinbox", "https://careers.example.test/jobs"),
        ("darwinbox", "https://tenant.darwinbox.com/not-careers"),
        ("jazzhr", "https://example.com/apply"),
        ("jazzhr", "https://example.applytojob.com/not-apply"),
        ("dreamjobs", "https://careers.datasentics.com/not-jobs"),
        ("lever", ""),
        ("lever", "https://example.com/not-lever"),
    ],
)
def test_add_source_rejects_internal_unsupported_blank_and_invalid_config(
    source: str,
    url: str,
) -> None:
    company = model("companies.Company").objects.create(name="Invalid Source")

    response = client().post(
        reverse("companies:source_create", args=(company.pk,)),
        valid_source_data(source=source, source_jobs_url=url),
    )

    assert response.status_code == 200
    assert response.context["form"].errors
    assert 'id="add-source-form"' in response.content.decode()
    assert 'data-auto-open' in response.content.decode()
    assert company.sources.count() == 0


def test_duplicate_source_is_a_form_error_and_same_url_is_allowed_for_other_company() -> None:
    first = model("companies.Company").objects.create(name="First")
    second = model("companies.Company").objects.create(name="Second")
    first.sources.create(
        source="lever",
        source_jobs_url="https://jobs.lever.co/shared",
        approval_status="approved",
        is_active=True,
    )
    data = valid_source_data(source_jobs_url="https://jobs.lever.co/shared")

    duplicate = client().post(
        reverse("companies:source_create", args=(first.pk,)), data
    )
    other_owner = client().post(
        reverse("companies:source_create", args=(second.pk,)), data
    )

    assert duplicate.status_code == 200
    assert "already configured" in duplicate.content.decode()
    assert other_owner.status_code == 302
    assert second.sources.count() == 1


def test_source_detail_is_company_scoped_and_displays_two_independent_sources() -> None:
    first = model("companies.Company").objects.create(name="First")
    second = model("companies.Company").objects.create(name="Second")
    source_a = first.sources.create(
        source="lever",
        source_jobs_url="https://jobs.lever.co/a",
        approval_status="approved",
        is_active=True,
    )
    source_b = first.sources.create(
        source="lever",
        source_jobs_url="https://jobs.lever.co/b",
        approval_status="approved",
        is_active=False,
    )
    foreign = second.sources.create(
        source="lever",
        source_jobs_url="https://jobs.lever.co/foreign",
        approval_status="approved",
        is_active=True,
    )

    html = client().get(reverse("companies:detail", args=(first.pk,))).content.decode()
    summary_html = html[: html.index('<dialog class="source-dialog"')]
    dialog_html = html[html.index('<dialog class="source-dialog"') :]

    assert "1 active" in summary_html
    assert "2 configured" in summary_html
    assert 'class="source-row"' not in summary_html
    assert source_a.source_jobs_url in dialog_html
    assert source_b.source_jobs_url in dialog_html
    assert foreign.source_jobs_url not in html
    assert dialog_html.count('class="source-row"') == 2
    assert dialog_html.count("APPROVED") == 2
    assert "INACTIVE" in dialog_html
    assert "Edit" in dialog_html
    assert "Deactivate" in dialog_html
    assert "Activate" in dialog_html
    assert reverse("companies:source_create", args=(first.pk,)) in dialog_html


def test_edit_source_changes_url_but_platform_is_immutable_and_cross_company_is_404() -> None:
    company = model("companies.Company").objects.create(name="Owner")
    other = model("companies.Company").objects.create(name="Other")
    source = company.sources.create(
        source="lever",
        source_jobs_url="https://jobs.lever.co/old",
        approval_status="approved",
        is_active=False,
    )
    edit_url = reverse("companies:source_edit", args=(company.pk, source.pk))

    response = client().post(
        edit_url,
        valid_source_data(
            source="fixture",
            source_jobs_url="https://jobs.lever.co/new",
        ),
    )
    cross_company = client().post(
        reverse("companies:source_edit", args=(other.pk, source.pk)),
        valid_source_data(source_jobs_url="https://jobs.lever.co/stolen"),
    )

    assert response.status_code == 302
    assert response.url.endswith("?manage_sources=1&source_tab=connected")
    assert cross_company.status_code == 404
    source.refresh_from_db()
    assert source.source == "lever"
    assert source.source_jobs_url == "https://jobs.lever.co/new"
    assert source.is_active is False


def test_invalid_source_edit_preserves_existing_configuration() -> None:
    company = model("companies.Company").objects.create(name="Preserved")
    source = company.sources.create(
        source="lever",
        source_jobs_url="https://jobs.lever.co/original",
        approval_status="approved",
        is_active=True,
    )

    response = client().post(
        reverse("companies:source_edit", args=(company.pk, source.pk)),
        valid_source_data(source_jobs_url="https://example.com/wrong"),
    )

    assert response.status_code == 200
    assert response.context["form"].errors
    assert f'id="edit-source-dialog-{source.pk}"' in response.content.decode()
    assert 'data-auto-open' in response.content.decode()
    source.refresh_from_db()
    assert source.source == "lever"
    assert source.source_jobs_url == "https://jobs.lever.co/original"
    assert source.is_active is True


@pytest.mark.parametrize("approval_status", ["needs_review", "blocked", "rejected"])
def test_nonapproved_source_cannot_be_activated_by_crafted_post(
    approval_status: str,
) -> None:
    company = model("companies.Company").objects.create(name="Review")
    source = company.sources.create(
        source="lever",
        source_jobs_url="https://jobs.lever.co/review",
        approval_status=approval_status,
        is_active=False,
    )

    response = client().post(
        reverse("companies:source_toggle_active", args=(company.pk, source.pk))
    )

    assert response.status_code == 302
    source.refresh_from_db()
    assert source.is_active is False


def test_unregistered_approved_source_cannot_be_activated() -> None:
    company = model("companies.Company").objects.create(name="Unknown")
    source = company.sources.create(
        source="not-registered",
        source_jobs_url="https://jobs.example.test/unknown",
        approval_status="approved",
        is_active=False,
    )

    response = client().post(
        reverse("companies:source_toggle_active", args=(company.pk, source.pk))
    )

    assert response.status_code == 302
    source.refresh_from_db()
    assert source.is_active is False


def test_linkedin_cannot_be_created_or_activated_by_crafted_post() -> None:
    company = model("companies.Company").objects.create(name="LinkedIn Disabled")
    add_url = reverse("companies:source_create", args=(company.pk,))

    create_response = client().post(
        add_url,
        valid_source_data(
            source="linkedin",
            source_jobs_url="https://www.linkedin.com/jobs/example-jobs?f_C=16691",
        ),
    )

    assert create_response.status_code == 200
    assert "source" in create_response.context["form"].errors
    assert company.sources.count() == 0

    source = company.sources.create(
        source="linkedin",
        source_jobs_url="https://www.linkedin.com/jobs/example-jobs?f_C=16691",
        approval_status="approved",
        is_active=False,
    )
    activate_response = client().post(
        reverse("companies:source_toggle_active", args=(company.pk, source.pk))
    )

    assert activate_response.status_code == 302
    source.refresh_from_db()
    assert source.is_active is False


def test_source_toggle_is_post_only_scoped_and_matches_backend_eligibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company = model("companies.Company").objects.create(name="Toggle")
    other = model("companies.Company").objects.create(name="Other")
    source_a = company.sources.create(
        source="lever",
        source_jobs_url="https://jobs.lever.co/a",
        approval_status="approved",
        is_active=True,
    )
    source_b = company.sources.create(
        source="lever",
        source_jobs_url="https://jobs.lever.co/b",
        approval_status="approved",
        is_active=True,
    )
    toggle_url = reverse(
        "companies:source_toggle_active", args=(company.pk, source_b.pk)
    )

    assert client().get(toggle_url).status_code == 405
    assert client().post(
        reverse("companies:source_toggle_active", args=(other.pk, source_b.pk))
    ).status_code == 404
    assert client().post(toggle_url).status_code == 302
    source_a.refresh_from_db()
    source_b.refresh_from_db()
    assert source_a.is_active is True
    assert source_b.is_active is False
    background = importlib.import_module("scraping.background")
    sentinel = object()
    monkeypatch.setattr(
        "scraping.background.run_source_pipeline",
        lambda **kwargs: sentinel,
    )
    with background.ControlledBackgroundExecutor() as executor:
        inactive_result = executor.submit_company(company=company)
        for handle in inactive_result.submitted:
            assert handle.future.result(timeout=5) is sentinel
    assert inactive_result.submitted_source_ids == (source_a.pk,)

    assert client().post(toggle_url).status_code == 302
    source_b.refresh_from_db()
    assert source_b.is_active is True
    with background.ControlledBackgroundExecutor() as executor:
        active_result = executor.submit_company(company=company)
        for handle in active_result.submitted:
            assert handle.future.result(timeout=5) is sentinel
    assert active_result.submitted_source_ids == (source_a.pk, source_b.pk)


def test_running_source_blocks_edit_and_deactivate_but_not_other_source() -> None:
    company = model("companies.Company").objects.create(name="Running Source")
    running_source = company.sources.create(
        source="lever",
        source_jobs_url="https://jobs.lever.co/running",
        approval_status="approved",
        is_active=True,
    )
    other_source = company.sources.create(
        source="lever",
        source_jobs_url="https://jobs.lever.co/other",
        approval_status="approved",
        is_active=True,
    )
    run = create_scrape_run(
        company,
        company_source=running_source,
        status="running",
        started_at=datetime(2026, 8, 11, 12, tzinfo=UTC),
    )

    edit_response = client().post(
        reverse("companies:source_edit", args=(company.pk, running_source.pk)),
        valid_source_data(source_jobs_url="https://jobs.lever.co/changed"),
    )
    toggle_response = client().post(
        reverse(
            "companies:source_toggle_active",
            args=(company.pk, running_source.pk),
        )
    )
    other_response = client().post(
        reverse(
            "companies:source_toggle_active", args=(company.pk, other_source.pk)
        )
    )

    assert edit_response.status_code == 200
    assert "currently running" in edit_response.content.decode()
    assert toggle_response.status_code == 302
    assert other_response.status_code == 302
    running_source.refresh_from_db()
    other_source.refresh_from_db()
    assert running_source.source_jobs_url == "https://jobs.lever.co/running"
    assert running_source.is_active is True
    assert other_source.is_active is False

    run.status = "success"
    run.finished_at = datetime(2026, 8, 11, 12, 1, tzinfo=UTC)
    run.duration_seconds = Decimal("60.000")
    run.save()
    assert client().post(
        reverse(
            "companies:source_toggle_active",
            args=(company.pk, running_source.pk),
        )
    ).status_code == 302
    running_source.refresh_from_db()
    assert running_source.is_active is False


def test_source_forms_have_csrf_protection_under_existing_anonymous_policy() -> None:
    company = model("companies.Company").objects.create(name="CSRF")
    csrf_client = importlib.import_module("django.test").Client(
        enforce_csrf_checks=True
    )

    response = csrf_client.post(
        reverse("companies:source_create", args=(company.pk,)),
        valid_source_data(),
    )

    assert response.status_code == 403
    assert company.sources.count() == 0


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
            "scraping.background.ControlledBackgroundExecutor.submit_pipeline"
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


def discovery_run(company: Any, *, status: str = "needs_review") -> Any:
    return model("discovery.DiscoveryRun").objects.create(
        company=company,
        query=f'"{company.name}" official website',
        official_website_url="https://example.com/",
        careers_url="https://example.com/careers",
        status=status,
        summary="Saved discovery result",
    )


def discovery_candidate(
    run: Any,
    *,
    url: str,
    kind: str = "source",
    platform: str = "lever",
    supported: bool = True,
    decision: str = "selected",
    company_source: Any = None,
    official_site_eligibility: str = "uncertain",
    job_source_eligibility: str = "uncertain",
    job_source_confidence: int = 0,
    evidence: list[str] | None = None,
) -> Any:
    return model("discovery.DiscoveryCandidate").objects.create(
        run=run,
        kind=kind,
        discovered_url=url,
        canonical_url=url,
        platform=platform,
        confidence=92,
        job_source_confidence=job_source_confidence,
        evidence=(
            evidence
            if evidence is not None
            else ["Company name matched", "Careers link relationship"]
        ),
        redirects=[url],
        supported=supported,
        decision=decision,
        reason="Saved evidence reason",
        company_source=company_source,
        official_site_eligibility=official_site_eligibility,
        job_source_eligibility=job_source_eligibility,
    )


def test_unified_sources_manager_has_exactly_two_tabs_and_no_second_dialog() -> None:
    company = model("companies.Company").objects.create(name="Unified Sources")
    run = discovery_run(company)
    discovery_candidate(run, url="https://jobs.lever.co/unified")

    html = client().get(reverse("companies:detail", args=(company.pk,))).content.decode()
    before_jobs = html[: html.index('id="jobs-heading"')]

    assert html.count("<dialog") == 1
    assert before_jobs.count('role="tab"') == 2
    assert '>Connected (0)</button>' in before_jobs
    assert '>Discovered</button>' in before_jobs
    assert 'id="sources-panel-discovered"' in before_jobs
    assert "Source Discovery" not in html
    assert html.index("source-summary") < html.index('id="jobs-heading"')


@pytest.mark.parametrize("status", ["running", "needs_review", "failed"])
def test_unified_sources_manager_defaults_to_discovered_for_actionable_run(
    status: str,
) -> None:
    company = model("companies.Company").objects.create(name=f"Discovery {status}")
    discovery_run(company, status=status)

    html = client().get(
        reverse("companies:detail", args=(company.pk,)) + "?manage_sources=1"
    ).content.decode()

    assert 'id="sources-tab-discovered" role="tab"' in html
    assert 'data-source-tab="discovered">Discovered</button>' in html
    assert 'data-source-panel="discovered" hidden' not in html
    assert 'data-source-panel="connected" hidden' in html
    if status == "running":
        assert 'data-discovery-running="true"' in html
        assert "source_tab=discovered" in html


def test_discovered_tab_derives_and_renders_every_candidate_state() -> None:
    company = model("companies.Company").objects.create(name="State Matrix")
    connected_source = company.sources.create(
        source="jazzhr",
        source_jobs_url="https://connected.applytojob.com/apply/jobs/",
        approval_status="approved",
        is_active=True,
    )
    old_run = discovery_run(company)
    discovery_candidate(old_run, url="https://jobs.lever.co/old-hidden")
    run = discovery_run(company)
    discovery_candidate(run, url="https://jobs.lever.co/ready")
    discovery_candidate(run, url="https://jobs.lever.co/ready-two")
    discovery_candidate(
        run,
        url=connected_source.source_jobs_url,
        platform="jazzhr",
        decision="already_connected",
        company_source=connected_source,
    )
    discovery_candidate(
        run,
        url="https://boards.greenhouse.io/state-matrix",
        platform="greenhouse",
        supported=False,
        decision="unsupported",
        evidence=["Unregistered ATS asset or host"],
    )
    discovery_candidate(
        run,
        url="https://example.com/careers",
        kind="careers",
        platform="",
        supported=False,
        decision="unsupported",
        evidence=["Confirmed careers listing with repeated job cards"],
    )
    discovery_candidate(
        run,
        url="https://jobs.lever.co/review",
        decision="needs_review",
        evidence=["Repeated job cards"],
    )
    discovery_candidate(
        run,
        url="https://linkedin.com/jobs/search/?keywords=state",
        kind="official_site",
        platform="",
        supported=False,
        decision="rejected",
    )
    discovery_candidate(
        run,
        url="https://linkedin.com/company/state-matrix",
        kind="official_site",
        platform="",
        supported=False,
        decision="rejected",
    )

    html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()

    for state in ("ready_to_connect", "adapter_required", "investigation_required", "needs_review"):
        assert f'data-candidate-state="{state}"' in html
    assert 'data-candidate-state="connected"' not in html
    assert 'data-candidate-state="not_a_job_source"' not in html
    assert "https://jobs.lever.co/old-hidden" in html
    assert "Adapter task" in html
    assert "New adapter needed" in html
    assert "Connect selected" not in html


def test_single_connect_is_idempotent_and_company_scoped() -> None:
    company = model("companies.Company").objects.create(name="Connect Owner")
    other = model("companies.Company").objects.create(name="Other Owner")
    run = discovery_run(company)
    candidate = discovery_candidate(run, url="https://jobs.lever.co/connect-owner")

    first = client().post(
        reverse("discovery:connect", args=(company.pk, candidate.pk))
    )
    repeated = client().post(
        reverse("discovery:connect", args=(company.pk, candidate.pk))
    )
    cross_company = client().post(
        reverse("discovery:connect", args=(other.pk, candidate.pk))
    )

    assert first.status_code == 302
    assert first.url.endswith("source_tab=connected")
    assert repeated.status_code == 302
    assert cross_company.status_code == 302
    assert company.sources.count() == 1
    assert other.sources.count() == 0
    candidate.refresh_from_db()
    assert candidate.decision == "connected"
    assert candidate.company_source_id == company.sources.get().pk


def test_bulk_connect_revalidates_each_candidate_and_reports_partial_result() -> None:
    company = model("companies.Company").objects.create(name="Bulk Owner")
    run = discovery_run(company)
    ready = discovery_candidate(run, url="https://jobs.lever.co/bulk-owner")
    review = discovery_candidate(
        run,
        url="https://jobs.lever.co/bulk-review",
        decision="needs_review",
    )
    browser = client()

    response = browser.post(
        reverse("discovery:connect_selected", args=(company.pk,)),
        {"candidate_ids": [str(ready.pk), str(ready.pk), str(review.pk), "bad"]},
    )
    rendered = browser.get(response.url).content.decode()

    assert response.status_code == 302
    assert response.url.endswith("source_tab=connected")
    assert company.sources.count() == 1
    assert "was connected" in rendered
    assert f"Candidate #{review.pk} could not be connected" in rendered
    review.refresh_from_db()
    assert review.decision == "needs_review"


def test_revalidate_uses_only_saved_candidate_and_never_creates_source() -> None:
    company = model("companies.Company").objects.create(name="Saved Revalidate")
    run = discovery_run(company)
    candidate = discovery_candidate(
        run,
        url="https://jobs.lever.co/saved-revalidate",
        supported=False,
        decision="unsupported",
    )

    with patch("discovery.service.configured_search_provider") as search_provider:
        response = client().post(
            reverse("discovery:revalidate", args=(company.pk, candidate.pk))
        )

    assert response.status_code == 302
    search_provider.assert_not_called()
    assert company.sources.count() == 0
    candidate.refresh_from_db()
    assert candidate.supported is True
    assert candidate.decision == "selected"


def test_existing_equivalent_source_state_is_preserved_by_discovered_view() -> None:
    company = model("companies.Company").objects.create(name="Preserve Source")
    source = company.sources.create(
        source="lever",
        source_jobs_url="https://jobs.lever.co/preserve/",
        approval_status="needs_review",
        is_active=False,
    )
    run = discovery_run(company)
    candidate = discovery_candidate(run, url="https://jobs.lever.co/preserve")

    html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()
    discovered_start = html.index('id="sources-panel-discovered"')
    discovered = html[discovered_start : html.index("</section>", discovered_start)]

    assert candidate.canonical_url not in discovered
    source.refresh_from_db()
    candidate.refresh_from_db()
    assert source.approval_status == "needs_review"
    assert source.is_active is False
    assert candidate.decision == "selected"


def test_adapter_task_draft_is_built_from_saved_data_without_side_effects() -> None:
    company = model("companies.Company").objects.create(name="Draft Company")
    run = discovery_run(company)
    candidate = discovery_candidate(
        run,
        url="https://boards.greenhouse.io/draft-company",
        platform="greenhouse",
        supported=False,
        decision="unsupported",
        evidence=["Unregistered ATS asset or host"],
    )
    run_count = model("discovery.DiscoveryRun").objects.count()
    candidate_count = model("discovery.DiscoveryCandidate").objects.count()

    with patch("discovery.service.configured_search_provider") as search_provider:
        html = client().get(
            reverse("companies:detail", args=(company.pk,))
            + "?manage_sources=1&source_tab=discovered"
        ).content.decode()

    search_provider.assert_not_called()
    assert "Adapter research task" in html
    assert "Draft Company" in html
    assert candidate.canonical_url in html
    assert "no registered adapter supports this source" in html
    assert "Do not assume implementation is feasible" in html
    assert "DiscoveryRun ID" not in html
    assert "Confidence:" not in html
    assert "Evidence:" not in html
    assert model("discovery.DiscoveryRun").objects.count() == run_count
    assert model("discovery.DiscoveryCandidate").objects.count() == candidate_count
    assert company.sources.count() == 0


def test_discovered_tab_hides_registry_coverage_and_internal_states() -> None:
    company = model("companies.Company").objects.create(name="Coverage Company")
    darwinbox = company.sources.create(
        source="darwinbox",
        source_jobs_url="https://coverage.darwinbox.com/ms/candidate/careers",
        approval_status="approved",
        is_active=True,
    )
    jazzhr = company.sources.create(
        source="jazzhr",
        source_jobs_url="https://coverage.applytojob.com/apply",
        approval_status="approved",
        is_active=True,
    )
    run = discovery_run(company, status="already_connected")
    darwinbox_candidate = discovery_candidate(
        run,
        url=darwinbox.source_jobs_url,
        platform="darwinbox",
        decision="already_connected",
        company_source=darwinbox,
    )
    darwinbox_candidate.origin = "existing_source"
    darwinbox_candidate.save(update_fields=("origin",))
    jazzhr_candidate = discovery_candidate(
        run,
        url=jazzhr.source_jobs_url,
        platform="jazzhr",
        decision="already_connected",
        company_source=jazzhr,
    )
    jazzhr_candidate.origin = "existing_source"
    jazzhr_candidate.save(update_fields=("origin",))
    check_model = model("discovery.DiscoveryAdapterCheck")
    for platform, status, candidate, source in (
        ("darwinbox", "already_connected", darwinbox_candidate, darwinbox),
        ("dreamjobs", "not_found", None, None),
        ("jazzhr", "already_connected", jazzhr_candidate, jazzhr),
        ("lever", "not_found", None, None),
    ):
        check_model.objects.create(
            run=run,
            platform=platform,
            status=status,
            reason="Coverage evidence",
            candidate=candidate,
            company_source=source,
        )

    html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()

    dialog = html[html.index('<dialog class="source-dialog"') : html.index("</dialog>")]
    discovered_start = dialog.index('id="sources-panel-discovered"')
    discovered = dialog[discovered_start : dialog.index("</section>", discovered_start)]
    assert "4 of 4 checked" not in dialog
    assert "Adapter coverage" not in dialog
    assert "Not found" not in dialog
    assert "not_checked" not in dialog
    assert "validation_failed" not in dialog
    assert "already_connected" not in dialog
    assert dialog.count(">Connected</span>") == 0
    assert darwinbox.source_jobs_url not in discovered
    assert jazzhr.source_jobs_url not in discovered


def test_discovered_groups_other_results_and_hides_technical_data_by_default() -> None:
    company = model("companies.Company").objects.create(name="Readable Discovery")
    run = discovery_run(company)
    connected_source = company.sources.create(
        source="jazzhr",
        source_jobs_url="https://readable.applytojob.com/apply",
        approval_status="approved",
        is_active=True,
    )
    discovery_candidate(
        run,
        url=connected_source.source_jobs_url,
        platform="jazzhr",
        decision="already_connected",
        company_source=connected_source,
    )
    urls = (
        "https://linkedin.com/company/readable",
        "https://leadiq.com/c/readable/123",
        "https://example.net/news/readable-company",
    )
    for url in urls:
        discovery_candidate(
            run,
            url=url,
            kind="official_site",
            platform="",
            supported=False,
            decision="rejected",
        )

    with patch("discovery.service.configured_search_provider") as search_provider:
        html = client().get(
            reverse("companies:detail", args=(company.pk,))
            + "?manage_sources=1&source_tab=discovered"
        ).content.decode()

    search_provider.assert_not_called()
    assert "Other search results" not in html
    for url in urls:
        assert url not in html
    assert "Evidence</dt>" not in html
    assert '<div class="discovery-source-list">' not in html
    assert 'class="discovery-candidate"' not in html
    assert run.candidates.count() == 4
    assert company.sources.count() == 1


def test_linkedin_jobs_without_confirmed_listing_is_hidden() -> None:
    company = model("companies.Company").objects.create(name="Data Sentics")
    run = discovery_run(company)
    candidate = discovery_candidate(
        run,
        url="https://www.linkedin.com/company/datasentics/jobs",
        kind="official_site",
        platform="",
        supported=False,
        decision="rejected",
        official_site_eligibility="not_official_site",
        job_source_eligibility="external_job_board",
        job_source_confidence=82,
    )
    discovery_candidate(
        run,
        url="https://www.linkedin.com/company/datasentics",
        kind="official_site",
        platform="",
        supported=False,
        decision="rejected",
        official_site_eligibility="not_official_site",
        job_source_eligibility="not_a_job_source",
        job_source_confidence=90,
    )

    html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()

    assert 'data-candidate-state="adapter_required"' not in html
    assert "LinkedIn jobs" not in html
    assert "New adapter needed" not in html
    assert "Adapter task" not in html
    assert reverse("discovery:connect", args=(company.pk, candidate.pk)) not in html
    assert "Other search results" not in html


def test_blog_result_with_labour_market_terms_is_hidden_from_discovered() -> None:
    company = model("companies.Company").objects.create(name="Acuity Analytics")
    run = discovery_run(company)
    blog_url = "https://acuityanalytics.com/blog/ai-skills-gap-and-talent-shortage/"
    discovery_candidate(
        run,
        url=blog_url,
        kind="careers",
        platform="",
        supported=False,
        decision="needs_review",
        job_source_eligibility="possible_job_source",
        evidence=[
            "Search result mentions talent, skills, jobs, careers, hiring and recruitment"
        ],
    )

    html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()

    assert blog_url not in html
    assert "Adapter task" not in html
    assert "New adapter needed" not in html
    assert "No job sources found." in html
    assert "require attention" not in html


@pytest.mark.parametrize("keyword", ["talent", "skills", "jobs", "careers"])
def test_article_keywords_do_not_create_adapter_task(keyword: str) -> None:
    company = model("companies.Company").objects.create(name=f"Article {keyword}")
    run = discovery_run(company)
    url = f"https://example.com/insights/{keyword}-market-report/"
    discovery_candidate(
        run,
        url=url,
        kind="careers",
        platform="",
        supported=False,
        decision="unsupported",
        job_source_eligibility="possible_job_source",
        evidence=[f"Search text contains {keyword}"],
    )

    html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()

    assert url not in html
    assert "Adapter task" not in html


def test_official_corporate_url_without_listing_does_not_create_adapter_task() -> None:
    company = model("companies.Company").objects.create(name="Corporate Only")
    run = discovery_run(company)
    corporate_url = "https://corporate-only.example/about-us/"
    discovery_candidate(
        run,
        url=corporate_url,
        kind="official_site",
        platform="",
        supported=False,
        decision="needs_review",
        official_site_eligibility="official_site",
        job_source_eligibility="possible_job_source",
        evidence=["Company identity matches the official domain"],
    )

    html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()

    assert corporate_url not in html
    assert "Adapter task" not in html


def test_confirmed_unknown_listing_creates_exactly_one_adapter_task() -> None:
    company = model("companies.Company").objects.create(name="Unknown ATS")
    run = discovery_run(company)
    listing_url = "https://careers.unknown-ats.example/openings"
    discovery_candidate(
        run,
        url=listing_url,
        kind="source",
        platform="unknown_ats",
        supported=False,
        decision="unsupported",
        job_source_eligibility="unsupported_ats",
        evidence=["Job listing schema", "Repeated job cards", "ATS-specific metadata"],
    )

    html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()

    assert listing_url in html
    assert html.count("Adapter task") == 1
    assert html.count("New adapter needed") == 1
    assert "1 additional source found." in html


def test_connected_and_canonical_duplicate_candidates_are_hidden_from_discovered() -> None:
    company = model("companies.Company").objects.create(name="Canonical Duplicate")
    source = company.sources.create(
        source="lever",
        source_jobs_url="https://jobs.lever.co/canonical-duplicate",
        approval_status="approved",
        is_active=True,
    )
    run = discovery_run(company)
    discovery_candidate(
        run,
        url=source.source_jobs_url,
        company_source=source,
        decision="already_connected",
    )
    duplicate = discovery_candidate(
        run,
        url=f"{source.source_jobs_url}/",
        decision="selected",
    )

    html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()
    discovered = html[html.index('id="sources-panel-discovered"') :]

    assert source.source_jobs_url not in discovered
    assert duplicate.canonical_url not in discovered
    assert "1 source already connected · No additional sources found." in discovered
    assert "require attention" not in discovered


def test_ignore_preserves_candidate_and_removes_it_from_inventory() -> None:
    company = model("companies.Company").objects.create(name="Reversible Ignore")
    run = discovery_run(company)
    candidate = discovery_candidate(
        run,
        url="https://unknown.example/careers/jobs",
        kind="careers",
        platform="",
        supported=False,
        decision="needs_review",
        evidence=["Confirmed careers listing with repeated job cards"],
    )

    ignored = client().post(reverse("discovery:ignore", args=(company.pk, candidate.pk)))
    candidate.refresh_from_db()
    assert ignored.status_code == 302
    assert candidate.is_ignored is True
    assert run.candidates.filter(pk=candidate.pk).exists()

    ignored_html = client().get(ignored.url).content.decode()
    assert "Restore" not in ignored_html
    assert "Ignored by user" not in ignored_html
    assert candidate.canonical_url not in ignored_html


def test_candidate_card_is_compact_and_discover_again_is_primary() -> None:
    company = model("companies.Company").objects.create(name="Compact Sources")
    run = discovery_run(company)
    discovery_candidate(
        run,
        url="https://unknown.example/company/jobs",
        kind="careers",
        platform="",
        supported=False,
        decision="needs_review",
        evidence=["Confirmed job listing schema"],
    )
    discovery_candidate(
        run,
        url="https://example.com/",
        kind="official_site",
        platform="",
        supported=False,
        decision="selected",
        official_site_eligibility="official_site",
    )

    html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()
    source_start = html.index('<div class="discovery-source-list">')
    source_end = html.index("</section>", source_start)
    compact_list = html[source_start:source_end]

    assert "Unrecognized careers source" in compact_list
    assert "New adapter needed" in compact_list
    assert "confidence" not in html.casefold()
    assert html.count('<article class="discovery-source-row"') == 1
    technical_labels = (
        "Official site</dt>",
        "Careers URL</dt>",
        "Origin</dt>",
        "Validation</dt>",
        "Redirects</dt>",
    )
    for technical_label in technical_labels:
        assert technical_label not in compact_list
    discover_button = (
        'class="button button-primary button-compact" type="submit" '
        'form="discovery-start-form" data-source-tab-action="discovered"'
    )
    assert discover_button in html


def test_historical_discovery_without_checks_hides_coverage_details() -> None:
    company = model("companies.Company").objects.create(name="Historical Coverage")
    run = discovery_run(company)
    discovery_candidate(run, url="https://jobs.lever.co/historical")

    html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()

    assert "Adapter coverage was not recorded for this run." not in html
    assert "Adapter coverage" not in html
    assert "0 of 0" not in html


def test_sources_dialog_keeps_close_and_tabs_in_sticky_header() -> None:
    company = model("companies.Company").objects.create(name="Sticky Sources")
    html = client().get(reverse("companies:detail", args=(company.pk,))).content.decode()
    header = html[html.index('<header class="source-dialog-header">') : html.index(
        "</header>", html.index('<header class="source-dialog-header">')
    )]

    assert "data-source-dialog-close" in header
    assert 'aria-label="Close job sources">\u00d7</button>' in header
    assert ">Close</button>" not in header
    assert 'role="tablist"' in header
    assert header.count('role="tab"') == 2
    assert 'role="tabpanel"' in html


def test_sources_toolbar_uses_actual_company_sources_and_one_context_action() -> None:
    company = model("companies.Company").objects.create(name="Toolbar Sources")
    for source, url in (
        ("darwinbox", "https://toolbar.darwinbox.com/ms/candidate/careers"),
        ("jazzhr", "https://toolbar.applytojob.com/apply"),
    ):
        company.sources.create(
            source=source,
            source_jobs_url=url,
            approval_status="approved",
            is_active=True,
        )

    connected_html = client().get(
        reverse("companies:detail", args=(company.pk,)) + "?manage_sources=1"
    ).content.decode()
    connected_header_start = connected_html.index('<header class="source-dialog-header">')
    connected_header = connected_html[
        connected_header_start : connected_html.index("</header>", connected_header_start)
    ]
    assert ">Connected (2)</button>" in connected_header
    assert 'data-source-tab-action="connected" data-source-form-toggle="add-source-form"' in (
        connected_header
    )
    assert ">+ Add source</button>" in connected_header
    assert 'data-source-tab-action="discovered" hidden' in connected_header
    assert "source-panel-heading" not in connected_html

    discovered_html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()
    discovered_header_start = discovered_html.index('<header class="source-dialog-header">')
    discovered_header = discovered_html[
        discovered_header_start : discovered_html.index(
            "</header>", discovered_header_start
        )
    ]
    hidden_add_action = (
        'data-source-tab-action="connected" '
        'data-source-form-toggle="add-source-form" hidden'
    )
    assert hidden_add_action in discovered_header
    assert 'form="discovery-start-form" data-source-tab-action="discovered"' in (
        discovered_header
    )
    assert discovered_header.count("Discover sources") == 1
    assert "Discover again" not in discovered_html
    assert "Start discovery" not in discovered_html
    assert discovered_html.count("Discover sources") == 1
    assert 'id="discovery-start-form" class="discovery-start-form"' in discovered_html
    assert "Official domain (optional)" in discovered_html


def test_connected_sources_survive_a_later_discovery_result() -> None:
    company = model("companies.Company").objects.create(name="Persistent Sources")
    darwinbox = company.sources.create(
        source="darwinbox",
        source_jobs_url="https://persistent.darwinbox.com/ms/candidate/careers",
        approval_status="approved",
        is_active=True,
    )
    jazzhr = company.sources.create(
        source="jazzhr",
        source_jobs_url="https://persistent.applytojob.com/apply",
        approval_status="approved",
        is_active=True,
    )
    discovery_run(company, status="not_found")

    html = client().get(
        reverse("companies:detail", args=(company.pk,)) + "?manage_sources=1"
    ).content.decode()
    connected_start = html.index('id="sources-panel-connected"')
    connected = html[connected_start : html.index("</section>", connected_start)]

    assert ">Connected (2)</button>" in html
    assert "Darwinbox" in connected
    assert darwinbox.source_jobs_url in connected
    assert "JazzHR" in connected
    assert jazzhr.source_jobs_url in connected
    assert connected.count(">APPROVED</span>") == 2
    assert connected.count(">ACTIVE</span>") == 2


def test_discovery_summary_uses_company_sources_and_is_rendered_once() -> None:
    company = model("companies.Company").objects.create(name="Summary Sources")
    for source, url in (
        ("darwinbox", "https://summary.darwinbox.com/ms/candidate/careers"),
        ("jazzhr", "https://summary.applytojob.com/apply"),
    ):
        company.sources.create(
            source=source,
            source_jobs_url=url,
            approval_status="approved",
            is_active=True,
        )
    discovery_run(company, status="already_connected")

    html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()
    summary = "2 sources already connected · No additional sources found."

    assert html.count(summary) == 1
    assert "No job sources found." not in html
    assert "No new sources found" not in html


def test_discovery_summary_reports_additional_source_without_connected_duplicates() -> None:
    company = model("companies.Company").objects.create(name="Additional Source")
    source = company.sources.create(
        source="jazzhr",
        source_jobs_url="https://additional.applytojob.com/apply",
        approval_status="approved",
        is_active=True,
    )
    run = discovery_run(company)
    discovery_candidate(
        run,
        url=source.source_jobs_url,
        platform="jazzhr",
        decision="already_connected",
        company_source=source,
    )
    new_url = "https://boards.greenhouse.io/additional-source"
    discovery_candidate(
        run,
        url=new_url,
        platform="greenhouse",
        supported=False,
        decision="unsupported",
        job_source_eligibility="unsupported_ats",
        evidence=["Unregistered ATS asset or host"],
    )

    html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()
    discovered_start = html.index('id="sources-panel-discovered"')
    discovered = html[discovered_start : html.index("</section>", discovered_start)]

    assert "1 source already connected · 1 additional source found." in discovered
    assert new_url in discovered
    assert source.source_jobs_url not in discovered


@pytest.mark.parametrize(
    ("platform", "platform_label", "url", "evidence"),
    [
        (
            "personio",
            "Personio",
            "https://myneva-group.jobs.personio.de/",
            "Personio public jobs host",
        ),
        (
            "workday",
            "Workday",
            "https://acme.wd3.myworkdayjobs.com/",
            "Workday public myworkdayjobs host",
        ),
    ],
)
def test_catalog_unsupported_ats_candidates_are_published_without_evidence_whitelist(
    platform: str, platform_label: str, url: str, evidence: str
) -> None:
    company = model("companies.Company").objects.create(name=f"Unsupported {platform}")
    run = discovery_run(company, status="unsupported")
    discovery_candidate(
        run,
        url=url,
        platform=platform,
        supported=False,
        decision="unsupported",
        job_source_eligibility="unsupported_ats",
        evidence=[evidence],
    )

    html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()
    discovered_start = html.index('id="sources-panel-discovered"')
    discovered = html[discovered_start : html.index("</section>", discovered_start)]

    assert url in discovered
    assert platform_label in discovered
    assert "Adapter: Adapter not implemented" in discovered
    assert "New adapter needed" in discovered
    assert "1 additional source found." in discovered
    assert "No job sources found." not in discovered
    assert "Confidence" not in discovered
    assert "Evidence" not in discovered


def test_multiple_supported_and_unsupported_sources_are_published_together() -> None:
    company = model("companies.Company").objects.create(name="Multi Source")
    run = discovery_run(company, status="needs_review")
    discovery_candidate(
        run,
        url="https://jobs.lever.co/multi-source",
        platform="lever",
        supported=True,
        decision="selected",
        job_source_eligibility="supported_ats",
    )
    discovery_candidate(
        run,
        url="https://multi-source.jobs.personio.de/",
        platform="personio",
        supported=False,
        decision="unsupported",
        job_source_eligibility="unsupported_ats",
        evidence=["Personio public jobs host"],
    )

    html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()
    discovered_start = html.index('id="sources-panel-discovered"')
    discovered = html[discovered_start : html.index("</section>", discovered_start)]

    assert "https://jobs.lever.co/multi-source" in discovered
    assert "https://multi-source.jobs.personio.de/" in discovered
    assert "Ready to connect" in discovered
    assert "Adapter: Adapter not implemented" in discovered
    assert "2 additional sources found." in discovered
    assert "No job sources found." not in discovered


def test_non_source_privacy_candidate_is_not_published_as_unknown_custom() -> None:
    company = model("companies.Company").objects.create(name="Privacy Candidate")
    run = discovery_run(company, status="unsupported")
    discovery_candidate(
        run,
        url="https://privacy.example/careers/privacy-policy",
        kind="careers",
        platform="",
        supported=False,
        decision="unsupported",
        job_source_eligibility="company_jobs_page",
        evidence=["Confirmed careers listing candidate"],
    )

    html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()

    assert "privacy.example/careers/privacy-policy" not in html
    assert "Unknown / Custom" not in html


@pytest.mark.parametrize("status", ["failed", "needs_review"])
def test_failed_or_partial_discovery_does_not_claim_no_additional_sources(
    status: str,
) -> None:
    company = model("companies.Company").objects.create(name=f"Incomplete {status}")
    company.sources.create(
        source="jazzhr",
        source_jobs_url=f"https://incomplete-{status}.applytojob.com/apply",
        approval_status="approved",
        is_active=True,
    )
    run = discovery_run(company, status=status)
    if status == "needs_review":
        model("discovery.DiscoveryAdapterCheck").objects.create(
            run=run,
            platform="lever",
            status="not_checked",
            reason="Bounded search did not complete",
        )

    html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()

    assert "No additional sources found" not in html
    expected = (
        "Discovery could not be completed. Existing sources remain connected."
        if status == "failed"
        else "Search incomplete — some sources could not be checked."
    )
    assert html.count(expected) == 1


def test_failed_discovery_shows_safe_name_only_search_guidance() -> None:
    company = model("companies.Company").objects.create(name="Search Guidance")
    run = discovery_run(company, status="failed")
    run.error_code = "SearchConfigurationError"
    run.error_message = (
        "SOURCE_DISCOVERY_TAVILY_API_KEY is required; enable the explicit keyless "
        "diagnostic mode only for bounded diagnostics"
    )
    run.save(update_fields=("error_code", "error_message"))

    html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()

    assert "Discovery needs search configuration." in html
    assert "Supply the official domain to bypass search" in html
    assert "Official domain (optional)" in html
    assert "No job sources found." not in html


def test_sources_manager_uses_compact_rows_and_hides_technical_fields() -> None:
    company = model("companies.Company").objects.create(name="Compact Contract")
    source = company.sources.create(
        source="dreamjobs",
        source_jobs_url="https://careers.example.com/jobs",
        approval_status="approved",
        is_active=True,
    )
    run = discovery_run(company)
    discovery_candidate(
        run,
        url=source.source_jobs_url,
        platform="dreamjobs",
        decision="already_connected",
        company_source=source,
    )
    discovery_candidate(
        run,
        url="https://linkedin.com/company/compact-contract/jobs",
        kind="official_site",
        platform="",
        supported=False,
        decision="needs_review",
        official_site_eligibility="not_official_site",
        job_source_eligibility="external_job_board",
    )
    discovery_candidate(
        run,
        url="https://unknown.example/compact-contract/jobs",
        kind="careers",
        platform="greenhouse",
        supported=False,
        decision="unsupported",
        job_source_eligibility="unsupported_ats",
        evidence=["Unregistered ATS asset or host"],
    )

    html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()
    dialog = html[html.index('<dialog class="source-dialog"') : html.index("</dialog>")]

    assert 'class="source-row"' in dialog
    assert 'class="source-row-main"' in dialog
    assert 'class="discovery-source-row"' in dialog
    assert 'class="discovery-candidate"' not in dialog
    for forbidden in (
        "Confidence",
        "Evidence",
        "Origin",
        "Redirects",
        "Validation status",
        "Adapter coverage",
        "Platform checks",
        "Run #",
        "canonical identity",
    ):
        assert forbidden not in dialog


def test_sources_manager_render_does_not_search_or_mutate_discovery_state() -> None:
    company = model("companies.Company").objects.create(name="Read Only Manager")
    run = discovery_run(company)
    candidate = discovery_candidate(
        run,
        url="https://jobs.lever.co/read-only-manager",
    )
    before = (run.status, run.summary, candidate.decision, candidate.reason)

    with patch("discovery.service.configured_search_provider") as search_provider:
        response = client().get(
            reverse("companies:detail", args=(company.pk,)) + "?manage_sources=1"
        )

    assert response.status_code == 200
    search_provider.assert_not_called()
    run.refresh_from_db()
    candidate.refresh_from_db()
    assert (run.status, run.summary, candidate.decision, candidate.reason) == before


def test_actionable_candidate_is_not_lost_when_new_run_is_empty() -> None:
    company = model("companies.Company").objects.create(name="Candidate Lifecycle")
    completed = discovery_run(company, status="needs_review")
    candidate = discovery_candidate(
        completed,
        url="https://ascent.applytojob.com/apply",
        platform="jazzhr",
        supported=True,
        decision="selected",
        job_source_eligibility="supported_ats",
        evidence=["JazzHR technical signal"],
    )
    running = discovery_run(company, status="running")

    running_state = {
        "run_id": running.pk,
        "run_status": running.status,
        "candidate_id": candidate.pk,
        "candidate_run_id": candidate.run_id,
        "url": candidate.canonical_url,
        "platform": candidate.platform,
        "classification": candidate.job_source_eligibility,
        "decision": candidate.decision,
        "ignored": candidate.is_ignored,
        "company_source_id": candidate.company_source_id,
    }
    running_html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()

    running.status = "not_found"
    running.save(update_fields=("status",))
    candidate.refresh_from_db()
    finished_state = {
        "run_id": running.pk,
        "run_status": running.status,
        "candidate_id": candidate.pk,
        "candidate_run_id": candidate.run_id,
        "url": candidate.canonical_url,
        "platform": candidate.platform,
        "classification": candidate.job_source_eligibility,
        "decision": candidate.decision,
        "ignored": candidate.is_ignored,
        "company_source_id": candidate.company_source_id,
    }
    finished_html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()

    assert running_state == {
        "run_id": running.pk,
        "run_status": "running",
        "candidate_id": candidate.pk,
        "candidate_run_id": completed.pk,
        "url": "https://ascent.applytojob.com/apply",
        "platform": "jazzhr",
        "classification": "supported_ats",
        "decision": "selected",
        "ignored": False,
        "company_source_id": None,
    }
    assert finished_state == {**running_state, "run_status": "not_found"}
    assert "JazzHR" in running_html
    assert "Ready to connect" in running_html
    assert "JazzHR" in finished_html
    assert "Ready to connect" in finished_html


def test_running_candidate_is_published_only_after_run_completion() -> None:
    company = model("companies.Company").objects.create(name="Atomic Publication")
    run = discovery_run(company, status="running")
    candidate = discovery_candidate(
        run,
        url="https://ascent.applytojob.com/apply",
        platform="jazzhr",
        job_source_eligibility="supported_ats",
    )
    url = (
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    )

    running_html = client().get(url).content.decode()
    run.status = "needs_review"
    run.save(update_fields=("status",))
    completed_html = client().get(url).content.decode()

    assert "Searching for job sources" in running_html
    assert candidate.canonical_url not in running_html
    assert "No job sources found." not in running_html
    assert candidate.canonical_url in completed_html
    assert "Ready to connect" in completed_html


def test_running_failed_and_partial_runs_preserve_last_published_snapshot() -> None:
    company = model("companies.Company").objects.create(name="Stable Snapshot")
    completed = discovery_run(company, status="needs_review")
    saved = discovery_candidate(
        completed,
        url="https://ascent.applytojob.com/apply",
        platform="jazzhr",
        job_source_eligibility="supported_ats",
    )
    page_url = (
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    )

    running = discovery_run(company, status="running")
    partial_candidate = discovery_candidate(
        running,
        url="https://jobs.lever.co/not-published",
        job_source_eligibility="supported_ats",
    )
    running_html = client().get(page_url).content.decode()

    running.status = "failed"
    running.save(update_fields=("status",))
    failed_html = client().get(page_url).content.decode()

    partial = discovery_run(company, status="needs_review")
    published_partial_candidate = discovery_candidate(
        partial,
        url="https://jobs.lever.co/published-after-completion",
        job_source_eligibility="supported_ats",
    )
    model("discovery.DiscoveryAdapterCheck").objects.create(
        run=partial,
        platform="darwinbox",
        status="not_checked",
        reason="Bounded query limit",
    )
    partial_html = client().get(page_url).content.decode()

    for html in (running_html, failed_html, partial_html):
        assert saved.canonical_url in html
        assert "No job sources found." not in html
    assert partial_candidate.canonical_url not in running_html
    assert partial_candidate.canonical_url not in failed_html
    assert partial_candidate.canonical_url not in partial_html
    assert published_partial_candidate.canonical_url in partial_html
    assert "Searching for job sources" in running_html
    assert "Discovery could not be completed" in failed_html
    assert "Search incomplete" in partial_html


def test_historical_candidate_connects_with_revalidation_and_moves_to_connected() -> None:
    company = model("companies.Company").objects.create(name="Historical Connect")
    completed = discovery_run(company, status="needs_review")
    candidate = discovery_candidate(
        completed,
        url="https://ascent.applytojob.com/apply",
        platform="jazzhr",
        job_source_eligibility="supported_ats",
    )
    discovery_run(company, status="not_found")
    manager_url = (
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    )

    first_open = client().get(manager_url).content.decode()
    reopened = client().get(manager_url).content.decode()
    with patch("discovery.service.validate_source_configuration") as validator:
        connected = client().post(
            reverse("discovery:connect", args=(company.pk, candidate.pk))
        )
    repeated = client().post(
        reverse("discovery:connect", args=(company.pk, candidate.pk))
    )
    after = client().get(manager_url).content.decode()

    assert candidate.canonical_url in first_open
    assert candidate.canonical_url in reopened
    assert connected.status_code == 302
    validator.assert_called_once_with(
        source="jazzhr", source_jobs_url=candidate.canonical_url
    )
    assert repeated.status_code == 302
    assert company.sources.count() == 1
    source = company.sources.get()
    assert source.source == "jazzhr"
    assert source.source_jobs_url == candidate.canonical_url
    candidate.refresh_from_db()
    assert candidate.company_source_id == source.pk
    discovered_start = after.index('id="sources-panel-discovered"')
    discovered = after[discovered_start : after.index("</section>", discovered_start)]
    assert candidate.canonical_url not in discovered
    assert ">Connected (1)</button>" in after


def test_historical_supported_review_candidate_remains_ready_to_confirm() -> None:
    company = model("companies.Company").objects.create(name="Fallback Review")
    completed = discovery_run(company, status="needs_review")
    candidate = discovery_candidate(
        completed,
        url="https://ascent.applytojob.com/apply",
        platform="jazzhr",
        supported=True,
        decision="needs_review",
        job_source_eligibility="supported_ats",
        evidence=[
            "Candidate came from search fallback, not direct site verification",
            "JazzHR technical signal",
        ],
    )
    model("discovery.DiscoveryAdapterCheck").objects.create(
        run=completed,
        platform="darwinbox",
        status="not_checked",
    )

    html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()

    assert candidate.canonical_url in html
    assert "Ready to connect" in html
    assert reverse("discovery:confirm", args=(company.pk, candidate.pk)) in html


def test_acuity_two_source_partial_inventory_connects_independently() -> None:
    darwinbox_url = "https://acuitykp.darwinbox.com/ms/candidate/careers"
    jazzhr_url = "https://ascent.applytojob.com/apply"
    deleted = model("companies.Company").objects.create(name="Acuity Analytics")
    deleted.sources.create(
        source="darwinbox",
        source_jobs_url=darwinbox_url,
        approval_status="approved",
        is_active=True,
    )
    deleted.delete()
    company = model("companies.Company").objects.create(name="Acuity Analytics")
    run = discovery_run(company, status="needs_review")
    jazzhr = discovery_candidate(
        run,
        url=jazzhr_url,
        platform="jazzhr",
        job_source_eligibility="supported_ats",
    )
    darwinbox = discovery_candidate(
        run,
        url=darwinbox_url,
        platform="darwinbox",
        job_source_eligibility="supported_ats",
    )
    model("discovery.DiscoveryAdapterCheck").objects.create(
        run=run,
        platform="lever",
        status="not_checked",
    )
    discovered_url = (
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    )

    first = client().get(discovered_url).content.decode()
    refreshed = client().get(discovered_url).content.decode()
    assert first.count("Ready to connect") == 2
    assert jazzhr_url in first and darwinbox_url in first
    assert jazzhr_url in refreshed and darwinbox_url in refreshed

    client().post(reverse("discovery:connect", args=(company.pk, jazzhr.pk)))
    after_jazzhr = client().get(discovered_url).content.decode()
    discovered_start = after_jazzhr.index('id="sources-panel-discovered"')
    discovered = after_jazzhr[
        discovered_start : after_jazzhr.index("</section>", discovered_start)
    ]
    assert ">Connected (1)</button>" in after_jazzhr
    assert jazzhr_url not in discovered
    assert darwinbox_url in discovered
    assert "Ready to connect" in discovered

    client().post(reverse("discovery:connect", args=(company.pk, darwinbox.pk)))
    after_darwinbox = client().get(discovered_url).content.decode()
    assert ">Connected (2)</button>" in after_darwinbox
    assert company.sources.count() == 2


def test_company_inventory_deduplicates_canonical_url_and_tenant_identity() -> None:
    company = model("companies.Company").objects.create(name="Canonical Inventory")
    older = discovery_run(company, status="needs_review")
    discovery_candidate(
        older,
        url="https://ascent.applytojob.com/apply/",
        platform="jazzhr",
        job_source_eligibility="supported_ats",
    )
    newer = discovery_run(company, status="needs_review")
    discovery_candidate(
        newer,
        url="https://ascent.applytojob.com/apply",
        platform="jazzhr",
        job_source_eligibility="supported_ats",
    )

    html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()

    assert html.count('data-candidate-state="ready_to_connect"') == 1


@pytest.mark.parametrize(
    ("status", "expected", "forbidden"),
    [
        ("running", "Searching for job sources", "No job sources found."),
        ("failed", "Discovery could not be completed", "No job sources found."),
        ("not_found", "No job sources found.", "Searching for job sources"),
    ],
)
def test_empty_state_requires_a_complete_successful_empty_run(
    status: str, expected: str, forbidden: str
) -> None:
    company = model("companies.Company").objects.create(name=f"Empty {status}")
    discovery_run(company, status=status)

    html = client().get(
        reverse("companies:detail", args=(company.pk,))
        + "?manage_sources=1&source_tab=discovered"
    ).content.decode()

    assert expected in html
    assert forbidden not in html


def test_sources_dialog_css_prevents_nested_and_horizontal_overflow() -> None:
    css = (Path(__file__).resolve().parents[1] / "static" / "css" / "app.css").read_text(
        encoding="utf-8"
    )

    assert ".source-dialog-header {" in css
    assert "position: sticky;" in css
    assert "overflow-x: hidden;" in css
    assert "text-overflow: ellipsis;" in css
    assert "body:has(.source-dialog[open])" in css
    assert "width: min(60rem, calc(100vw - 3rem));" in css
    assert "max-height: calc(100vh - 3rem);" in css
    assert "width: calc(100vw - 2rem);" in css
    assert "scrollbar-gutter: stable;" in css
