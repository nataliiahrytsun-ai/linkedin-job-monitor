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


def create_scrape_run(
    company: Any,
    *,
    status: str,
    started_at: datetime,
    error_message: str = "",
) -> Any:
    terminal = status != "running"
    return model("scrape_runs.ScrapeRun").objects.create(
        company=company,
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
    assert '<link rel="stylesheet" href="/static/css/app.css">' in html
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
    assert "2026-08-07 14:33" in html
    assert "Add company" in html
    assert html.count(f'href="{reverse("companies:create")}"') == 1
    assert f'href="{reverse("companies:detail", args=(alpha.pk,))}"' in html
    assert f'href="{reverse("companies:detail", args=(beta.pk,))}"' in html
    assert f'href="{reverse("companies:detail", args=(zulu.pk,))}"' in html
    assert "Edit" in html
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
    assert f'href="{reverse("companies:edit", args=(alpha.pk,))}"' in table_html
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
    tablet_alignment_start = tablet_css.index(
        "@media (min-width: 48.01rem) and (max-width: 70rem)"
    )
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
    assert "fixture" in html
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
        "Vacancy source",
        "Jobs URL",
        "Last run status",
        "Last run time",
        "Active jobs",
    ):
        assert label in html
    assert "Source jobs URL" not in html
    assert 'class="detail-row detail-source-row"' in html
    assert 'class="detail-row detail-monitoring-row"' in html
    assert html.index("Vacancy source") < html.index("Jobs URL")
    assert html.index("Jobs URL") < html.index("Last run status")
    assert html.index("Last run status") < html.index("Last run time")
    assert html.index("Last run time") < html.index("Active jobs")
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
    closed = create_job(
        company, source_job_id="closed-job", title="Closed Role", status="closed"
    )
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
    assert jobs_html.count('<th scope="col">') == 5
    assert "Original job link" not in jobs_html
    assert ">Open</a>" not in jobs_html
    for posting in (newer, older, closed):
        assert (
            f'<a href="{reverse("jobs:detail", args=(posting.pk,))}">'
            f"{posting.title}</a>"
        ) in jobs_html
    assert 'class="status-badge status-active">ACTIVE</span>' in jobs_html
    assert 'class="status-badge">NOT_FOUND</span>' in jobs_html
    assert 'class="status-badge">CLOSED</span>' in jobs_html
    assert 'class="status-badge status-active">NOT_FOUND</span>' not in jobs_html
    assert 'class="status-badge status-active">CLOSED</span>' not in jobs_html


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

    assert (
        f'action="{reverse("companies:detail", args=(company.pk,))}"' in filter_form
    )
    assert (
        f'href="{reverse("companies:detail", args=(company.pk,))}">Clear filters</a>'
        in filter_form
    )
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
    job = create_job(company)
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
    assert "2026-08-06 12:00" in response.content.decode()
    assert model("companies.Company").objects.values().get(pk=company.pk) == company_before
    assert model("jobs.JobPosting").objects.values().get(pk=job.pk) == job_before
    assert model("scrape_runs.ScrapeRun").objects.count() == 0
    pipeline.assert_not_called()
    background_submit.assert_not_called()


def test_company_detail_update_jobs_action_is_csrf_protected_post_form() -> None:
    company = create_company()
    update_url = reverse("companies:update_jobs", args=(company.pk,))

    html = client().get(reverse("companies:detail", args=(company.pk,))).content.decode()

    assert f'action="{update_url}"' in html
    action_index = html.index(f'action="{update_url}"')
    update_form = html[html.rfind("<form", 0, action_index) :]
    update_form = update_form[: update_form.index("</form>")]
    assert 'method="post"' in update_form
    assert 'name="csrfmiddlewaretoken"' in update_form
    assert "Update jobs" in update_form
    assert "disabled" not in update_form


def test_runtime_fixture_setting_uses_tracked_demo_data_not_test_fixtures() -> None:
    settings = importlib.import_module("django.conf").settings
    expected = Path(settings.BASE_DIR) / "data" / "fixtures" / "demo_jobs.json"
    test_fixture = Path(__file__).parent / "fixtures" / "backend" / "run_1.json"
    view_source = (
        Path(__file__).resolve().parents[1] / "companies" / "views.py"
    ).read_text(encoding="utf-8")

    assert isinstance(settings.JOB_MONITOR_FIXTURE_PATH, Path)
    assert expected == settings.JOB_MONITOR_FIXTURE_PATH
    assert expected.is_file()
    assert expected.read_text(encoding="utf-8") == test_fixture.read_text(
        encoding="utf-8"
    )
    assert '"tests" / "fixtures"' not in view_source


def test_update_jobs_requires_post_and_get_starts_nothing() -> None:
    company = create_company()
    update_url = reverse("companies:update_jobs", args=(company.pk,))

    with patch("companies.views.background_executor.submit_pipeline") as submit:
        response = client().get(update_url)

    assert response.status_code == 405
    submit.assert_not_called()
    assert model("scrape_runs.ScrapeRun").objects.count() == 0


def test_update_jobs_submits_source_neutral_background_task_and_redirects() -> None:
    company = create_company()
    browser = client()
    update_url = reverse("companies:update_jobs", args=(company.pk,))
    with patch("companies.views.background_executor.submit_pipeline") as submit:
        response = browser.post(update_url)

    assert response.status_code == 302
    assert response.url == f'{reverse("companies:detail", args=(company.pk,))}?watch_after=0'
    submit.assert_called_once()
    assert submit.call_args.kwargs["company"].pk == company.pk
    assert set(submit.call_args.kwargs) == {"company"}
    detail_html = browser.get(response.url).content.decode()
    assert "Job update started." in detail_html
    assert 'id="company-run-polling"' in detail_html
    assert 'data-baseline-run-id="0"' in detail_html
    assert 'data-mode="new"' in detail_html
    assert 'src="/static/js/company_run_polling.js"' in detail_html
    javascript = Path("static/js/company_run_polling.js").read_text(encoding="utf-8")
    assert "maxNewRunChecks = 24" in javascript
    assert "remainingNewRunChecks <= 0" in javascript


def test_update_jobs_view_does_not_inspect_configured_fixture_path(
    tmp_path: Path,
) -> None:
    company = create_company()
    browser = client()
    missing_fixture = tmp_path / "missing-demo.json"
    override_settings = importlib.import_module("django.test").override_settings

    with (
        override_settings(JOB_MONITOR_FIXTURE_PATH=missing_fixture),
        patch("companies.views.background_executor.submit_pipeline") as submit,
    ):
        response = browser.post(reverse("companies:update_jobs", args=(company.pk,)))

    assert response.status_code == 302
    assert response.url == f'{reverse("companies:detail", args=(company.pk,))}?watch_after=0'
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
        {"watch_after": old_run.pk},
    )
    assert 'data-mode="new"' in watched_page.content.decode()

    new_run = create_scrape_run(
        company,
        status="success",
        started_at=datetime(2026, 8, 11, 9, 1, tzinfo=UTC),
    )
    response = client().get(
        reverse("scrape_runs:status"),
        {"company_id": company.pk, "ids": old_run.pk},
    )
    payload = response.json()

    assert payload["company_latest_run"]["id"] == new_run.pk
    assert payload["company_latest_run"]["status"] == "success"
    assert payload["company_latest_run"]["is_terminal"] is True


def test_company_polling_tracks_running_to_terminal_then_stops() -> None:
    company = create_company()
    running = create_scrape_run(
        company,
        status="running",
        started_at=datetime(2026, 8, 11, 10, tzinfo=UTC),
    )

    running_html = client().get(
        reverse("companies:detail", args=(company.pk,))
    ).content.decode()
    assert 'data-mode="running"' in running_html
    assert f'data-baseline-run-id="{running.pk}"' in running_html

    finish_scrape_run = importlib.import_module(
        "scraping.run_lifecycle"
    ).finish_scrape_run
    finish_scrape_run(
        scrape_run=running,
        status="success",
        finished_at=datetime(2026, 8, 11, 10, 0, 1, tzinfo=UTC),
        jobs_found=1,
        jobs_created=1,
        jobs_updated=0,
        requests_made=0,
    )
    endpoint_payload = client().get(
        reverse("scrape_runs:status"),
        {"company_id": company.pk, "ids": running.pk},
    ).json()
    terminal_html = client().get(
        reverse("companies:detail", args=(company.pk,))
    ).content.decode()

    assert endpoint_payload["company_latest_run"]["is_terminal"] is True
    assert 'id="company-run-polling"' not in terminal_html
    javascript = Path("static/js/company_run_polling.js").read_text(encoding="utf-8")
    assert 'mode === "running"' in javascript
    assert "latestRun.is_terminal" in javascript
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

    payload = client().get(
        reverse("scrape_runs:status"),
        {"company_id": watched_company.pk, "ids": watched_run.pk},
    ).json()

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
        patch("companies.views.background_executor.submit_pipeline") as submit,
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

    with patch("companies.views.background_executor.submit_pipeline") as submit:
        response = browser.post(reverse("companies:update_jobs", args=(company.pk,)))

    assert response.status_code == 302
    submit.assert_not_called()
    company.refresh_from_db()
    assert company.is_active is False
    assert b"Activate this company before updating jobs." in browser.get(
        response.url
    ).content


def test_update_jobs_reports_controlled_unknown_source_submission_error() -> None:
    company = create_company(source="not-permitted")
    browser = client()

    source_error = importlib.import_module("scraping.background").BackgroundSourceError
    with patch(
        "companies.views.background_executor.submit_pipeline",
        side_effect=source_error("unknown source"),
    ) as submit:
        response = browser.post(reverse("companies:update_jobs", args=(company.pk,)))

    assert response.status_code == 302
    submit.assert_called_once_with(company=company)
    assert b"Job update could not be started." in browser.get(response.url).content


def test_update_jobs_reports_existing_running_run_without_submission() -> None:
    company = create_company()
    model("scrape_runs.ScrapeRun").objects.create(company=company)
    browser = client()

    with patch("companies.views.background_executor.submit_pipeline") as submit:
        response = browser.post(reverse("companies:update_jobs", args=(company.pk,)))

    assert response.status_code == 302
    submit.assert_not_called()
    assert model("scrape_runs.ScrapeRun").objects.filter(company=company).count() == 1
    assert b"A job update is already running for this company." in browser.get(
        response.url
    ).content


def test_update_jobs_reports_executor_duplicate_without_second_run() -> None:
    company = create_company()
    browser = client()
    duplicate_error = importlib.import_module(
        "scraping.background"
    ).BackgroundRunAlreadyScheduledError

    with patch(
        "companies.views.background_executor.submit_pipeline",
        side_effect=duplicate_error("already scheduled"),
    ) as submit:
        response = browser.post(reverse("companies:update_jobs", args=(company.pk,)))

    assert response.status_code == 302
    submit.assert_called_once()
    assert model("scrape_runs.ScrapeRun").objects.count() == 0
    assert b"A job update is already running for this company." in browser.get(
        response.url
    ).content


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
        "Source",
        "Source jobs URL",
        "Monitoring active",
    ):
        assert label in html
    for excluded in ("last_scrape_status", "last_scraped_at", "created_at", "updated_at"):
        assert f'name="{excluded}"' not in html
    assert '<option value="client">Customer</option>' in html
    assert '<option value="supplier">Supplier</option>' in html
    assert '<option value="other" selected>Other</option>' in html
    assert '<select name="source"' in html
    assert 'type="text" name="source"' not in html
    assert '<option value="lever">Lever</option>' in html
    assert '<option value="fixture">Fixture</option>' not in html
    assert "Kunde" not in html
    assert "Sonstige" not in html
    assert html.count("<h1") == 1
    assert model("companies.Company").objects.count() == 0


def test_create_source_options_come_from_user_selectable_registry_api() -> None:
    response = client().get(reverse("companies:create"))
    source_field = response.context["form"].fields["source"]
    rendered_values = tuple(value for value, _label in source_field.choices)
    selectable_values = importlib.import_module(
        "scraping.sources.registry"
    ).user_selectable_source_keys()

    assert rendered_values == selectable_values
    assert ("lever", "Lever") in tuple(source_field.choices)
    assert all(value != "fixture" for value, _label in source_field.choices)


def test_valid_create_uses_redirect_normalizes_source_and_refresh_does_not_duplicate() -> None:
    browser = client()
    response = browser.post(
        reverse("companies:create"),
        valid_company_data(source="  LEVER  "),
    )

    assert response.status_code == 302
    assert response.url == reverse("companies:list")
    company = model("companies.Company").objects.get()
    assert company.source == "lever"
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
    assert '<option value="lever" selected>Lever</option>' in html
    assert model("companies.Company").objects.count() == 0


def test_create_rejects_unsupported_source_submitted_directly() -> None:
    response = client().post(
        reverse("companies:create"),
        valid_company_data(source="https://jobs.lever.co/olo"),
    )

    assert response.status_code == 200
    assert "Select a valid choice" in response.content.decode()
    assert model("companies.Company").objects.count() == 0


def test_create_rejects_internal_fixture_source_submitted_directly() -> None:
    response = client().post(
        reverse("companies:create"),
        valid_company_data(source="fixture"),
    )

    assert response.status_code == 200
    assert "Select a valid choice" in response.content.decode()
    assert model("companies.Company").objects.count() == 0


def test_duplicate_constraint_is_displayed_as_form_error() -> None:
    create_company(source="lever")

    response = client().post(
        reverse("companies:create"),
        valid_company_data(
            source="  LEVER  ",
            source_jobs_url="https://jobs.example.test/example/openings",
        ),
    )

    assert response.status_code == 200
    assert response.context["form"].errors
    assert model("companies.Company").objects.count() == 1


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
    assert '<select name="source"' in html
    assert '<option value="lever" selected>Lever</option>' in html
    assert model("companies.Company").objects.values().get(pk=company.pk) == before


def test_edit_get_shows_existing_internal_source_without_selecting_lever() -> None:
    company = create_company(name="Acme GmbH", source="fixture")

    response = client().get(reverse("companies:edit", args=(company.pk,)))
    html = response.content.decode()

    assert response.status_code == 200
    assert '<option value="fixture" selected>Fixture (internal)</option>' in html
    assert '<option value="lever">Lever</option>' in html
    assert '<option value="lever" selected>' not in html


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
    assert b"was updated" in browser.get(response.url).content


def test_edit_rejects_internal_fixture_source() -> None:
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

    assert response.status_code == 200
    assert "Select a valid choice" in response.content.decode()
    company.refresh_from_db()
    assert company.source == "lever"


def test_invalid_edit_does_not_save_partial_changes() -> None:
    company = create_company(name="Original Name", source="fixture")

    response = client().post(
        reverse("companies:edit", args=(company.pk,)),
        valid_company_data(name="", source="lever"),
    )

    assert response.status_code == 200
    assert '<option value="lever" selected>Lever</option>' in response.content.decode()
    company.refresh_from_db()
    assert company.name == "Original Name"
    assert company.source == "fixture"


def test_edit_rejects_unsupported_source_submitted_directly() -> None:
    company = create_company(source="lever", source_jobs_url="https://jobs.lever.co/olo")

    response = client().post(
        reverse("companies:edit", args=(company.pk,)),
        valid_company_data(source="not-registered"),
    )

    assert response.status_code == 200
    assert "Select a valid choice" in response.content.decode()
    company.refresh_from_db()
    assert company.source == "lever"


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
