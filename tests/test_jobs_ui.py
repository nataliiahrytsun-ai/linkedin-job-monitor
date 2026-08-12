from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(scope="module", autouse=True)
def migrated_database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    apps = importlib.import_module("django.apps").apps
    if not apps.ready:
        database_path = tmp_path_factory.mktemp("jobs-ui-db") / "jobs.sqlite3"
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


def reverse(name: str, *, kwargs: dict[str, object] | None = None) -> str:
    return str(importlib.import_module("django.urls").reverse(name, kwargs=kwargs))


def company(
    *, name: str, company_type: str = "other", sequence: int = 1
) -> Any:
    return model("companies.Company").objects.create(
        name=name,
        company_type=company_type,
        source="fixture",
        source_jobs_url=f"https://jobs.example.test/company-{sequence}",
    )


def job(company_record: Any, *, sequence: int, **overrides: object) -> Any:
    values: dict[str, object] = {
        "company": company_record,
        "source": "fixture",
        "source_job_id": f"job-{sequence}",
        "title": f"Role {sequence}",
        "country": "Austria",
        "location": "Vienna",
        "workplace_type": "onsite",
        "published_at": datetime(2026, 8, sequence, 9, tzinfo=UTC),
        "first_seen_at": datetime(2026, 8, sequence, 10, tzinfo=UTC),
        "content_hash": f"{sequence:064x}",
        "dedupe_key": f"{sequence + 100:064x}",
        "status": "active",
    }
    values.update(overrides)
    return model("jobs.JobPosting").objects.create(**values)


def rendered_titles(response: Any) -> list[str | None]:
    return [posting.title for posting in response.context["jobs"]]


def test_jobs_page_returns_200_and_shows_multiple_companies() -> None:
    customer = company(name="Alpha Customer", company_type="client", sequence=1)
    supplier = company(name="Beta Supplier", company_type="supplier", sequence=2)
    older = job(customer, sequence=1, title="Data Analyst")
    newer = job(
        supplier,
        sequence=2,
        title="Platform Engineer",
        location="Graz",
        country="Austria",
        source_job_url="https://jobs.example.test/jobs/platform-engineer",
    )

    response = client().get(reverse("jobs:list"))
    html = response.content.decode()

    assert response.status_code == 200
    assert [posting.pk for posting in response.context["jobs"]] == [newer.pk, older.pk]
    assert "Alpha Customer" in html
    assert "Beta Supplier" in html
    assert "Data Analyst" in html
    assert "Platform Engineer" in html
    assert "Published" in html
    assert "First seen" in html
    assert 'aria-current="page">Jobs</a>' in html


def test_company_filter_uses_existing_company_choices() -> None:
    alpha = company(name="Alpha", sequence=1)
    beta = company(name="Beta", sequence=2)
    job(alpha, sequence=1, title="Alpha Role")
    job(beta, sequence=2, title="Beta Role")

    response = client().get(reverse("jobs:list"), {"company": str(beta.pk)})
    html = response.content.decode()

    assert rendered_titles(response) == ["Beta Role"]
    assert "Alpha" in html and "Beta" in html
    assert f'<option value="{beta.pk}" selected>Beta</option>' in html


@pytest.mark.parametrize(
    ("parameters", "expected_title"),
    [
        ({"company_type": "supplier"}, "Supplier Engineer"),
        ({"country": "Germany"}, "Berlin Engineer"),
        ({"location": "berlin"}, "Berlin Engineer"),
        ({"q": "supplier"}, "Supplier Engineer"),
        ({"status": "not_found"}, "Former Analyst"),
        ({"workplace_type": "remote"}, "Remote Designer"),
    ],
)
def test_single_value_filters(
    parameters: dict[str, str], expected_title: str
) -> None:
    customer = company(name="Customer", company_type="client", sequence=1)
    supplier = company(name="Supplier", company_type="supplier", sequence=2)
    job(customer, sequence=1, title="Berlin Engineer", country="Germany", location="Berlin")
    job(
        supplier,
        sequence=2,
        title="Supplier Engineer",
        country="Austria",
        location="Vienna",
    )
    job(customer, sequence=3, title="Former Analyst", status="not_found")
    job(customer, sequence=4, title="Remote Designer", workplace_type="remote")

    response = client().get(reverse("jobs:list"), parameters)

    assert rendered_titles(response) == [expected_title]


def test_publication_date_from_and_to_filters() -> None:
    employer = company(name="Dates", sequence=1)
    job(employer, sequence=1, title="Early")
    job(employer, sequence=2, title="Middle")
    job(employer, sequence=3, title="Late")

    from_response = client().get(reverse("jobs:list"), {"published_from": "2026-08-02"})
    to_response = client().get(reverse("jobs:list"), {"published_to": "2026-08-02"})

    assert rendered_titles(from_response) == ["Late", "Middle"]
    assert rendered_titles(to_response) == ["Middle", "Early"]


def test_first_seen_date_from_and_to_filters_and_invalid_values() -> None:
    employer = company(name="First Seen Dates", sequence=1)
    job(employer, sequence=1, title="Early")
    job(employer, sequence=2, title="Middle")
    job(employer, sequence=3, title="Late")

    from_response = client().get(reverse("jobs:list"), {"first_seen_from": "2026-08-02"})
    to_response = client().get(reverse("jobs:list"), {"first_seen_to": "2026-08-02"})
    invalid_response = client().get(
        reverse("jobs:list"),
        {"first_seen_from": "not-a-date", "first_seen_to": "also-invalid"},
    )

    assert rendered_titles(from_response) == ["Late", "Middle"]
    assert rendered_titles(to_response) == ["Middle", "Early"]
    assert invalid_response.status_code == 200
    assert rendered_titles(invalid_response) == ["Late", "Middle", "Early"]


def test_filters_can_be_combined() -> None:
    customer = company(name="Customer", company_type="client", sequence=1)
    supplier = company(name="Supplier", company_type="supplier", sequence=2)
    job(customer, sequence=1, title="Remote Analyst", workplace_type="remote")
    match = job(
        supplier,
        sequence=2,
        title="Remote Analyst",
        workplace_type="remote",
        location="Vienna",
        country="Austria",
    )
    job(supplier, sequence=3, title="Onsite Analyst", workplace_type="onsite")

    response = client().get(
        reverse("jobs:list"),
        {
            "company_type": "supplier",
            "country": "Austria",
            "location": "Vienna",
            "q": "Analyst",
            "status": "active",
            "workplace_type": "remote",
            "published_from": "2026-08-01",
            "published_to": "2026-08-02",
            "first_seen_from": "2026-08-02",
            "first_seen_to": "2026-08-02",
        },
    )

    assert [posting.pk for posting in response.context["jobs"]] == [match.pk]


def test_invalid_values_are_safe_and_selected_values_are_preserved() -> None:
    employer = company(name="Safe Filters", sequence=1)
    job(employer, sequence=1, title="Safe Role")

    invalid = client().get(
        reverse("jobs:list"),
        {"company": "unknown", "status": "bogus", "published_from": "not-a-date"},
    )
    selected = client().get(
        reverse("jobs:list"),
        {
            "q": "Safe",
            "country": "Austria",
            "location": "Vienna",
            "workplace_type": "onsite",
        },
    )
    selected_html = selected.content.decode()

    assert invalid.status_code == 200
    assert rendered_titles(invalid) == ["Safe Role"]
    assert "Some invalid filter values were ignored." in invalid.content.decode()
    assert 'value="Safe"' in selected_html
    assert '<option value="Austria" selected>Austria</option>' in selected_html
    assert 'value="Vienna"' in selected_html
    assert '<option value="onsite" selected>Onsite</option>' in selected_html


def test_per_column_filters_share_one_form_and_clear_targets_clean_url() -> None:
    employer = company(name="Column Filters", sequence=1)
    job(employer, sequence=1, title="Anything")
    response = client().get(
        reverse("jobs:list"),
        {"q": "anything", "country": "Austria"},
    )
    html = response.content.decode()

    assert f'href="{reverse("jobs:list")}">Clear filters</a>' in html
    assert ">Filters<" not in html
    assert "job-filter-disclosure" not in html
    assert "job-filter-panel" not in html
    assert html.count('<details class="column-filter') == 7
    for label in (
        "Filter position",
        "Filter company",
        "Filter location",
        "Filter country",
        "Filter published date",
        "Filter first seen date",
        "Filter status",
    ):
        assert f'aria-label="{label}"' in html

    form_start = html.index('<form class="jobs-table-filter-form"')
    filter_form = html[form_start : html.index("</form>", form_start)]
    for field_name in (
        "q",
        "company",
        "company_type",
        "country",
        "location",
        "workplace_type",
        "published_from",
        "published_to",
        "first_seen_from",
        "first_seen_to",
        "status",
    ):
        assert filter_form.count(f'name="{field_name}"') == 1
    assert filter_form.count(">Apply</button>") == 2
    assert filter_form.count('class="column-filter-actions"') == 2
    assert '<script src="/static/js/job_filters.js" defer></script>' in html
    assert html.count("column-filter-active") == 2


def test_jobs_filters_use_expected_autosubmit_events() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "static" / "js" / "job_filters.js"
    ).read_text(encoding="utf-8")

    assert "select, input[type='date']" in script
    assert 'control.addEventListener("change"' in script
    assert "input[type='text']" in script
    assert 'control.addEventListener("keydown"' in script
    assert 'event.key === "Enter"' in script
    assert "form.requestSubmit()" in script


def test_jobs_filters_share_one_viewport_aware_popover_controller() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "static" / "js" / "job_filters.js"
    ).read_text(encoding="utf-8")

    assert 'filter.addEventListener("toggle"' in script
    assert "otherFilter.open = false" in script
    assert 'document.addEventListener("pointerdown"' in script
    assert 'event.key === "Escape"' in script
    assert 'window.addEventListener("resize"' in script
    assert 'window.addEventListener("orientationchange"' in script
    assert "trigger.getBoundingClientRect()" in script
    assert "popover.getBoundingClientRect()" in script
    assert "window.innerWidth - safeMargin - popoverRect.width" in script
    assert "window.innerHeight - safeMargin - popoverRect.height" in script
    assert 'window.matchMedia("(max-width: 40rem)").matches' in script
    assert "top = triggerRect.bottom + triggerGap" in script
    assert "popover.style.maxHeight = `${availableHeight}px`" in script


def test_jobs_filter_popovers_are_fixed_overlays_that_cannot_resize_the_table() -> None:
    stylesheet = (
        Path(__file__).resolve().parents[1] / "static" / "css" / "app.css"
    ).read_text(encoding="utf-8")

    assert """.column-filter-popover {
  position: fixed;
  z-index: 3;
  width: 18rem;
  max-width: calc(100vw - 24px);
  max-height: calc(100vh - 24px);""" in stylesheet
    assert "table-layout: fixed;" in stylesheet
    assert "nth-last-child(-n + 3) .column-filter-popover" not in stylesheet
    assert "nth-child(even) .column-filter-popover" not in stylesheet


def test_jobs_table_header_is_sticky_except_in_mobile_card_layout() -> None:
    stylesheet = (
        Path(__file__).resolve().parents[1] / "static" / "css" / "app.css"
    ).read_text(encoding="utf-8")

    sticky_header = """.global-jobs-table thead {
  position: sticky;
  z-index: 2;
  top: 0;
  background: #f9fafb;
}"""
    mobile_header = """.global-jobs-table thead {
    position: static;
    z-index: auto;
    margin-bottom: 0.75rem;
  }"""

    assert sticky_header in stylesheet
    assert mobile_header in stylesheet
    assert ".global-jobs-table-container {\n  overflow: visible;" in stylesheet


def test_country_select_uses_unique_nonempty_sorted_stored_values() -> None:
    employer = company(name="Countries", sequence=1)
    job(employer, sequence=1, country="Germany")
    job(employer, sequence=2, country="Austria")
    job(employer, sequence=3, country="Austria")
    job(employer, sequence=4, country=None)
    job(employer, sequence=5, country="")

    html = client().get(reverse("jobs:list")).content.decode()
    select_start = html.index('<select name="country"')
    country_select = html[select_start : html.index("</select>", select_start)]

    assert '<option value="" selected>All countries</option>' in country_select
    assert country_select.count('value="Austria"') == 1
    assert country_select.count('value="Germany"') == 1
    assert country_select.index("Austria") < country_select.index("Germany")
    assert ">None<" not in country_select


def test_empty_database_and_no_match_are_distinct() -> None:
    empty_html = client().get(reverse("jobs:list")).content.decode()
    employer = company(name="Existing Jobs", sequence=1)
    job(employer, sequence=1, title="Existing Role")
    no_match_html = client().get(reverse("jobs:list"), {"q": "missing"}).content.decode()

    assert "No jobs available yet." in empty_html
    assert "No jobs match the selected filters." not in empty_html
    assert "No jobs match the selected filters." in no_match_html
    assert "No jobs available yet." not in no_match_html


def test_list_position_always_links_to_internal_detail() -> None:
    employer = company(name="Optional Values", sequence=1)
    linked = job(
        employer,
        sequence=1,
        title="Linked Role",
        source_job_url="https://jobs.example.test/jobs/linked",
    )
    missing = job(
        employer,
        sequence=2,
        title=None,
        country=None,
        location=None,
        published_at=None,
        workplace_type=None,
    )

    html = client().get(reverse("jobs:list")).content.decode()

    assert "Original" not in html
    assert ">Open</a>" not in html
    assert linked.source_job_url not in html
    assert f'href="{reverse("jobs:detail", kwargs={"pk": linked.pk})}"' in html
    assert f'href="{reverse("jobs:detail", kwargs={"pk": missing.pk})}"' in html
    assert f'title="{linked.title}"' in html
    assert f'>{linked.title}</a>' in html
    assert ">—</a>" in html
    assert missing.source_job_id not in html
    assert "None" not in html
    assert "—" in html


def test_job_detail_displays_stored_fields_and_related_links() -> None:
    employer = company(name="Detail Customer", company_type="client", sequence=1)
    posting = job(
        employer,
        sequence=4,
        title="Senior Platform Engineer",
        source="fixture",
        source_job_id="external-456",
        source_job_url="https://jobs.example.test/jobs/external-456",
        location="Vienna, Austria",
        country="Austria",
        city="Vienna",
        workplace_type="hybrid",
        employment_type="full-time",
        seniority_level="manager",
        job_function="sales",
        industry="software",
        description="Build reliable systems.\nWork with the platform team.",
        first_seen_at=datetime(2026, 8, 4, 10, tzinfo=UTC),
        last_seen_at=datetime(2026, 8, 5, 11, tzinfo=UTC),
    )

    response = client().get(reverse("jobs:detail", kwargs={"pk": posting.pk}))
    html = response.content.decode()

    assert response.status_code == 200
    assert response.context["job"].pk == posting.pk
    assert "Senior Platform Engineer" in html
    assert f'href="{reverse("companies:detail", kwargs={"pk": employer.pk})}"' in html
    assert "Detail Customer" in html
    assert 'class="job-detail-company-type"' in html
    assert "Customer" in html
    assert 'id="job-overview-heading">Job overview</h2>' in html
    assert 'class="job-overview-grid"' in html
    overview_start = html.index('<dl class="job-overview-grid">')
    overview = html[overview_start : html.index("</dl>", overview_start)]
    assert overview.count("<dt>") == 6
    for label in (
        "Location",
        "Workplace",
        "Employment type",
        "Seniority",
        "Job function",
        "Industry",
    ):
        assert f"<dt>{label}</dt>" in overview
    dates_start = html.index('<dl class="job-overview-dates">')
    dates = html[dates_start : html.index("</dl>", dates_start)]
    assert dates.count('class="job-overview-date"') == 3
    for label in ("Published", "First seen", "Last seen"):
        assert f"<dt>{label}</dt>" in dates
    for hidden_label in (
        "City",
        "Country",
        "Source",
        "External job ID",
        "Saved",
        "Updated",
    ):
        assert f"<dt>{hidden_label}</dt>" not in html
    assert "Vienna, Austria" in overview
    assert "Hybrid" in html
    assert "Full-time" in html
    assert "Manager" in html
    assert "Sales" in html
    assert "Software" in html
    assert "4 Aug 2026" in dates
    assert "5 Aug 2026" in dates
    assert ":00" not in dates
    assert "Build reliable systems." in html
    assert "Work with the platform team." in html
    assert f'href="{posting.source_job_url}"' in html
    assert ">View original job</a>" in html
    assert '<span class="status-badge status-active">ACTIVE</span>' in html
    assert f'href="{reverse("jobs:list")}">← Back to jobs</a>' in html


def test_job_detail_handles_null_values_and_empty_description() -> None:
    employer = company(name="Optional Detail", sequence=1)
    posting = job(
        employer,
        sequence=1,
        title=None,
        source_job_url=None,
        location=None,
        country=None,
        city=None,
        workplace_type=None,
        employment_type=None,
        seniority_level=None,
        job_function=None,
        industry=None,
        published_at=None,
        description="",
    )

    response = client().get(reverse("jobs:detail", kwargs={"pk": posting.pk}))
    html = response.content.decode()

    assert response.status_code == 200
    assert "—" in html
    assert "No description available." in html
    assert "Original job link unavailable" not in html
    assert "View original job" not in html
    assert "None" not in html


@pytest.mark.parametrize(
    ("status", "expected_class"),
    [
        ("active", "status-active"),
        ("not_found", "status-inactive"),
        ("closed", "status-inactive"),
    ],
)
def test_job_detail_uses_positive_and_neutral_status_badges(
    status: str, expected_class: str
) -> None:
    employer = company(name="Status Detail", sequence=1)
    posting = job(employer, sequence=1, status=status)

    html = client().get(
        reverse("jobs:detail", kwargs={"pk": posting.pk})
    ).content.decode()

    assert f'<span class="status-badge {expected_class}">{status.upper()}</span>' in html
    assert "status-error" not in html


def test_job_detail_renders_sanitized_paragraphs_emphasis_lists_and_breaks() -> None:
    employer = company(name="Formatted Description", sequence=1)
    raw_description = (
        "<h3>About the role</h3>"
        "<p><b>Job purpose</b><br>Build reliable systems.</p>"
        "<ul><li>Own delivery</li><li><strong>Support the team</strong></li></ul>"
    )
    posting = job(
        employer,
        sequence=1,
        description=raw_description,
    )

    html = client().get(
        reverse("jobs:detail", kwargs={"pk": posting.pk})
    ).content.decode()

    assert "<h3>About the role</h3>" in html
    assert "<p><b>Job purpose</b><br>Build reliable systems.</p>" in html
    assert "<ul><li>Own delivery</li><li><strong>Support the team</strong></li></ul>" in html
    assert "&lt;p&gt;" not in html
    assert 'class="job-description"' in html
    posting.refresh_from_db()
    assert posting.description == raw_description


def test_job_detail_removes_scripts_event_handlers_and_dangerous_attributes() -> None:
    employer = company(name="Unsafe Description", sequence=1)
    posting = job(
        employer,
        sequence=1,
        description=(
            '<script>alert("unsafe")</script>'
            '<p class="external" onclick="steal()">Safe paragraph '
            '<a href="javascript:alert(1)" target="_blank">link text</a></p>'
            '<iframe src="https://evil.example.test">hidden frame</iframe>'
            '<img src="x" onerror="steal()">'
        ),
    )

    html = client().get(
        reverse("jobs:detail", kwargs={"pk": posting.pk})
    ).content.decode()
    description = html[
        html.index('<div class="job-description">') : html.index(
            "</div>", html.index('<div class="job-description">')
        )
    ]

    assert "<p>Safe paragraph link text</p>" in description
    for unsafe_value in (
        "<script",
        "alert(",
        "onclick",
        'class="external"',
        "<a ",
        "href=",
        "javascript:",
        "target=",
        "<iframe",
        "evil.example.test",
        "hidden frame",
        "<img",
        "onerror",
    ):
        assert unsafe_value not in description


def test_job_detail_preserves_plain_text_and_line_breaks_safely() -> None:
    employer = company(name="Plain Description", sequence=1)
    posting = job(
        employer,
        sequence=1,
        description="First < second & safe\nSecond line\n\nFinal paragraph",
    )

    html = client().get(
        reverse("jobs:detail", kwargs={"pk": posting.pk})
    ).content.decode()

    assert "First &lt; second &amp; safe<br>" in html
    assert "Second line<br>\n<br>\nFinal paragraph" in html
    assert "First < second" not in html


@pytest.mark.parametrize("description", [None, ""])
def test_job_detail_keeps_empty_description_fallback(description: str | None) -> None:
    employer = company(name="Empty Description", sequence=1)
    posting = job(employer, sequence=1, description=description)

    html = client().get(
        reverse("jobs:detail", kwargs={"pk": posting.pk})
    ).content.decode()

    assert "No description available." in html
    assert '<div class="job-description">' not in html


def test_job_detail_returns_404_for_unknown_pk() -> None:
    response = client().get(reverse("jobs:detail", kwargs={"pk": 999_999}))

    assert response.status_code == 404


def test_job_detail_fetches_job_and_company_in_one_query() -> None:
    employer = company(name="Query Detail", sequence=1)
    posting = job(employer, sequence=1)
    connection = importlib.import_module("django.db").connection
    capture_queries = importlib.import_module(
        "django.test.utils"
    ).CaptureQueriesContext

    with capture_queries(connection) as queries:
        response = client().get(reverse("jobs:detail", kwargs={"pk": posting.pk}))

    assert response.status_code == 200
    assert len(queries) == 1


def test_desktop_jobs_table_gives_position_space_from_status_column() -> None:
    stylesheet = (
        Path(__file__).resolve().parents[1] / "static" / "css" / "app.css"
    ).read_text(encoding="utf-8")

    assert """.global-jobs-table thead th:first-child {
    width: 22%;
  }""" in stylesheet
    assert """.global-jobs-table thead th:nth-child(2),
  .global-jobs-table thead th:nth-child(3),
  .global-jobs-table thead th:nth-child(5),
  .global-jobs-table thead th:nth-child(6) {
    width: 14%;
  }""" in stylesheet
    assert """.global-jobs-table thead th:nth-child(4) {
    width: 11.5%;
  }""" in stylesheet
    assert """.global-jobs-table thead th:last-child {
    width: 10.5%;
  }""" in stylesheet
    assert """.global-job-title {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }""" in stylesheet
    assert """.global-jobs-table .column-header > span {
    min-width: 0;
    white-space: nowrap;
  }""" in stylesheet


def test_jobs_overview_uses_bounded_queries_without_company_n_plus_one() -> None:
    first = company(name="First", sequence=1)
    second = company(name="Second", sequence=2)
    for sequence in range(1, 11):
        job(first if sequence % 2 else second, sequence=sequence)
    connection = importlib.import_module("django.db").connection
    capture_queries = importlib.import_module(
        "django.test.utils"
    ).CaptureQueriesContext

    with capture_queries(connection) as queries:
        response = client().get(reverse("jobs:list"))

    assert response.status_code == 200
    assert len(queries) <= 4
