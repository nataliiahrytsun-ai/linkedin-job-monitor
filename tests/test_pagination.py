from pathlib import Path

import pytest

from spikes.pagination import PaginationPage, run_local_pagination

FIXTURES = Path(__file__).parent / "fixtures" / "linkedin"
PAGE_1_URL = "https://local.test/jobs?page=1"
PAGE_2_URL = "https://local.test/jobs?page=2"
PAGE_3_URL = "https://local.test/jobs?page=3"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class InMemoryPageSource:
    def __init__(self, pages: dict[str, PaginationPage]) -> None:
        self.pages = pages
        self.requested_urls: list[str] = []

    def get_page(self, url: str) -> PaginationPage:
        self.requested_urls.append(url)
        return self.pages[url]


def synthetic_pages() -> dict[str, PaginationPage]:
    return {
        PAGE_1_URL: PaginationPage(
            PAGE_1_URL, fixture("pagination_page_1_synthetic.html"), PAGE_2_URL
        ),
        PAGE_2_URL: PaginationPage(
            PAGE_2_URL, fixture("pagination_page_2_synthetic.html"), PAGE_3_URL
        ),
        PAGE_3_URL: PaginationPage(
            PAGE_3_URL, fixture("pagination_page_3_synthetic.html"), None
        ),
    }


def test_accumulates_ids_deduplicates_and_stops_without_new_ids() -> None:
    source = InMemoryPageSource(synthetic_pages())

    result = run_local_pagination(
        start_url=PAGE_1_URL, source=source, max_pages=5, max_requests=5
    )

    assert result.job_ids == frozenset({"1001", "1002", "1003"})
    assert [card.linkedin_job_id for card in result.cards] == ["1001", "1002", "1003"]
    assert result.pages_fetched == 3
    assert result.requests_made == 3
    assert result.stop_reason == "no_new_job_ids"
    assert source.requested_urls == [PAGE_1_URL, PAGE_2_URL, PAGE_3_URL]


def test_stops_before_requesting_a_repeated_url() -> None:
    pages = synthetic_pages()
    first = pages[PAGE_1_URL]
    pages[PAGE_1_URL] = PaginationPage(first.url, first.html, PAGE_1_URL)
    source = InMemoryPageSource(pages)

    result = run_local_pagination(
        start_url=PAGE_1_URL, source=source, max_pages=5, max_requests=5
    )

    assert result.stop_reason == "repeated_url"
    assert result.pages_fetched == 1
    assert result.requests_made == 1
    assert source.requested_urls == [PAGE_1_URL]


def test_stops_when_different_url_returns_identical_content() -> None:
    pages = synthetic_pages()
    first = pages[PAGE_1_URL]
    pages[PAGE_2_URL] = PaginationPage(PAGE_2_URL, first.html, PAGE_3_URL)
    source = InMemoryPageSource(pages)

    result = run_local_pagination(
        start_url=PAGE_1_URL, source=source, max_pages=5, max_requests=5
    )

    assert result.stop_reason == "repeated_content"
    assert result.pages_fetched == 2
    assert result.requests_made == 2
    assert source.requested_urls == [PAGE_1_URL, PAGE_2_URL]


def test_stops_when_current_page_has_no_next_page() -> None:
    pages = synthetic_pages()
    first = pages[PAGE_1_URL]
    pages[PAGE_1_URL] = PaginationPage(first.url, first.html, None)
    source = InMemoryPageSource(pages)

    result = run_local_pagination(
        start_url=PAGE_1_URL, source=source, max_pages=5, max_requests=5
    )

    assert result.stop_reason == "no_next_page"
    assert result.pages_fetched == 1
    assert result.requests_made == 1
    assert source.requested_urls == [PAGE_1_URL]


def test_stops_at_max_pages() -> None:
    source = InMemoryPageSource(synthetic_pages())

    result = run_local_pagination(
        start_url=PAGE_1_URL, source=source, max_pages=1, max_requests=5
    )

    assert result.stop_reason == "max_pages"
    assert result.pages_fetched == 1
    assert result.requests_made == 1
    assert source.requested_urls == [PAGE_1_URL]


def test_stops_at_max_requests() -> None:
    source = InMemoryPageSource(synthetic_pages())

    result = run_local_pagination(
        start_url=PAGE_1_URL, source=source, max_pages=5, max_requests=1
    )

    assert result.stop_reason == "max_requests"
    assert result.pages_fetched == 1
    assert result.requests_made == 1
    assert source.requested_urls == [PAGE_1_URL]


@pytest.mark.parametrize(("max_pages", "max_requests"), [(0, 1), (1, 0)])
def test_rejects_non_positive_limits(max_pages: int, max_requests: int) -> None:
    source = InMemoryPageSource(synthetic_pages())

    with pytest.raises(ValueError):
        run_local_pagination(
            start_url=PAGE_1_URL,
            source=source,
            max_pages=max_pages,
            max_requests=max_requests,
        )
