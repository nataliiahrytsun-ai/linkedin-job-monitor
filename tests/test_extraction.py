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
