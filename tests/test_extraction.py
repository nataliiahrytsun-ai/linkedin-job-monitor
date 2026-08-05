from pathlib import Path

from spikes.extraction import (
    JobCard,
    extract_job_cards,
    extract_job_detail,
    extract_linkedin_job_id,
    should_stop_pagination,
    unique_cards,
)

FIXTURES = Path(__file__).parent / "fixtures" / "linkedin"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_extracts_job_card_and_id() -> None:
    cards = extract_job_cards(fixture("job_cards_synthetic.html"))

    assert len(cards) == 2
    assert cards[0] == JobCard(
        linkedin_job_id="4434981246",
        title="Data Platform Engineer",
        company="Example Analytics",
        location="Vienna, Austria",
        published_at="2026-07-20",
        job_url=(
            "https://www.linkedin.com/jobs/view/"
            "data-platform-engineer-4434981246?trackingId=fixture"
        ),
    )


def test_missing_card_fields_are_none() -> None:
    card = extract_job_cards(fixture("job_cards_synthetic.html"))[1]

    assert card.company is None
    assert card.location is None
    assert card.published_at is None


def test_extracts_rendered_standalone_job_link() -> None:
    cards = extract_job_cards(
        fixture("job_card_rendered_link_synthetic.html"),
        base_url="https://in.linkedin.com/jobs/",
    )

    assert len(cards) == 1
    assert cards[0].linkedin_job_id == "4447661197"
    assert cards[0].title == "Delivery Manager"
    assert cards[0].job_url == (
        "https://in.linkedin.com/jobs/view/"
        "delivery-manager-at-acuity-analytics-4447661197"
    )


def test_container_and_link_fallback_return_one_card_per_job_id() -> None:
    html = """
    <li class="jobs-search-results__list-item" data-job-id="4447661197">
      <a href="/jobs/view/delivery-manager-4447661197">
        <h3 class="base-search-card__title">Delivery Manager</h3>
      </a>
    </li>
    """

    cards = extract_job_cards(html)

    assert len(cards) == 1
    assert cards[0].linkedin_job_id == "4447661197"


def test_standalone_job_link_rejects_non_linkedin_domain() -> None:
    html = '<a href="https://example.com/jobs/view/example-4447661197">Example</a>'

    assert extract_job_cards(html) == []


def test_standalone_job_link_rejects_missing_numeric_job_id() -> None:
    html = '<a href="https://www.linkedin.com/jobs/view/delivery-manager">Delivery Manager</a>'

    assert extract_job_cards(html) == []


def test_standalone_relative_job_link_resolves_against_linkedin_base_url() -> None:
    html = '<a href="/jobs/view/delivery-manager-4447661197">Delivery Manager</a>'

    cards = extract_job_cards(html, base_url="https://in.linkedin.com/jobs/search")

    assert len(cards) == 1
    assert cards[0].job_url == "https://in.linkedin.com/jobs/view/delivery-manager-4447661197"


def test_standalone_job_link_allows_missing_title() -> None:
    html = '<a href="https://www.linkedin.com/jobs/view/4447661197"></a>'

    cards = extract_job_cards(html)

    assert len(cards) == 1
    assert cards[0].title is None


def test_standalone_job_link_title_falls_back_to_aria_label_then_visible_text() -> None:
    aria_html = (
        '<a href="https://www.linkedin.com/jobs/view/4447661197" '
        'aria-label="Delivery Manager"></a>'
    )
    text_html = '<a href="https://www.linkedin.com/jobs/view/4447661198">QA Manager</a>'

    assert extract_job_cards(aria_html)[0].title == "Delivery Manager"
    assert extract_job_cards(text_html)[0].title == "QA Manager"


def test_extracts_id_from_supported_public_shapes() -> None:
    assert extract_linkedin_job_id("urn:li:jobPosting:123456") == "123456"
    assert extract_linkedin_job_id("https://www.linkedin.com/jobs/view/name-234567") == "234567"
    assert extract_linkedin_job_id("https://example.test/?currentJobId=345678") == "345678"
    assert extract_linkedin_job_id(None, "not-an-id") is None


def test_duplicate_detection_prefers_job_id_and_has_url_fallback() -> None:
    first, second = extract_job_cards(fixture("job_cards_synthetic.html"))
    assert second.job_url is not None
    same_id = JobCard("4434981246", "Changed title", None, None, None, "/different")
    url_only = JobCard(None, "Fallback", None, None, None, second.job_url + "?ref=duplicate")

    assert unique_cards([first, same_id, second, url_only]) == [first, second]


def test_pagination_stops_on_bounds_or_no_new_ids() -> None:
    assert should_stop_pagination(
        page_number=1, request_count=1, new_job_ids=set(), max_pages=2, max_requests=3
    )
    assert should_stop_pagination(
        page_number=2,
        request_count=2,
        new_job_ids={"1"},
        max_pages=2,
        max_requests=3,
    )
    assert not should_stop_pagination(
        page_number=1,
        request_count=1,
        new_job_ids={"1"},
        max_pages=2,
        max_requests=3,
    )


def test_extracts_detail_fields() -> None:
    detail = extract_job_detail(fixture("job_detail_synthetic.html"))

    assert detail.description == "Build and operate a reliable analytics platform."
    assert detail.seniority_level == "Mid-Senior level"
    assert detail.employment_type == "Full-time"
    assert detail.job_function == "Engineering"
    assert detail.industries == "Software Development"


def test_missing_detail_fields_are_none() -> None:
    detail = extract_job_detail("<html><body></body></html>")

    assert detail.description is None
    assert detail.seniority_level is None
