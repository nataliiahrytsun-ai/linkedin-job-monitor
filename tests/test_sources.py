from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path
from typing import Literal

import pytest

from scraping.sources.base import SourceBatch, SourceError
from scraping.sources.darwinbox import DarwinboxSourceAdapter
from scraping.sources.dreamjobs import DreamJobsSourceAdapter
from scraping.sources.fixture import FixtureSourceAdapter
from scraping.sources.generic import (
    FakeJobExtractionProvider,
    GenericSourceAdapter,
    candidate_id_for_url,
)
from scraping.sources.jazzhr import JazzHRSourceAdapter
from scraping.sources.lever import LeverSourceAdapter
from scraping.sources.registry import (
    UnknownSourceError,
    executable_source_keys,
    get_source_adapter,
    registered_source_keys,
    source_unavailability_message,
    user_deletable_source_keys,
    user_selectable_source_keys,
    user_visible_source_keys,
)
from scraping.sources.zoho_recruit import ZohoRecruitSourceAdapter

GENERIC_FIXTURES = Path(__file__).parent / "fixtures" / "generic"


@dataclass
class CompanyStub:
    source: str
    source_jobs_url: str | None = None


class GenericPageResponse:
    status = 200

    def __init__(self, url: str, body: str) -> None:
        self.url = url
        self.body = body.encode()


class GenericPageSession:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def __enter__(self) -> GenericPageSession:
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> Literal[False]:
        return False

    def get(self, url: str) -> GenericPageResponse:
        self.calls.append(url)
        return GenericPageResponse(url, self.pages[url])


def test_source_batch_is_immutable_and_validates_request_count() -> None:
    batch = SourceBatch(records=({"source": "fixture"},), requests_made=0)

    assert batch.records == ({"source": "fixture"},)
    assert batch.requests_made == 0
    with pytest.raises(FrozenInstanceError):
        batch.requests_made = 1  # type: ignore[misc]
    with pytest.raises(ValueError, match="non-negative int"):
        SourceBatch(records=(), requests_made=-1)
    with pytest.raises(ValueError, match="excluding bool"):
        SourceBatch(records=(), requests_made=True)


def test_registry_normalizes_source_and_selects_fixture_adapter() -> None:
    adapter = get_source_adapter(CompanyStub(source="  FiXtUrE  "))

    assert isinstance(adapter, FixtureSourceAdapter)


def test_registry_normalizes_source_and_selects_lever_adapter() -> None:
    adapter = get_source_adapter(
        CompanyStub(
            source="  LeVeR  ",
            source_jobs_url="https://jobs.lever.co/olo",
        )
    )

    assert isinstance(adapter, LeverSourceAdapter)


def test_registry_keeps_darwinbox_registered_visible_selectable_and_executable() -> None:
    adapter = get_source_adapter(
        CompanyStub(
            source="  DaRwInBoX  ",
            source_jobs_url="https://tenant.darwinbox.com/ms/candidate/careers",
        )
    )

    assert isinstance(adapter, DarwinboxSourceAdapter)
    assert "darwinbox" in registered_source_keys()
    assert "darwinbox" in user_visible_source_keys()
    assert "darwinbox" in user_selectable_source_keys()
    assert "darwinbox" in executable_source_keys()
    assert source_unavailability_message(" DaRwInBoX ") is None


def test_registry_keeps_jazzhr_registered_visible_selectable_and_executable() -> None:
    adapter = get_source_adapter(
        CompanyStub(
            source="  JaZzHr  ",
            source_jobs_url="https://example.applytojob.com/apply",
        )
    )

    assert isinstance(adapter, JazzHRSourceAdapter)
    assert "jazzhr" in registered_source_keys()
    assert "jazzhr" in user_visible_source_keys()
    assert "jazzhr" in user_selectable_source_keys()
    assert "jazzhr" in executable_source_keys()
    assert source_unavailability_message(" JaZzHr ") is None


def test_registry_keeps_dreamjobs_registered_visible_selectable_and_executable() -> None:
    adapter = get_source_adapter(
        CompanyStub(
            source="  DrEaMjObS  ",
            source_jobs_url="https://careers.datasentics.com/jobs",
        )
    )

    assert isinstance(adapter, DreamJobsSourceAdapter)
    assert "dreamjobs" in registered_source_keys()
    assert "dreamjobs" in user_visible_source_keys()
    assert "dreamjobs" in user_selectable_source_keys()
    assert "dreamjobs" in executable_source_keys()
    assert source_unavailability_message(" DrEaMjObS ") is None


def test_registry_distinguishes_registered_and_user_selectable_sources() -> None:
    registered_keys = registered_source_keys()
    visible_keys = user_visible_source_keys()
    selectable_keys = user_selectable_source_keys()
    executable_keys = executable_source_keys()

    assert isinstance(registered_keys, tuple)
    assert isinstance(visible_keys, tuple)
    assert isinstance(selectable_keys, tuple)
    assert isinstance(executable_keys, tuple)
    assert "fixture" in registered_keys
    assert "fixture" in executable_keys
    assert "fixture" not in visible_keys
    assert "fixture" not in selectable_keys
    assert "darwinbox" in registered_keys
    assert "darwinbox" in visible_keys
    assert "darwinbox" in selectable_keys
    assert "darwinbox" in executable_keys
    assert "lever" in registered_keys
    assert "lever" in visible_keys
    assert "lever" in selectable_keys
    assert "lever" in executable_keys
    assert "jazzhr" in registered_keys
    assert "jazzhr" in visible_keys
    assert "jazzhr" in selectable_keys
    assert "jazzhr" in executable_keys
    assert "dreamjobs" in registered_keys
    assert "dreamjobs" in visible_keys
    assert "dreamjobs" in selectable_keys
    assert "dreamjobs" in executable_keys
    assert set(selectable_keys) <= set(registered_keys)
    assert set(executable_keys) <= set(registered_keys)


def test_linkedin_is_visible_but_not_registered_selectable_or_executable() -> None:
    assert "linkedin" in user_visible_source_keys()
    assert "linkedin" not in registered_source_keys()
    assert "linkedin" not in user_selectable_source_keys()
    assert "linkedin" not in executable_source_keys()
    assert source_unavailability_message(" LiNkEdIn ") == (
        "Technical adapter ready · Production disabled · "
        "Requires approved LinkedIn access"
    )

    with pytest.raises(UnknownSourceError, match="linkedin"):
        get_source_adapter(
            CompanyStub(
                source="linkedin",
                source_jobs_url="https://www.linkedin.com/jobs/example-jobs",
            )
        )


def test_registry_keeps_generic_hidden_but_registered_for_explicit_source() -> None:
    adapter = get_source_adapter(
        CompanyStub(
            source=" Generic ",
            source_jobs_url="https://example.com/careers",
        )
    )

    assert isinstance(adapter, GenericSourceAdapter)
    assert "generic" in registered_source_keys()
    assert "generic" in executable_source_keys()
    assert "generic" not in user_visible_source_keys()
    assert "generic" not in user_selectable_source_keys()
    assert "generic" in user_deletable_source_keys()
    assert "fixture" not in user_deletable_source_keys()
    assert "zoho_recruit" in user_deletable_source_keys()


def test_registry_keeps_zoho_recruit_registered_selectable_and_executable() -> None:
    adapter = get_source_adapter(
        CompanyStub(
            source=" Zoho_Recruit ",
            source_jobs_url="https://jobs.example.com/jobs/Careers",
        )
    )

    assert isinstance(adapter, ZohoRecruitSourceAdapter)
    assert "zoho_recruit" in registered_source_keys()
    assert "zoho_recruit" in user_visible_source_keys()
    assert "zoho_recruit" in user_selectable_source_keys()
    assert "zoho_recruit" in executable_source_keys()


def test_generic_source_adapter_returns_valid_batch_from_minimal_fixture() -> None:
    job_url = "https://example.com/jobs/123"
    candidate_id = candidate_id_for_url(job_url)

    class DummyResponse:
        status = 200
        body = (
            b"<html><body><a href='https://example.com/jobs/123'>"
            b"Senior Analyst</a></body></html>"
        )

    class DummySession:
        def __enter__(self) -> DummySession:
            return self

        def __exit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> Literal[False]:
            return False

        def get(self, url: str) -> DummyResponse:
            assert url == "https://example.com/careers"
            return DummyResponse()

    adapter = GenericSourceAdapter(
        provider=FakeJobExtractionProvider(mapping={candidate_id: "Senior Analyst"}),
        session_factory=lambda timeout_seconds: DummySession(),
    )
    batch = adapter.fetch(
        company=CompanyStub(source="generic", source_jobs_url="https://example.com/careers")
    )

    assert batch.requests_made == 1
    assert batch.records == (
        {
            "source": "generic",
            "source_job_id": candidate_id,
            "title": "Senior Analyst",
            "source_job_url": job_url,
        },
    )


def test_generic_source_adapter_uses_deterministic_jobs_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GENERIC_AI_OPENAI_API_KEY", raising=False)
    body = (GENERIC_FIXTURES / "abylon_careers.html").read_bytes()

    class DummyResponse:
        status = 200

        def __init__(self) -> None:
            self.body = body

    class DummySession:
        def __enter__(self) -> DummySession:
            return self

        def __exit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> Literal[False]:
            return False

        def get(self, url: str) -> DummyResponse:
            assert url == "https://abylon.io/career/"
            return DummyResponse()

    adapter = GenericSourceAdapter(
        session_factory=lambda timeout_seconds: DummySession(),
    )
    batch = adapter.fetch(
        company=CompanyStub(source="generic", source_jobs_url="https://abylon.io/career/")
    )

    expected_titles = {
        "https://abylon.io/career/ai-solutions-architect-enterprise-ai/": (
            "AI Solutions Architect"
        ),
        "https://abylon.io/career/data-platform-engineer-azure-databricks-devops/": (
            "Data Platform Engineer"
        ),
        "https://abylon.io/career/junior-business-analyst-london-abylon/": (
            "Junior Business Analyst — London"
        ),
        "https://abylon.io/career/senior-data-engineer/": "Senior Data Engineer",
    }
    assert batch.requests_made == 1
    assert len(batch.records) == 4
    assert {
        record["source_job_url"]: record["title"] for record in batch.records
    } == expected_titles
    assert all(record["source"] == "generic" for record in batch.records)
    assert all(
        record["source_job_id"] == candidate_id_for_url(str(record["source_job_url"]))
        for record in batch.records
    )


def test_generic_source_adapter_uses_safe_url_slug_title_fallback() -> None:
    job_url = "https://example.com/career/senior-data-engineer/"

    class DummyResponse:
        status = 200
        body = b"<html><body><a href='/career/senior-data-engineer/'>Apply now</a></body></html>"

    class DummySession:
        def __enter__(self) -> DummySession:
            return self

        def __exit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> Literal[False]:
            return False

        def get(self, url: str) -> DummyResponse:
            assert url == "https://example.com/careers"
            return DummyResponse()

    batch = GenericSourceAdapter(
        session_factory=lambda timeout_seconds: DummySession(),
    ).fetch(company=CompanyStub(source="generic", source_jobs_url="https://example.com/careers"))

    assert batch.records == (
        {
            "source": "generic",
            "source_job_id": candidate_id_for_url(job_url),
            "title": "Senior Data Engineer",
            "source_job_url": job_url,
        },
    )


def test_generic_source_adapter_rejects_candidate_without_safe_title() -> None:
    class DummyResponse:
        status = 200
        body = b"<html><body><a href='/jobs/12345'>Apply now</a></body></html>"

    class DummySession:
        def __enter__(self) -> DummySession:
            return self

        def __exit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> Literal[False]:
            return False

        def get(self, url: str) -> DummyResponse:
            assert url == "https://example.com/careers"
            return DummyResponse()

    adapter = GenericSourceAdapter(session_factory=lambda timeout_seconds: DummySession())

    with pytest.raises(SourceError, match="no validated jobs"):
        adapter.fetch(
            company=CompanyStub(source="generic", source_jobs_url="https://example.com/careers")
        )


def test_generic_source_adapter_fails_closed_on_http_error() -> None:
    class DummyResponse:
        status = 503
        body = b"<html><body>Service unavailable</body></html>"

    class DummySession:
        def __enter__(self) -> DummySession:
            return self

        def __exit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> Literal[False]:
            return False

        def get(self, url: str) -> DummyResponse:
            assert url == "https://example.com/careers"
            return DummyResponse()

    adapter = GenericSourceAdapter(session_factory=lambda timeout_seconds: DummySession())
    with pytest.raises(SourceError, match="HTTP 503"):
        adapter.fetch(company=CompanyStub(source="generic", source_jobs_url="https://example.com/careers"))


def test_generic_source_adapter_fails_closed_when_no_candidates_are_found() -> None:
    class DummyResponse:
        status = 200
        body = b"<html><body><a href='/privacy'>Privacy</a></body></html>"

    class DummySession:
        def __enter__(self) -> DummySession:
            return self

        def __exit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> Literal[False]:
            return False

        def get(self, url: str) -> DummyResponse:
            assert url == "https://example.com/careers"
            return DummyResponse()

    adapter = GenericSourceAdapter(session_factory=lambda timeout_seconds: DummySession())
    with pytest.raises(SourceError, match="no public job-like candidates"):
        adapter.fetch(company=CompanyStub(source="generic", source_jobs_url="https://example.com/careers"))


def test_generic_source_adapter_fails_closed_on_navigation_links_only() -> None:
    first_url = "https://example.com/careers"
    session = GenericPageSession(
        {
            first_url: (
                "<header><a href='/careers'>Visit careers page</a></header>"
                "<nav><a href='/alljobs?locale=en_US'>English (United States)</a></nav>"
                "<footer><a href='/viewalljobs/'>Sitemap</a>"
                "<a href='/jobs'>View all jobs</a></footer>"
            )
        }
    )

    with pytest.raises(SourceError, match="no public job-like candidates") as caught:
        GenericSourceAdapter(
            session_factory=lambda timeout_seconds: session,
        ).fetch(company=CompanyStub(source="generic", source_jobs_url=first_url))

    assert caught.value.requests_made == 1


def test_generic_source_adapter_fails_closed_when_pagination_exceeds_page_cap() -> None:
    class DummyResponse:
        status = 200
        body = (
            b"<html><body>"
            b"<a href='https://example.com/jobs/123'>Role</a>"
            b"<a rel='next' href='https://example.com/careers?page=2'>Next</a>"
            b"</body></html>"
        )

    class DummySession:
        def __enter__(self) -> DummySession:
            return self

        def __exit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> Literal[False]:
            return False

        def get(self, url: str) -> DummyResponse:
            assert url == "https://example.com/careers"
            return DummyResponse()

    adapter = GenericSourceAdapter(
        session_factory=lambda timeout_seconds: DummySession(),
        max_pages=1,
    )
    with pytest.raises(SourceError, match="page limit"):
        adapter.fetch(company=CompanyStub(source="generic", source_jobs_url="https://example.com/careers"))


def test_generic_source_adapter_respects_request_cap() -> None:
    first_url = "https://example.com/careers"
    second_url = "https://example.com/careers?page=2"
    session = GenericPageSession(
        {
            first_url: (
                "<a href='/jobs/first-role'>First Role</a>"
                f"<a rel='next' href='{second_url}'>Next</a>"
            ),
        }
    )

    with pytest.raises(SourceError, match="request limit") as caught:
        GenericSourceAdapter(
            session_factory=lambda timeout_seconds: session,
            max_pages=2,
            max_requests=1,
        ).fetch(company=CompanyStub(source="generic", source_jobs_url=first_url))

    assert caught.value.requests_made == 1
    assert session.calls == [first_url]


@pytest.mark.parametrize(
    ("next_href", "second_url"),
    [
        ("?page=2", "https://example.com/careers?page=2"),
        ("/careers/page/2/", "https://example.com/careers/page/2/"),
        ("?offset=50", "https://example.com/careers?offset=50"),
        ("?startrow=50", "https://example.com/careers?startrow=50"),
    ],
)
def test_generic_pagination_collects_supported_query_and_path_patterns(
    next_href: str,
    second_url: str,
) -> None:
    first_url = "https://example.com/careers"
    session = GenericPageSession(
        {
            first_url: (
                "<main><article class='job-card'><a href='/jobs/alpha-role'>"
                "Alpha Engineer</a></article></main>"
                f"<nav class='pagination'><a rel='next' href='{next_href}'>Next</a></nav>"
            ),
            second_url: (
                "<main><article class='job-card'><a href='/jobs/beta-role'>"
                "Beta Engineer</a></article></main>"
            ),
        }
    )

    batch = GenericSourceAdapter(
        session_factory=lambda timeout_seconds: session,
    ).fetch(company=CompanyStub(source="generic", source_jobs_url=first_url))

    assert session.calls == [first_url, second_url]
    assert batch.requests_made == 2
    assert {record["title"] for record in batch.records} == {
        "Alpha Engineer",
        "Beta Engineer",
    }


def test_generic_rel_next_pagination_deduplicates_overlapping_jobs() -> None:
    first_url = "https://example.com/jobs"
    second_url = "https://example.com/jobs?page=2"
    duplicate = "<article class='job-card'><a href='/jobs/shared-role'>Shared Role</a></article>"
    session = GenericPageSession(
        {
            first_url: duplicate + f"<a rel='next' href='{second_url}'>Next</a>",
            second_url: duplicate + (
                "<article class='job-card'><a href='/jobs/new-role'>New Role</a></article>"
            ),
        }
    )

    batch = GenericSourceAdapter(
        session_factory=lambda timeout_seconds: session,
    ).fetch(company=CompanyStub(source="generic", source_jobs_url=first_url))

    assert batch.requests_made == 2
    assert {record["source_job_url"] for record in batch.records} == {
        "https://example.com/jobs/shared-role",
        "https://example.com/jobs/new-role",
    }


def test_generic_numeric_listing_family_pagination_fetches_three_pages() -> None:
    first_url = "https://example.com/viewalljobs/"
    second_url = "https://example.com/viewalljobs/50/"
    third_url = "https://example.com/viewalljobs/100/"

    def page(job_href: str, job_title: str, current_url: str) -> str:
        links = "".join(
            (
                f"<a href='{url}' class='current-page' rel='nofollow'>{number}</a>"
                if url == current_url
                else f"<a href='{url}' rel='nofollow'>{number}</a>"
            )
            for number, url in enumerate((first_url, second_url, third_url), start=1)
        )
        return (
            f"<article class='job-row'><a href='{job_href}'>{job_title}</a></article>"
            f"<nav class='pagination'>{links}</nav>"
        )

    session = GenericPageSession(
        {
            first_url: page("/job/alpha-role", "Alpha Role", first_url),
            second_url: page("/job/beta-role", "Beta Role", second_url),
            third_url: page("/job/gamma-role", "Gamma Role", third_url),
        }
    )

    batch = GenericSourceAdapter(
        session_factory=lambda timeout_seconds: session,
    ).fetch(company=CompanyStub(source="generic", source_jobs_url=first_url))

    assert session.calls == [first_url, second_url, third_url]
    assert batch.requests_made == 3
    assert {record["title"] for record in batch.records} == {
        "Alpha Role",
        "Beta Role",
        "Gamma Role",
    }


def test_generic_uses_public_get_job_search_form_then_existing_pagination() -> None:
    landing_url = "https://example.com/viewalljobs/"
    search_url = "https://example.com/search/?locationsearch=&q="
    second_url = "https://example.com/search/?q=&startrow=50"
    session = GenericPageSession(
        {
            landing_url: (
                "<section id='category-list'>"
                "<a href='/browse/engineering-jobs/1001/'>Engineering Jobs</a>"
                "</section>"
                "<form method='get' action='/search/' role='search' "
                "class='job-search-form'>"
                "<input name='q'><input name='locationsearch'>"
                "</form>"
            ),
            search_url: (
                "<article class='job-row'>"
                "<a href='/job/alpha-role/9001/'>Alpha Role</a></article>"
                f"<nav class='pagination'><a href='{second_url}'>2</a></nav>"
            ),
            second_url: (
                "<article class='job-row'>"
                "<a href='/job/beta-role/9002/'>Beta Role</a></article>"
                "<nav class='language-selector'>"
                "<a href='?q=&amp;startrow=50&amp;locale=en_US'>English</a>"
                "</nav>"
            ),
        }
    )

    batch = GenericSourceAdapter(
        session_factory=lambda timeout_seconds: session,
    ).fetch(company=CompanyStub(source="generic", source_jobs_url=landing_url))

    assert session.calls == [landing_url, search_url, second_url]
    assert batch.requests_made == 3
    assert {record["title"] for record in batch.records} == {"Alpha Role", "Beta Role"}


def test_generic_pagination_loop_stops_on_seen_next_url() -> None:
    first_url = "https://example.com/careers"
    second_url = "https://example.com/careers?page=2"
    session = GenericPageSession(
        {
            first_url: (
                "<a href='/jobs/first-role'>First Role</a>"
                f"<a rel='next' href='{second_url}'>Next</a>"
            ),
            second_url: (
                "<a href='/jobs/second-role'>Second Role</a>"
                f"<a rel='next' href='{first_url}'>Next</a>"
            ),
        }
    )

    batch = GenericSourceAdapter(
        session_factory=lambda timeout_seconds: session,
    ).fetch(company=CompanyStub(source="generic", source_jobs_url=first_url))

    assert session.calls == [first_url, second_url]
    assert len(batch.records) == 2


def test_generic_pagination_rejects_external_next_url() -> None:
    first_url = "https://example.com/careers"
    session = GenericPageSession(
        {
            first_url: (
                "<a href='/jobs/first-role'>First Role</a>"
                "<a rel='next' href='https://elsewhere.example/jobs?page=2'>Next</a>"
            )
        }
    )

    with pytest.raises(SourceError, match="external or unrelated") as caught:
        GenericSourceAdapter(
            session_factory=lambda timeout_seconds: session,
        ).fetch(company=CompanyStub(source="generic", source_jobs_url=first_url))

    assert caught.value.requests_made == 1
    assert session.calls == [first_url]


def test_generic_detected_unsupported_pagination_fails_closed() -> None:
    first_url = "https://example.com/careers"
    session = GenericPageSession(
        {
            first_url: (
                "<a href='/jobs/first-role'>First Role</a>"
                "<button aria-label='Next page'>Next</button>"
            )
        }
    )

    with pytest.raises(SourceError, match="unsupported pagination"):
        GenericSourceAdapter(
            session_factory=lambda timeout_seconds: session,
        ).fetch(company=CompanyStub(source="generic", source_jobs_url=first_url))


def test_generic_unpaginated_listing_uses_one_request() -> None:
    first_url = "https://example.com/careers"
    session = GenericPageSession(
        {
            first_url: (
                "<main><a href='/careers/senior-platform-engineer'>"
                "Senior Platform Engineer</a></main>"
            )
        }
    )

    batch = GenericSourceAdapter(
        session_factory=lambda timeout_seconds: session,
    ).fetch(company=CompanyStub(source="generic", source_jobs_url=first_url))

    assert session.calls == [first_url]
    assert batch.requests_made == 1
    assert len(batch.records) == 1


def test_generic_live_like_paginated_listing_excludes_navigation_noise() -> None:
    first_url = "https://example.com/search/"
    second_url = "https://example.com/search/50/"
    first_jobs = "".join(
        f"<tr class='job-row'><td><a href='/job/role-{index}'>Role {index}</a></td></tr>"
        for index in range(50)
    )
    second_jobs = "".join(
        f"<tr class='job-row'><td><a href='/job/role-{index}'>Role {index}</a></td></tr>"
        for index in range(45, 60)
    )
    noise = (
        "<div class='language'><a href='/alljobs?locale=en_US'>English (United States)</a></div>"
        "<div class='footer'><a href='/viewalljobs/'>Sitemap</a>"
        "<a href='/careers'>Visit careers page</a></div>"
    )
    session = GenericPageSession(
        {
            first_url: first_jobs + noise + (
                f"<ul class='pagination'><li><a href='{second_url}'>2</a></li></ul>"
            ),
            second_url: second_jobs + noise,
        }
    )

    batch = GenericSourceAdapter(
        session_factory=lambda timeout_seconds: session,
    ).fetch(company=CompanyStub(source="generic", source_jobs_url=first_url))

    assert batch.requests_made == 2
    assert len(batch.records) == 60
    assert not {
        "Sitemap",
        "English (United States)",
        "Visit careers page",
    } & {str(record["title"]) for record in batch.records}


def test_generic_source_adapter_fails_closed_when_provider_returns_no_validated_jobs() -> None:
    class DummyResponse:
        status = 200
        body = (
            b"<html><body><a href='https://example.com/jobs/123'>"
            b"Senior Analyst</a></body></html>"
        )

    class DummySession:
        def __enter__(self) -> DummySession:
            return self

        def __exit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> Literal[False]:
            return False

        def get(self, url: str) -> DummyResponse:
            assert url == "https://example.com/careers"
            return DummyResponse()

    adapter = GenericSourceAdapter(
        provider=FakeJobExtractionProvider(mapping={}),
        session_factory=lambda timeout_seconds: DummySession(),
    )
    with pytest.raises(SourceError, match="no validated jobs"):
        adapter.fetch(company=CompanyStub(source="generic", source_jobs_url="https://example.com/careers"))


def test_registry_rejects_unknown_source_safely() -> None:
    with pytest.raises(UnknownSourceError, match="not-permitted") as caught:
        get_source_adapter(CompanyStub(source=" Not-Permitted "))

    assert caught.value.requests_made == 0


def test_fixture_adapter_returns_offline_source_batch(tmp_path: Path) -> None:
    fixture_path = tmp_path / "jobs.json"
    fixture_path.write_text(
        '[{"source": "fixture", "title": "Analyst"}]', encoding="utf-8"
    )

    batch = FixtureSourceAdapter(fixture_path).fetch(
        company=CompanyStub(
            source="fixture",
            source_jobs_url="https://unused.example.test/jobs",
        )
    )

    assert batch.records == ({"source": "fixture", "title": "Analyst"},)
    assert batch.requests_made == 0
