from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import pytest

from scraping.sources.base import SourceError
from scraping.sources.darwinbox import (
    DarwinboxMethod,
    DarwinboxSourceAdapter,
    darwinbox_source_from_url,
)
from scraping.sources.darwinbox_browser import (
    DarwinboxBrowserLocation,
    DarwinboxBrowserTransport,
    DarwinboxBrowserTransportError,
)

FIXTURES = Path(__file__).parent / "fixtures" / "darwinbox"


@dataclass
class CompanyStub:
    source: str = "darwinbox"
    source_jobs_url: str | None = "https://tenant.darwinbox.com/ms/candidate/careers"


class FakeTransport:
    def __init__(self, *responses: str | bytes | Exception) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[DarwinboxMethod, str, dict[str, object] | None, float]] = []

    def __call__(
        self,
        method: DarwinboxMethod,
        url: str,
        json_body: object,
        timeout_seconds: float,
    ) -> str | bytes:
        body = cast(dict[str, object] | None, json_body)
        self.calls.append((method, url, body, timeout_seconds))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class FakeBrowserTransport(DarwinboxBrowserTransport):
    def __init__(
        self,
        *listing_pages: bytes,
        detail_response: bytes | None = None,
    ) -> None:
        self.pages = listing_pages
        self.detail_response = detail_response
        self.listing_calls: list[tuple[str, int, float]] = []
        self.detail_calls: list[tuple[str, str, float]] = []

    def listing_pages(
        self,
        location: DarwinboxBrowserLocation,
        *,
        max_pages: int,
        timeout_seconds: float,
    ) -> tuple[bytes, ...]:
        self.listing_calls.append((location.host, max_pages, timeout_seconds))
        return self.pages

    def detail(
        self,
        location: DarwinboxBrowserLocation,
        source_job_id: str,
        *,
        timeout_seconds: float,
    ) -> bytes:
        self.detail_calls.append((location.host, source_job_id, timeout_seconds))
        if self.detail_response is None:
            raise AssertionError("unexpected detail request")
        return self.detail_response


class FakeBrowserResponse:
    def __init__(
        self,
        body: bytes,
        *,
        url: str | None = None,
        status: int = 200,
    ) -> None:
        self.url = url or (
            "https://tenant.darwinbox.com/ms/candidateapi/job/alljobs?companyId=main"
        )
        self.status = status
        self._body = body

    def body(self) -> bytes:
        return self._body


class FakeResponseInfo:
    def __init__(self, response: FakeBrowserResponse) -> None:
        self.value = response

    def __enter__(self) -> FakeResponseInfo:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeLocator:
    def __init__(self, page: FakeBrowserPage) -> None:
        self.page = page

    @property
    def first(self) -> FakeLocator:
        return self

    def count(self) -> int:
        return int(bool(self.page.remaining_responses))

    def is_visible(self) -> bool:
        return bool(self.page.remaining_responses)

    def click(self) -> None:
        self.page.clicks += 1

    def inner_text(self) -> str:
        return "86 Open jobs available"


class FakeBrowserPage:
    def __init__(
        self,
        *responses: FakeBrowserResponse,
        title_text: str = "Acuity Analytics",
    ) -> None:
        self.initial_response = responses[0]
        self.remaining_responses = list(responses[1:])
        self.response_listener: object | None = None
        self.clicks = 0
        self.title_text = title_text

    def on(self, _event: str, listener: object) -> None:
        self.response_listener = listener

    def emit_initial_response(self) -> None:
        listener = cast(Any, self.response_listener)
        listener(self.initial_response)

    def title(self) -> str:
        return self.title_text

    def locator(self, _selector: str) -> FakeLocator:
        return FakeLocator(self)

    def wait_for_load_state(self, _state: str, *, timeout: int) -> None:
        assert timeout == 20_000

    def get_by_role(self, _role: str, *, name: object) -> FakeLocator:
        return FakeLocator(self)

    def get_by_text(self, _text: object, *, exact: bool) -> FakeLocator:
        return FakeLocator(self)

    def expect_response(self, predicate: object, *, timeout: int) -> FakeResponseInfo:
        response = self.remaining_responses.pop(0)
        assert cast(Any, predicate)(response)
        assert timeout == 20_000
        return FakeResponseInfo(response)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def listing(*jobs: dict[str, object], total: int = 0) -> str:
    return json.dumps({"status": "success", "data": jobs, "job_counts": total})


def job(job_id: str, *, jd: str = "Description", title: str = "Analyst") -> dict[str, object]:
    return {"id": job_id, "title": title, "jd": jd}


def test_legacy_url_uses_observed_main_company_identifier() -> None:
    result = darwinbox_source_from_url(
        "  https://acuitykp.darwinbox.com/ms/candidate/careers  "
    )

    assert result.scheme == "https"
    assert result.host == "acuitykp.darwinbox.com"
    assert result.company_id == "main"


def test_candidate_v2_url_uses_path_company_identifier() -> None:
    result = darwinbox_source_from_url(
        "http://tenant.darwinbox.com/ms/candidatev2/acme/careers/jobDetails/123"
    )

    assert result.scheme == "http"
    assert result.company_id == "acme"


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "https://example.com/ms/candidate/careers",
        "https://darwinbox.com/ms/candidate/careers",
        "https://tenant.darwinbox.com/not-careers",
        "https://user@tenant.darwinbox.com/ms/candidate/careers",
        "https://tenant.darwinbox.com:443/ms/candidate/careers",
        "https://tenant.darwinbox.com/ms/candidatev2/%20/careers",
    ],
)
def test_url_parser_rejects_unsupported_or_unsafe_urls(url: str | None) -> None:
    with pytest.raises(SourceError):
        darwinbox_source_from_url(url)


def test_one_fetch_walks_two_pages_and_maps_records() -> None:
    transport = FakeTransport(
        fixture("page_1.json"), fixture("page_2_terminal.json")
    )

    batch = DarwinboxSourceAdapter(request=transport).fetch(company=CompanyStub())

    assert [record["source_job_id"] for record in batch.records] == [
        "darwinbox-1",
        "darwinbox-2",
        "darwinbox-3",
    ]
    assert batch.requests_made == 2
    assert [call[0] for call in transport.calls] == ["POST", "POST"]
    assert [call[2] for call in transport.calls] == [
        {"companyId": "main", "page": 1, "sort_option": "new", "limit": 10},
        {"companyId": "main", "page": 2, "sort_option": "new", "limit": 10},
    ]
    first = batch.records[0]
    assert first["source"] == "darwinbox"
    assert first["title"] == "Data Analyst"
    assert first["location"] == "Bengaluru"
    assert first["country"] == "India"
    assert first["job_function"] == "Analytics"
    assert first["employment_type"] == "Full time"
    assert first["published_at"] == "2026-08-01T09:00:00+00:00"
    assert first["description"] == "<p>Build trusted reports.</p>"
    assert first["source_job_url"] == (
        "https://tenant.darwinbox.com/ms/candidatev2/main/careers/"
        "jobDetails/darwinbox-1?from=all"
    )
    assert batch.records[1]["location"] == "Gurugram, Remote"


def test_default_flow_accepts_browser_captured_pages_without_direct_api() -> None:
    browser = FakeBrowserTransport(
        fixture("page_1.json").encode(),
        fixture("page_2_terminal.json").encode(),
    )

    batch = DarwinboxSourceAdapter(browser_transport=browser).fetch(
        company=CompanyStub()
    )

    assert [record["source_job_id"] for record in batch.records] == [
        "darwinbox-1",
        "darwinbox-2",
        "darwinbox-3",
    ]
    assert batch.requests_made == 2
    assert browser.listing_calls == [("tenant.darwinbox.com", 100, 20.0)]


def test_browser_detail_fallback_counts_one_additional_data_operation() -> None:
    browser = FakeBrowserTransport(
        listing(job("darwinbox-detail", jd=""), total=1).encode(),
        detail_response=fixture("detail_success.json").encode(),
    )

    batch = DarwinboxSourceAdapter(browser_transport=browser).fetch(
        company=CompanyStub()
    )

    assert batch.requests_made == 2
    assert browser.detail_calls == [
        ("tenant.darwinbox.com", "darwinbox-detail", 20.0)
    ]
    assert batch.records[0]["description"] == "<p>Detailed description.</p>"


def test_browser_detail_opens_public_ui_and_captures_matching_data_response() -> None:
    page = FakeBrowserPage(
        FakeBrowserResponse(
            fixture("detail_success.json").encode(),
            url=(
                "https://tenant.darwinbox.com/ms/candidateapi/job/"
                "darwinbox-detail?companyId=main"
            ),
        )
    )
    navigations: list[str] = []

    def browser_fetch(url: str, **kwargs: Any) -> object:
        navigations.append(url)
        cast(Any, kwargs["page_setup"])(page)
        page.emit_initial_response()
        cast(Any, kwargs["page_action"])(page)
        return object()

    location = darwinbox_source_from_url(
        "https://tenant.darwinbox.com/ms/candidate/careers"
    )
    body = DarwinboxBrowserTransport(browser_fetch=browser_fetch).detail(
        location,
        "darwinbox-detail",
        timeout_seconds=20,
    )

    assert json.loads(body)["status"] == "success"
    assert navigations == [
        "https://tenant.darwinbox.com/ms/candidatev2/main/careers/"
        "jobDetails/darwinbox-detail?from=all"
    ]


def test_plain_headful_transport_uses_ui_load_more_and_safe_browser_options() -> None:
    page = FakeBrowserPage(
        FakeBrowserResponse(listing(job("A"), job("B"), total=3).encode()),
        FakeBrowserResponse(listing(job("C"), total=3).encode()),
    )
    calls: list[tuple[str, dict[str, object]]] = []

    def browser_fetch(url: str, **kwargs: Any) -> object:
        calls.append((url, kwargs))
        cast(Any, kwargs["page_setup"])(page)
        page.emit_initial_response()
        cast(Any, kwargs["page_action"])(page)
        return object()

    location = darwinbox_source_from_url(
        "https://tenant.darwinbox.com/ms/candidate/careers"
    )
    pages = DarwinboxBrowserTransport(browser_fetch=browser_fetch).listing_pages(
        location,
        max_pages=3,
        timeout_seconds=20,
    )

    assert len(pages) == 2
    assert page.clicks == 1
    url, options = calls[0]
    assert url.endswith("/ms/candidatev2/main/careers/allJobs")
    assert options["headless"] is False
    assert options["real_chrome"] is True
    assert options["google_search"] is False
    assert options["disable_resources"] is False
    assert options["retries"] == 1
    assert options["user_data_dir"] == ""
    assert "cookies" not in options
    assert "proxy" not in options
    assert "useragent" not in options
    assert "extra_headers" not in options
    assert "cdp_url" not in options


def test_headful_ui_collects_initial_ten_and_all_86_jobs() -> None:
    pages = [
        listing(
            *(job(f"job-{index}") for index in range(start, min(start + 10, 86))),
            total=86,
        ).encode()
        for start in range(0, 86, 10)
    ]
    page = FakeBrowserPage(*(FakeBrowserResponse(body) for body in pages))

    def browser_fetch(_url: str, **kwargs: Any) -> object:
        cast(Any, kwargs["page_setup"])(page)
        page.emit_initial_response()
        cast(Any, kwargs["page_action"])(page)
        return object()

    batch = DarwinboxSourceAdapter(
        browser_transport=DarwinboxBrowserTransport(browser_fetch=browser_fetch),
        max_pages=9,
    ).fetch(company=CompanyStub())

    assert len(batch.records) == 86
    assert batch.requests_made == 9
    assert page.clicks == 8
    assert batch.records[0]["source_job_id"] == "job-0"
    assert batch.records[-1]["source_job_id"] == "job-85"


def test_browser_transport_fails_closed_when_spa_emits_no_listing() -> None:
    calls = 0

    def browser_fetch(_url: str, **kwargs: Any) -> object:
        nonlocal calls
        calls += 1
        page = FakeBrowserPage(FakeBrowserResponse(b"{}"))
        cast(Any, kwargs["page_setup"])(page)
        cast(Any, kwargs["page_action"])(page)
        return object()

    location = darwinbox_source_from_url(
        "https://tenant.darwinbox.com/ms/candidate/careers"
    )

    with pytest.raises(
        DarwinboxBrowserTransportError, match="did not emit an initial listing"
    ):
        DarwinboxBrowserTransport(browser_fetch=browser_fetch).listing_pages(
            location,
            max_pages=1,
            timeout_seconds=20,
        )

    assert calls == 1


def test_browser_transport_stops_on_access_control_signal() -> None:
    page = FakeBrowserPage(
        FakeBrowserResponse(b"{}"), title_text="Attention Required | Cloudflare"
    )

    def browser_fetch(_url: str, **kwargs: Any) -> object:
        cast(Any, kwargs["page_setup"])(page)
        cast(Any, kwargs["page_action"])(page)
        return object()

    location = darwinbox_source_from_url(
        "https://tenant.darwinbox.com/ms/candidate/careers"
    )

    with pytest.raises(
        DarwinboxBrowserTransportError, match="access-control challenge"
    ) as caught:
        DarwinboxBrowserTransport(browser_fetch=browser_fetch).listing_pages(
            location,
            max_pages=1,
            timeout_seconds=20,
        )

    assert caught.value.requests_made == 0


def test_browser_transport_rejects_non_success_listing_response() -> None:
    page = FakeBrowserPage(FakeBrowserResponse(b"forbidden", status=403))

    def browser_fetch(_url: str, **kwargs: Any) -> object:
        cast(Any, kwargs["page_setup"])(page)
        page.emit_initial_response()
        cast(Any, kwargs["page_action"])(page)
        return object()

    location = darwinbox_source_from_url(
        "https://tenant.darwinbox.com/ms/candidate/careers"
    )

    with pytest.raises(DarwinboxBrowserTransportError, match="HTTP 403") as caught:
        DarwinboxBrowserTransport(browser_fetch=browser_fetch).listing_pages(
            location,
            max_pages=1,
            timeout_seconds=20,
        )

    assert caught.value.requests_made == 1


def test_browser_transport_fails_when_load_more_is_missing_before_total() -> None:
    page = FakeBrowserPage(
        FakeBrowserResponse(listing(job("A"), total=2).encode())
    )

    def browser_fetch(_url: str, **kwargs: Any) -> object:
        cast(Any, kwargs["page_setup"])(page)
        page.emit_initial_response()
        cast(Any, kwargs["page_action"])(page)
        return object()

    location = darwinbox_source_from_url(
        "https://tenant.darwinbox.com/ms/candidate/careers"
    )

    with pytest.raises(DarwinboxBrowserTransportError, match="ended before"):
        DarwinboxBrowserTransport(browser_fetch=browser_fetch).listing_pages(
            location,
            max_pages=2,
            timeout_seconds=20,
        )

    assert page.clicks == 0


def test_browser_transport_stops_after_duplicate_only_load_more_response() -> None:
    page = FakeBrowserPage(
        FakeBrowserResponse(listing(job("A"), total=2).encode()),
        FakeBrowserResponse(listing(job("A"), total=2).encode()),
        FakeBrowserResponse(listing(job("B"), total=2).encode()),
    )

    def browser_fetch(_url: str, **kwargs: Any) -> object:
        cast(Any, kwargs["page_setup"])(page)
        page.emit_initial_response()
        cast(Any, kwargs["page_action"])(page)
        return object()

    location = darwinbox_source_from_url(
        "https://tenant.darwinbox.com/ms/candidate/careers"
    )

    with pytest.raises(DarwinboxBrowserTransportError, match="repeated a page"):
        DarwinboxBrowserTransport(browser_fetch=browser_fetch).listing_pages(
            location,
            max_pages=4,
            timeout_seconds=20,
        )

    assert page.clicks == 1


def test_exact_code_pagination_walks_page_one_then_page_two_once() -> None:
    transport = FakeTransport(
        listing(job("A"), job("B"), total=4),
        listing(job("C"), job("D"), total=4),
    )

    batch = DarwinboxSourceAdapter(request=transport).fetch(company=CompanyStub())

    assert [record["source_job_id"] for record in batch.records] == ["A", "B", "C", "D"]
    assert [call[2] for call in transport.calls] == [
        {"companyId": "main", "page": 1, "sort_option": "new", "limit": 10},
        {"companyId": "main", "page": 2, "sort_option": "new", "limit": 10},
    ]


def test_one_page_and_three_page_complete_snapshots() -> None:
    one_page = DarwinboxSourceAdapter(
        request=FakeTransport(listing(job("A"), total=1))
    ).fetch(company=CompanyStub())
    three_page_transport = FakeTransport(
        listing(job("A"), total=3),
        listing(job("B"), total=3),
        listing(job("C"), total=3),
    )
    three_pages = DarwinboxSourceAdapter(request=three_page_transport).fetch(
        company=CompanyStub()
    )

    assert len(one_page.records) == 1
    assert len(three_pages.records) == 3
    assert three_pages.requests_made == 3


def test_valid_empty_snapshot_is_complete() -> None:
    transport = FakeTransport(fixture("empty_complete.json"))

    batch = DarwinboxSourceAdapter(request=transport).fetch(company=CompanyStub())

    assert batch.records == ()
    assert batch.requests_made == 1


def test_duplicate_across_pages_is_deduplicated_when_page_has_new_id() -> None:
    transport = FakeTransport(
        listing(job("A"), job("B"), total=3),
        listing(job("B"), job("C"), total=3),
    )

    batch = DarwinboxSourceAdapter(request=transport).fetch(company=CompanyStub())

    assert [record["source_job_id"] for record in batch.records] == ["A", "B", "C"]


def test_duplicate_inside_one_page_is_deduplicated() -> None:
    transport = FakeTransport(listing(job("A"), job("A"), total=1))

    batch = DarwinboxSourceAdapter(request=transport).fetch(company=CompanyStub())

    assert [record["source_job_id"] for record in batch.records] == ["A"]


@pytest.mark.parametrize(
    ("responses", "message", "expected_requests"),
    [
        ((listing(job("A"), total=2), listing(job("A"), total=2)), "repeated", 2),
        ((listing(job("A"), total=2), listing(total=2)), "empty page", 2),
        ((fixture("malformed_listing.json"),), "job_counts", 1),
        ((json.dumps({"status": "error", "data": [], "job_counts": 0}),), "status", 1),
        (("<html><title>Login</title></html>",), "valid JSON", 1),
        ((listing(job("A"), total=2), listing(job("B"), total=3)), "changed", 2),
        ((listing(job("A"), job("B"), total=1),), "more unique", 1),
    ],
)
def test_incomplete_or_malformed_listing_fails_closed(
    responses: tuple[str, ...], message: str, expected_requests: int
) -> None:
    transport = FakeTransport(*responses)

    with pytest.raises(SourceError, match=message) as caught:
        DarwinboxSourceAdapter(request=transport).fetch(company=CompanyStub())

    assert caught.value.requests_made == expected_requests


def test_page_limit_before_total_fails_closed() -> None:
    transport = FakeTransport(listing(job("A"), total=100))

    with pytest.raises(SourceError, match="page limit") as caught:
        DarwinboxSourceAdapter(request=transport, max_pages=1).fetch(
            company=CompanyStub()
        )

    assert caught.value.requests_made == 1


def test_transport_failure_preserves_request_count() -> None:
    with pytest.raises(SourceError, match="request failed") as caught:
        DarwinboxSourceAdapter(request=FakeTransport(RuntimeError("offline"))).fetch(
            company=CompanyStub()
        )

    assert caught.value.requests_made == 1


def test_detail_is_not_requested_when_listing_description_is_present() -> None:
    transport = FakeTransport(listing(job("A", jd="Present"), total=1))

    batch = DarwinboxSourceAdapter(request=transport).fetch(company=CompanyStub())

    assert batch.requests_made == 1
    assert [call[0] for call in transport.calls] == ["POST"]


def test_empty_listing_description_fetches_only_matching_detail() -> None:
    detail = fixture("detail_success.json")
    listing_job = job("darwinbox-detail", jd="", title="Detail Analyst")
    transport = FakeTransport(listing(listing_job, total=1), detail)

    batch = DarwinboxSourceAdapter(request=transport).fetch(company=CompanyStub())

    assert batch.requests_made == 2
    assert [call[0] for call in transport.calls] == ["POST", "GET"]
    assert transport.calls[1][2] is None
    assert urlsplit(transport.calls[1][1]).path.endswith("/job/darwinbox-detail")
    assert parse_qs(urlsplit(transport.calls[1][1]).query) == {"companyId": ["main"]}
    assert batch.records[0]["description"] == "<p>Detailed description.</p>"
    assert batch.records[0]["job_function"] == "Research"


@pytest.mark.parametrize(
    ("fixture_name", "message"),
    [
        ("detail_malformed.json", "non-empty array"),
        ("detail_id_mismatch.json", "does not match"),
    ],
)
def test_malformed_or_mismatched_detail_fails_closed(
    fixture_name: str, message: str
) -> None:
    transport = FakeTransport(
        listing(job("darwinbox-detail", jd=""), total=1), fixture(fixture_name)
    )

    with pytest.raises(SourceError, match=message) as caught:
        DarwinboxSourceAdapter(request=transport).fetch(company=CompanyStub())

    assert caught.value.requests_made == 2


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"page_size": 0}, "page_size"),
        ({"page_size": True}, "page_size"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"timeout_seconds": True}, "timeout_seconds"),
        ({"max_pages": 0}, "max_pages"),
    ],
)
def test_invalid_adapter_configuration_is_rejected(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        DarwinboxSourceAdapter(**kwargs)  # type: ignore[arg-type]
